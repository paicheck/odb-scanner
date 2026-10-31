package com.odbscanner.ai

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.net.URL

/**
 * An assistant request failed. [error] says how, in one of the twelve
 * categories (AiErrors), safe to show; the message is its sentence.
 */
class AiException(message: String, cause: Throwable? = null, val error: AiError? = null,
                  val retryAfterMs: Long? = null) : Exception(message, cause) {
    constructor(error: AiError, cause: Throwable? = null, retryAfterMs: Long? = null) :
        this(error.message, cause, error, retryAfterMs)

    val category: AiErrorCategory get() = error?.category ?: AiErrorCategory.UNKNOWN_ERROR
}

/** A tool the AI may call: a name, what it does, its JSON-schema parameters. */
data class ToolSpec(val name: String, val description: String, val parameters: JSONObject)

/** One call the AI made: its id (to answer it), the tool, the arguments. */
data class ToolCall(val id: String, val name: String, val args: JSONObject)

/** Runs a tool call and returns its result as text (JSON). Never throws. */
fun interface ToolRunner {
    suspend fun run(call: ToolCall): String
}

/**
 * One conversation with the AI. [ask] sends the driver's words, runs the AI's
 * tool calls through [ToolRunner] until it answers, and returns the answer.
 * The history is append-only for the life of the session -- a new
 * conversation is a new session, never an edited one.
 */
interface ChatSession {
    val model: String
    suspend fun ask(text: String, tools: ToolRunner): String

    /**
     * As ask, handing the answer's words to [onText] as they arrive where
     * the service streams (OpenAiChatSession with streaming on). The default
     * does not stream: [onText] is never called and the answer comes whole.
     */
    suspend fun ask(text: String, tools: ToolRunner, onText: ((String) -> Unit)?): String =
        ask(text, tools)

    /**
     * Start this session with the conversation so far, as plain words
     * (question, answer) -- a backup taking over, or a session rebuilt in
     * the middle of a conversation, then knows what "there" means. Called
     * once, before the first ask.
     */
    fun seed(turns: List<Pair<String, String>>) {}
}

/** Where each request's token use goes (UsageLedger); null counts are "not reported". */
fun interface UsageSink {
    fun record(provider: String, inputTokens: Int?, outputTokens: Int?)
}

const val MAX_TOOL_ROUNDS = 6

/** A test question's answer: what was said, by which model, and the tokens it took. */
data class Probe(val text: String, val model: String, val inputTokens: Int?,
                 val outputTokens: Int?)

/**
 * OpenAI-compatible chat completions with function calling: OpenAI, Gemini,
 * Mistral, Groq, OpenRouter, DeepSeek, xAI, a custom endpoint or Ollama.
 * org.json over [transport] (HttpURLConnection by default), like the app's
 * other services. No sampling parameters or output cap are sent: several
 * newer models reject them.
 *
 * A turn that fails or is cancelled is taken out of the history again, so
 * the conversation stays one the service accepts and nothing of that turn
 * comes back. Cancelling the caller aborts the request in flight.
 */
class OpenAiChatSession(
    private val baseUrl: String,
    private val key: String,
    override val model: String,
    system: String,
    private val tools: List<ToolSpec>,
    // Long enough for a thinking model given the tools; a spoken question
    // waits, but a failed one helps nobody.
    private val timeoutMs: Int = 90_000,
    /** Sent as reasoning_effort when set (AiPreset.reasoningEffort); "" sends none. */
    private val reasoningEffort: String = "",
    /** Who this is, in errors ("Groq"); the host when not given. */
    private val provider: String = "",
    private val transport: HttpTransport = UrlTransport,
    /** The preset id usage is counted under; "" counts nothing. */
    private val usageId: String = "",
    private val usage: UsageSink? = null,
    /** Ask for the answer as server-sent events, so it can be spoken as it comes. */
    private val streaming: Boolean = false,
) : ChatSession {
    private var streamOk = streaming
    private val messages = JSONArray().put(JSONObject().put("role", "system").put("content", system))
    private val host = runCatching { URL(baseUrl).host }.getOrDefault(baseUrl)
    private val label = provider.ifBlank { host }

    override suspend fun ask(text: String, tools: ToolRunner): String = ask(text, tools, null)

    override suspend fun ask(text: String, tools: ToolRunner, onText: ((String) -> Unit)?): String =
        withContext(Dispatchers.IO) {
        val mark = messages.length()
        try {
            messages.put(JSONObject().put("role", "user").put("content", text))
            repeat(MAX_TOOL_ROUNDS) {
                val reply = request(onText)
                val msg = reply.optJSONArray("choices")?.optJSONObject(0)?.optJSONObject("message")
                    ?: throw invalid("Unexpected reply from $label")
                messages.put(echo(msg))
                val calls = parseToolCalls(msg)
                if (calls.isEmpty()) {
                    return@withContext contentText(msg).ifBlank { throw invalid("$label gave no answer") }
                }
                for (call in calls) {
                    messages.put(JSONObject().put("role", "tool").put("tool_call_id", call.id)
                        .put("content", tools.run(call)))
                }
            }
            throw invalid("$label kept calling tools without answering")
        } catch (t: Throwable) {
            while (messages.length() > mark) messages.remove(messages.length() - 1)
            throw t
        }
    }

    override fun seed(turns: List<Pair<String, String>>) {
        turns.forEach { (q, a) ->
            messages.put(JSONObject().put("role", "user").put("content", q))
            messages.put(JSONObject().put("role", "assistant").put("content", a))
        }
    }

    private fun invalid(message: String) =
        AiException(AiError(AiErrorCategory.INVALID_RESPONSE, label, message))

    private suspend fun request(onText: ((String) -> Unit)?): JSONObject {
        val body = JSONObject().put("model", model).put("messages", messages)
        if (reasoningEffort.isNotBlank()) body.put("reasoning_effort", reasoningEffort)
        if (tools.isNotEmpty()) {
            body.put("tools", JSONArray().apply {
                this@OpenAiChatSession.tools.forEach { t ->
                    put(JSONObject().put("type", "function").put("function", JSONObject()
                        .put("name", t.name).put("description", t.description)
                        .put("parameters", t.parameters)))
                }
            })
        }
        if (streamOk && onText != null) {
            try {
                return streamed(body.put("stream", true), onText)
            } catch (e: AiException) {
                // A service that will not stream is asked normally, from now on.
                val err = e.error
                if (err?.status != 400 && err?.status != 422) throw e
                if ("stream" !in (err.detail + err.message).lowercase()) throw e
                streamOk = false
                body.remove("stream")
            }
        }
        val text = post("$baseUrl/chat/completions", body.toString(), timeoutMs)
        return try {
            JSONObject(text).also { o ->
                if (usageId.isNotBlank()) o.optJSONObject("usage").let { u ->
                    usage?.record(usageId, u?.optInt("prompt_tokens")?.takeIf { u.has("prompt_tokens") },
                        u?.optInt("completion_tokens")?.takeIf { u.has("completion_tokens") })
                }
            }
        } catch (e: JSONException) {
            throw AiException(AiErrors.fromException(label, host, e), e)
        }
    }

    /** The reply as events: the words to [onText] as they come; the same shape as unstreamed. */
    private suspend fun streamed(body: JSONObject, onText: (String) -> Unit): JSONObject {
        val acc = SseAccumulator(onText)
        val req = HttpRequest("POST", "$baseUrl/chat/completions", headers(key) + mapOf(
            "Content-Type" to "application/json; charset=utf-8", "Accept" to "text/event-stream"),
            body.toString(), readTimeoutMs = timeoutMs)
        var attempt = 1
        while (true) {
            try {
                val reply = try {
                    transport.stream(req) { acc.line(it) }
                } catch (e: HttpFailure) {
                    throw AiException(AiErrors.fromException(label, host, e.cause ?: e, e.sent,
                        (req.readTimeoutMs + 999) / 1000), e)
                }
                if (reply.status >= 400) {
                    throw AiException(AiErrors.fromHttp(label, reply.status, reply.body, model,
                        listOf(key)), retryAfterMs = RetryPolicy.retryAfterMs(reply.headers))
                }
                acc.error?.let { e ->
                    throw AiException(AiErrors.fromHttp(label, e.optInt("code", 500).takeIf { it >= 400 }
                        ?: 500, JSONObject().put("error", e).toString(), model, listOf(key)))
                }
                break
            } catch (e: AiException) {
                // Words already spoken are never asked for again.
                val wait = if (acc.started) null
                else RetryPolicy.delayFor(e.error, attempt, e.retryAfterMs)
                wait ?: throw e
                delay(wait)
                attempt++
            }
        }
        if (usageId.isNotBlank()) {
            val u = acc.usage
            usage?.record(usageId, u?.optInt("prompt_tokens")?.takeIf { u.has("prompt_tokens") },
                u?.optInt("completion_tokens")?.takeIf { u.has("completion_tokens") })
        }
        return JSONObject().put("choices", JSONArray().put(JSONObject().put("message", acc.message())))
    }

    private suspend fun post(url: String, body: String, readTimeoutMs: Int): String =
        call(transport, label, host, key, model, HttpRequest("POST", url, headers(key) + mapOf(
            "Content-Type" to "application/json; charset=utf-8"), body,
            readTimeoutMs = readTimeoutMs))

    companion object {
        fun headers(key: String): Map<String, String> =
            mapOf("Accept" to "application/json") +
                (if (key.isNotBlank()) mapOf("Authorization" to "Bearer $key") else emptyMap())

        /**
         * One request through [transport]: the body of a 2xx reply, else an
         * AiException in its category, the key taken out of any message.
         * Tried again only as RetryPolicy allows (a request that cannot have
         * been processed, once, after the wait the service asks for).
         */
        suspend fun call(transport: HttpTransport, label: String, host: String, key: String,
                         model: String, req: HttpRequest): String {
            var attempt = 1
            while (true) {
                try {
                    return once(transport, label, host, key, model, req)
                } catch (e: AiException) {
                    val wait = RetryPolicy.delayFor(e.error, attempt, e.retryAfterMs) ?: throw e
                    delay(wait)                 // cancellable: a stopped turn is never retried
                    attempt++
                }
            }
        }

        private suspend fun once(transport: HttpTransport, label: String, host: String,
                                 key: String, model: String, req: HttpRequest): String {
            val reply = try {
                transport.send(req)
            } catch (e: HttpFailure) {
                throw AiException(AiErrors.fromException(label, host, e.cause ?: e, e.sent,
                    (req.readTimeoutMs + 999) / 1000), e)
            } catch (e: IOException) {
                throw AiException(AiErrors.fromException(label, host, e), e)
            }
            if (reply.status >= 400) {
                throw AiException(AiErrors.fromHttp(label, reply.status, reply.body, model,
                    listOf(key)), retryAfterMs = RetryPolicy.retryAfterMs(reply.headers))
            }
            return reply.body
        }

        /** The assistant message as it goes back into the history. */
        fun echo(msg: JSONObject): JSONObject {
            val out = JSONObject().put("role", "assistant")
            out.put("content", if (msg.isNull("content")) JSONObject.NULL else msg.opt("content"))
            msg.optJSONArray("tool_calls")?.let { out.put("tool_calls", it) }
            return out
        }

        fun parseToolCalls(msg: JSONObject): List<ToolCall> {
            val arr = msg.optJSONArray("tool_calls") ?: return emptyList()
            return (0 until arr.length()).mapNotNull { i ->
                val c = arr.optJSONObject(i) ?: return@mapNotNull null
                val f = c.optJSONObject("function") ?: return@mapNotNull null
                val args = runCatching { JSONObject(f.optString("arguments", "{}").ifBlank { "{}" }) }
                    .getOrDefault(JSONObject())
                ToolCall(c.optString("id"), f.optString("name"), args)
            }
        }

        /** Text content, whether a string or a list of {type: text} parts. */
        fun contentText(msg: JSONObject): String {
            if (msg.isNull("content")) return ""
            val c = msg.opt("content")
            if (c is JSONArray) {
                return (0 until c.length()).mapNotNull { c.optJSONObject(it)?.optString("text") }
                    .joinToString("").trim()
            }
            return c?.toString()?.trim() ?: ""
        }

        /**
         * Model ids the service offers (GET /models): for the settings picker,
         * and the setup screen's key test (a refused key fails here).
         */
        suspend fun listModels(baseUrl: String, key: String, label: String = "",
                               transport: HttpTransport = UrlTransport): List<String> {
            val url = "${baseUrl.trimEnd('/')}/models"
            val host = runCatching { URL(url).host }.getOrDefault(url)
            val text = call(transport, label.ifBlank { host }, host, key, "",
                HttpRequest("GET", url, headers(key), readTimeoutMs = 20_000))
            val data = try {
                JSONObject(text).optJSONArray("data") ?: return emptyList()
            } catch (e: JSONException) {
                throw AiException(AiErrors.fromException(label.ifBlank { host }, host, e), e)
            }
            return (0 until data.length()).mapNotNull { data.optJSONObject(it)?.optString("id") }
                .map { it.removePrefix("models/") }.filter { it.isNotBlank() }.sorted()
        }

        /**
         * One short question with no tools and no history, for the setup
         * screen's AI test: the answer, the model that gave it and the
         * tokens, where the reply says. Synthetic words only.
         */
        suspend fun probe(baseUrl: String, key: String, model: String, label: String,
                          prompt: String, reasoningEffort: String = "",
                          transport: HttpTransport = UrlTransport,
                          readTimeoutMs: Int = 30_000): Probe {
            val url = "${baseUrl.trimEnd('/')}/chat/completions"
            val host = runCatching { URL(url).host }.getOrDefault(url)
            val body = JSONObject().put("model", model).put("messages", JSONArray()
                .put(JSONObject().put("role", "user").put("content", prompt)))
            if (reasoningEffort.isNotBlank()) body.put("reasoning_effort", reasoningEffort)
            val text = call(transport, label, host, key, model, HttpRequest("POST", url,
                headers(key) + mapOf("Content-Type" to "application/json; charset=utf-8"),
                body.toString(), readTimeoutMs = readTimeoutMs))
            return try {
                val o = JSONObject(text)
                val msg = o.optJSONArray("choices")?.optJSONObject(0)?.optJSONObject("message")
                    ?: throw JSONException("no choices")
                val usage = o.optJSONObject("usage")
                Probe(contentText(msg), o.optString("model").ifBlank { model },
                    usage?.optInt("prompt_tokens")?.takeIf { usage.has("prompt_tokens") },
                    usage?.optInt("completion_tokens")?.takeIf { usage.has("completion_tokens") })
            } catch (e: JSONException) {
                throw AiException(AiErrors.fromException(label, host, e), e)
            }
        }
    }
}

/**
 * Server-sent events of a chat completion, put back together: the words
 * handed on as they arrive, tool calls assembled from their pieces (by
 * index; names and arguments may come in parts), an error event or usage
 * kept. [message] is the reply in the shape an unstreamed one has. Pure.
 */
class SseAccumulator(private val onText: (String) -> Unit) {
    private val text = StringBuilder()
    private val calls = sortedMapOf<Int, JSONObject>()
    var error: JSONObject? = null
        private set
    var usage: JSONObject? = null
        private set
    var done = false
        private set
    /** Some words or a call have come: the request must not be re-sent. */
    val started: Boolean get() = text.isNotEmpty() || calls.isNotEmpty()

    fun line(raw: String) {
        val l = raw.trim()
        if (!l.startsWith("data:")) return
        val data = l.removePrefix("data:").trim()
        if (data == "[DONE]") {
            done = true
            return
        }
        val o = runCatching { JSONObject(data) }.getOrNull() ?: return
        o.optJSONObject("error")?.let { error = it; return }
        o.optJSONObject("usage")?.let { usage = it }
        val delta = o.optJSONArray("choices")?.optJSONObject(0)?.optJSONObject("delta") ?: return
        val c = delta.opt("content")
        if (c is String && c.isNotEmpty()) {
            text.append(c)
            onText(c)
        }
        val arr = delta.optJSONArray("tool_calls") ?: return
        for (k in 0 until arr.length()) {
            val d = arr.optJSONObject(k) ?: continue
            val acc = calls.getOrPut(d.optInt("index", k)) {
                JSONObject().put("type", "function")
                    .put("function", JSONObject().put("name", "").put("arguments", ""))
            }
            d.optString("id").takeIf { it.isNotBlank() }?.let { acc.put("id", it) }
            val f = d.optJSONObject("function") ?: continue
            val af = acc.getJSONObject("function")
            f.optString("name").takeIf { it.isNotEmpty() }?.let { af.put("name", af.getString("name") + it) }
            f.optString("arguments").takeIf { it.isNotEmpty() }
                ?.let { af.put("arguments", af.getString("arguments") + it) }
        }
    }

    fun message(): JSONObject = JSONObject().put("role", "assistant")
        .put("content", if (text.isEmpty()) JSONObject.NULL else text.toString())
        .apply { if (calls.isNotEmpty()) put("tool_calls", JSONArray(calls.values.toList())) }
}

/**
 * Streamed words into sentences fit to speak one at a time: a sentence is
 * handed on when it ends (. ! ? or a line break, not a decimal point), and
 * a short one waits for the next so speech does not stutter. [flush] gives
 * what is left at the end. Pure.
 */
class SentenceChunker(private val minChars: Int = 24) {
    private val buf = StringBuilder()

    fun feed(delta: String): List<String> {
        buf.append(delta)
        val out = mutableListOf<String>()
        var start = 0
        for (i in buf.indices) {
            if (!endsAt(i)) continue
            val piece = buf.substring(start, i + 1).trim()
            // A short one ("Sure.") waits to go with the next.
            if (piece.length >= minChars) {
                out += piece
                start = i + 1
            }
        }
        buf.delete(0, start)
        return out
    }

    fun flush(): String? = buf.toString().trim().ifEmpty { null }.also { buf.clear() }

    /** A sentence ends at i: a line break, or . ! ? before a space ("3.5" is not one). */
    private fun endsAt(i: Int): Boolean {
        val ch = buf[i]
        if (ch == '\n') return true
        if (ch != '.' && ch != '!' && ch != '?') return false
        val next = buf.getOrNull(i + 1) ?: return false      // not yet known: wait
        return next.isWhitespace()
    }
}
