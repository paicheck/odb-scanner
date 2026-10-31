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
    /** The conversation so far, as plain words, kept apart from any one service. */
    private val words = mutableListOf<Pair<String, String>>()
    private var conversationTrip = ""
    private var warnedDay = ""
    /** The model that answered last, for the interaction history. */
    @Volatile var lastModel = ""
        private set

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
    suspend fun answer(context: Context, text: String, driving: Boolean = false,
                       onText: ((String) -> Unit)? = null): Reply = lock.withLock {
        val app = context.applicationContext
        add(Line(true, text))
        _busy.value = true
        val reply = try {
            val s = sessionFor(app, driving || drivingNow())
            val said = withTimeout(TURN_TIMEOUT_MS) { s.ask(text, AssistantTools(app), onText) }
            lastModel = s.model
            turns++
            words += text to said
            (s as? FallbackChatSession)?.let {
                _active.value = it.activeLabel + if (it.onBackup) " (backup)" else ""
            }
            // Near a limit you set: said on the screen, once a day.
            val st = AiPrefs.load(app)
            UsageLedger.book.warning(st.limits, st.prices)?.let { w ->
                if (warnedDay != UsageLedger.book.today()) {
                    warnedDay = UsageLedger.book.today()
                    add(Line(false, "($w)"))
                }
            }
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
        val final = reply.error?.let { e -> local(app, text, e) } ?: reply
        add(Line(false, final.shown, final.error))
        // Shown, not spoken: which backup answered and why the others did not.
        (session as? FallbackChatSession)?.takeNote()?.let { add(Line(false, "($it)")) }
        final
    }

    private fun failed(e: AiError) = Reply(e.spoken, e.line, e)

    /**
     * When no AI service could answer: the phone's own answer to a battery,
     * range, next-stop, arrival, trip or fault question (LocalAnswers), from
     * the same tools, said as such. Null when the question is not one of
     * those, or the tools have nothing.
     */
    private suspend fun local(context: Context, text: String, why: AiError): Reply? {
        if (!LocalAnswers.covers(why)) return null
        val intent = LocalAnswers.intent(text) ?: return null
        val data = runCatching {
            org.json.JSONObject(AssistantTools(context).run(ToolCall("local", LocalAnswers.tool(intent),
                org.json.JSONObject())))
        }.getOrNull() ?: return null
        val said = LocalAnswers.say(intent, data) ?: return null
        _active.value = "the phone itself (no AI service)"
        lastModel = ""
        return Reply(said, "$said (answered from the phone's own data: no AI service could " +
            "be used -- ${why.category})")
    }

    /** Start the next question in a new conversation (New chat). */
    fun newConversation() {
        session = null
        words.clear()
        turns = 0
    }

    /** On a trip, or navigating: the driver is (probably) driving. */
    private fun drivingNow(): Boolean = com.odbscanner.route.Navigator.active ||
        com.odbscanner.route.TripMonitor.state.value != null

    private fun add(line: Line) {
        _transcript.value = (_transcript.value + line).takeLast(SHOWN)
    }

    /**
     * The session for the next question. A conversation is the words said so
     * far ([words]); it ends -- and the next question starts afresh -- after
     * IDLE_MS of quiet, MAX_TURNS questions, a new journey (another trip
     * id), New chat, or every question when memory is off. Within it, a new
     * session (settings or the answer style changed, or the last one failed)
     * is seeded with the words, so follow-up questions still know what came
     * before. Each service's own history stays append-only.
     */
    private fun sessionFor(context: Context, driving: Boolean): ChatSession {
        val s = AiPrefs.load(context)
        val chain = s.chain.ifEmpty {
            throw AiException(AiError(AiErrorCategory.NOT_CONFIGURED, "Assistant",
                "Every AI service is turned off: turn one on in AI & Voice Setup"))
        }
        val keys = chain.map { AiPrefs.key(context, it) }
        val style = effectiveStyle(s.style, driving)
        val trip = com.odbscanner.route.TripMonitor.state.value?.route?.tripId ?: ""
        val sig = listOf(s.provider, s.effectiveModel, s.effectiveBaseUrl, s.effort,
            chain.joinToString(","), keys.hashCode(), style).joinToString("|")
        val now = System.currentTimeMillis()
        val sameConversation = s.memory && turns < MAX_TURNS && now - lastUse < IDLE_MS &&
            trip == conversationTrip
        if (!sameConversation) {
            words.clear()
            turns = 0
            conversationTrip = trip
        }
        val current = session
        if (current != null && sig == signature && sameConversation) return current
        UsageLedger.attach(context)
        val fresh = FallbackChatSession(
            chain.mapIndexed { i, id -> AiPresets.byId(id).label to { build(s, id, keys[i], style) } },
            changesState = { name -> name in AssistantTools.CHANGES_STATE },
            ids = chain, health = ProviderHealth.breaker, history = words.toList(),
            limit = { id, label -> UsageLedger.book.check(id, label, s.limits, s.prices) })
        session = fresh
        signature = sig
        return fresh
    }

    /** brief / normal / detailed: "auto" is brief while driving, else normal. */
    fun effectiveStyle(style: String, driving: Boolean): String = when (style) {
        "brief", "normal", "detailed" -> style
        else -> if (driving) "brief" else "normal"
    }

    /**
     * A session with [provider]: the chosen service with its settings, or a
     * backup with its own defaults (its default model; Claude at low effort,
     * for a quick spoken answer).
     */
    private fun build(s: AiSettings, provider: String, key: String, style: String): ChatSession {
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
                AssistantTools.prompt(style), AssistantTools.SPECS,
                baseUrl = if (chosen) s.baseUrl else "", usageId = provider, usage = UsageLedger)
        } else {
            val base = (if (chosen) s.effectiveBaseUrl else preset.baseUrl.trimEnd('/')).ifBlank {
                throw missing("Set the service address")
            }
            OpenAiChatSession(base, key, model, AssistantTools.prompt(style), AssistantTools.SPECS,
                reasoningEffort = preset.reasoningEffort, provider = preset.label,
                usageId = provider, usage = UsageLedger, streaming = s.streaming)
        }
    }
}
