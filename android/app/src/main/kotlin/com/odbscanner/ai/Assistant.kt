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
            (s.provider == "custom" && s.effectiveBaseUrl.isNotBlank())
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
        val key = AiPrefs.key(context, s.provider)
        val sig = listOf(s.provider, s.effectiveModel, s.effectiveBaseUrl, s.effort,
            key.hashCode()).joinToString("|")
        val now = System.currentTimeMillis()
        val current = session
        if (current != null && sig == signature && turns < MAX_TURNS && now - lastUse < IDLE_MS) {
            return current
        }
        if (key.isBlank() && s.provider != "custom") {
            throw AiException("there is no API key for ${s.preset.label}; add one in Assistant settings")
        }
        val model = s.effectiveModel.ifBlank {
            throw AiException("choose a model for ${s.preset.label} in Assistant settings")
        }
        val fresh: ChatSession = if (s.preset.protocol == "anthropic") {
            ClaudeChatSession(key, model, s.effort, AssistantTools.SYSTEM_PROMPT,
                AssistantTools.SPECS, baseUrl = s.baseUrl)
        } else {
            val base = s.effectiveBaseUrl.ifBlank {
                throw AiException("set the service address in Assistant settings")
            }
            OpenAiChatSession(base, key, model, AssistantTools.SYSTEM_PROMPT, AssistantTools.SPECS)
        }
        session = fresh
        signature = sig
        turns = 0
        return fresh
    }
}
