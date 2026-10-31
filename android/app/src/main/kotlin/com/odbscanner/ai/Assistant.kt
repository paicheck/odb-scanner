package com.odbscanner.ai

import android.content.Context
import android.util.Log
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeout

/** One line of the conversation as shown on the phone and car screens. */
data class Line(val fromDriver: Boolean, val text: String, val error: AiError? = null)

/** An answer: what to say, what to show, and the failure if it was one. */
data class Reply(val spoken: String, val shown: String, val error: AiError? = null)

/**
 * The assistant: the chosen AI service, the app's tools, and the current
 * conversation. A conversation is one append-only session; it is replaced by
 * a fresh one after IDLE_MS of quiet, after MAX_TURNS questions, or when the
 * settings change -- never trimmed, which would edit its history.
 */
object Assistant {
    private const val TAG = "Assistant"
    private const val IDLE_MS = 10 * 60_000L
    private const val MAX_TURNS = 16
    private const val SHOWN = 30
    /** A whole question, tools and backups included: past this it gives up. */
    const val TURN_TIMEOUT_MS = 120_000L

    private val lock = Mutex()
    private var session: ChatSession? = null
    private var signature = ""
    private var turns = 0
    private var lastUse = 0L

    private val _transcript = MutableStateFlow<List<Line>>(emptyList())
    val transcript: StateFlow<List<Line>> = _transcript
    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy
    /** Who answered last: "Groq (backup)" -- a switch is shown, never silent. */
    private val _active = MutableStateFlow<String?>(null)
    val active: StateFlow<String?> = _active

    /** Whether the assistant has what it needs to answer (a key, or a custom endpoint). */
    fun ready(context: Context): Boolean {
        val s = AiPrefs.load(context)
        return s.chain.any { id ->
            AiPrefs.key(context, id).isNotBlank() ||
                (id == "custom" && id == s.provider && s.effectiveBaseUrl.isNotBlank())
        }
    }

    /** Ask, and get the answer to say (a failure is a short sentence to say, too). */
    suspend fun ask(context: Context, text: String): String = answer(context, text).spoken

    /**
     * Ask, within TURN_TIMEOUT_MS. A failure comes back as a Reply with its
     * AiError: a few words to say, the detail to show. Cancelling the caller
     * stops it at once -- the request is aborted, nothing of the turn stays
     * in the conversation, and CancellationException goes on up (it is never
     * turned into an answer).
     */
    suspend fun answer(context: Context, text: String): Reply = lock.withLock {
        val app = context.applicationContext
        add(Line(true, text))
        _busy.value = true
        val reply = try {
            val s = sessionFor(app)
            val said = withTimeout(TURN_TIMEOUT_MS) { s.ask(text, AssistantTools(app)) }
            turns++
            (s as? FallbackChatSession)?.let {
                _active.value = it.activeLabel + if (it.onBackup) " (backup)" else ""
            } ?: run { _active.value = AiPrefs.load(app).preset.label }
            Reply(said, said)
        } catch (e: TimeoutCancellationException) {
            session = null
            failed(AiError(AiErrorCategory.TIMEOUT, AiPrefs.load(app).preset.label,
                "No answer within ${TURN_TIMEOUT_MS / 1000} s, so the assistant gave up"))
        } catch (e: CancellationException) {
            add(Line(false, "(stopped)"))
            throw e
        } catch (e: AiException) {
            Log.w(TAG, "ask failed: ${e.category} ${e.message}")
            session = null                      // a failed turn is not replayed
            failed(e.error ?: AiError(e.category, "Assistant", e.message ?: "failed"))
        } catch (e: Exception) {
            Log.w(TAG, "ask failed: ${e.javaClass.simpleName}")
            session = null
            failed(AiError(AiErrorCategory.UNKNOWN_ERROR, "Assistant",
                "Something went wrong (${e.javaClass.simpleName})"))
        } finally {
            _busy.value = false
        }
        lastUse = System.currentTimeMillis()
        add(Line(false, reply.shown, reply.error))
        // Shown, not spoken: which backup answered and why the others did not.
        (session as? FallbackChatSession)?.takeNote()?.let { add(Line(false, "($it)")) }
        reply
    }

    private fun failed(e: AiError) = Reply(e.spoken, e.line, e)

    /** Start the next question in a new conversation. */
    fun newConversation() {
        session = null
    }

    private fun add(line: Line) {
        _transcript.value = (_transcript.value + line).takeLast(SHOWN)
    }

    private fun sessionFor(context: Context): ChatSession {
        val s = AiPrefs.load(context)
        val chain = s.chain.ifEmpty {
            throw AiException(AiError(AiErrorCategory.NOT_CONFIGURED, "Assistant",
                "Every AI service is turned off: turn one on in AI & Voice Setup"))
        }
        val keys = chain.map { AiPrefs.key(context, it) }
        val sig = listOf(s.provider, s.effectiveModel, s.effectiveBaseUrl, s.effort,
            chain.joinToString(","), keys.hashCode()).joinToString("|")
        val now = System.currentTimeMillis()
        val current = session
        if (current != null && sig == signature && turns < MAX_TURNS && now - lastUse < IDLE_MS) {
            return current
        }
        val fresh: ChatSession = if (chain.size == 1) {
            build(s, chain[0], keys[0])
        } else {
            FallbackChatSession(chain.mapIndexed { i, id ->
                AiPresets.byId(id).label to { build(s, id, keys[i]) }
            }) { name -> name in AssistantTools.CHANGES_STATE }
        }
        session = fresh
        signature = sig
        turns = 0
        return fresh
    }

    /**
     * A session with [provider]: the chosen service with its settings, or a
     * backup with its own defaults (its default model; Claude at low effort,
     * for a quick spoken answer).
     */
    private fun build(s: AiSettings, provider: String, key: String): ChatSession {
        val chosen = provider == s.provider
        val preset = AiPresets.byId(provider)
        fun missing(what: String) = AiException(AiError(AiErrorCategory.NOT_CONFIGURED,
            preset.label, "$what for ${preset.label} in Assistant settings"))
        if (key.isBlank() && provider != "custom") throw missing("Add an API key")
        val model = (if (chosen) s.effectiveModel else preset.defaultModel).ifBlank {
            throw missing("Choose a model")
        }
        return if (preset.protocol == "anthropic") {
            ClaudeChatSession(key, model, if (chosen) s.effort else "low",
                AssistantTools.SYSTEM_PROMPT, AssistantTools.SPECS,
                baseUrl = if (chosen) s.baseUrl else "")
        } else {
            val base = (if (chosen) s.effectiveBaseUrl else preset.baseUrl.trimEnd('/')).ifBlank {
                throw missing("Set the service address")
            }
            OpenAiChatSession(base, key, model, AssistantTools.SYSTEM_PROMPT, AssistantTools.SPECS,
                reasoningEffort = preset.reasoningEffort, provider = preset.label)
        }
    }
}
