package com.odbscanner.ai

import android.content.Context
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** One line of the conversation as shown on the phone and car screens. */
data class Line(val fromDriver: Boolean, val text: String)

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

    private val lock = Mutex()
    private var session: ChatSession? = null
    private var signature = ""
    private var turns = 0
    private var lastUse = 0L

    private val _transcript = MutableStateFlow<List<Line>>(emptyList())
    val transcript: StateFlow<List<Line>> = _transcript
    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy

    /** Whether the assistant has what it needs to answer (a key, or a custom endpoint). */
    fun ready(context: Context): Boolean {
        val s = AiPrefs.load(context)
        return AiPrefs.key(context, s.provider).isNotBlank() ||
            (s.provider == "custom" && s.effectiveBaseUrl.isNotBlank()) ||
            s.backups.any { AiPrefs.key(context, it).isNotBlank() }
    }

    /** Ask, and get the answer to say. Failures come back as a sentence to say, too. */
    suspend fun ask(context: Context, text: String): String = lock.withLock {
        val app = context.applicationContext
        add(Line(true, text))
        _busy.value = true
        val answer = try {
            sessionFor(app).ask(text, AssistantTools(app)).also { turns++ }
        } catch (e: AiException) {
            Log.w(TAG, "ask failed", e)
            session = null                      // a failed turn is not replayed
            "Sorry, ${e.message?.replaceFirstChar { it.lowercase() } ?: "that failed"}."
        } catch (e: Exception) {
            Log.w(TAG, "ask failed", e)
            session = null
            "Sorry, something went wrong (${e.javaClass.simpleName})."
        } finally {
            _busy.value = false
        }
        lastUse = System.currentTimeMillis()
        add(Line(false, answer))
        // Shown, not spoken: which backup answered and why the others did not.
        (session as? FallbackChatSession)?.takeNote()?.let { add(Line(false, "($it)")) }
        answer
    }

    /** Start the next question in a new conversation. */
    fun newConversation() {
        session = null
    }

    private fun add(line: Line) {
        _transcript.value = (_transcript.value + line).takeLast(SHOWN)
    }

    private fun sessionFor(context: Context): ChatSession {
        val s = AiPrefs.load(context)
        val chain = listOf(s.provider) + s.backups
        val keys = chain.map { AiPrefs.key(context, it) }
        val sig = listOf(s.provider, s.effectiveModel, s.effectiveBaseUrl, s.effort,
            chain.joinToString(","), keys.hashCode()).joinToString("|")
        val now = System.currentTimeMillis()
        val current = session
        if (current != null && sig == signature && turns < MAX_TURNS && now - lastUse < IDLE_MS) {
            return current
        }
        val fresh: ChatSession = if (chain.size == 1) {
            build(s, s.provider, keys[0])
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
        if (key.isBlank() && provider != "custom") {
            throw AiException("there is no API key for ${preset.label}; add one in Assistant settings")
        }
        val model = (if (chosen) s.effectiveModel else preset.defaultModel).ifBlank {
            throw AiException("choose a model for ${preset.label} in Assistant settings")
        }
        return if (preset.protocol == "anthropic") {
            ClaudeChatSession(key, model, if (chosen) s.effort else "low",
                AssistantTools.SYSTEM_PROMPT, AssistantTools.SPECS,
                baseUrl = if (chosen) s.baseUrl else "")
        } else {
            val base = (if (chosen) s.effectiveBaseUrl else preset.baseUrl.trimEnd('/')).ifBlank {
                throw AiException("set the service address in Assistant settings")
            }
            OpenAiChatSession(base, key, model, AssistantTools.SYSTEM_PROMPT, AssistantTools.SPECS,
                reasoningEffort = preset.reasoningEffort)
        }
    }
}
