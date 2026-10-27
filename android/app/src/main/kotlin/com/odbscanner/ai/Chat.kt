package com.odbscanner.ai

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** An assistant request failed; the message says why, fit to show or speak. */
class AiException(message: String, cause: Throwable? = null) : Exception(message, cause)

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
}

const val MAX_TOOL_ROUNDS = 6

/**
 * OpenAI-compatible chat completions with function calling: OpenAI, Gemini,
 * Mistral, Groq, OpenRouter, DeepSeek, xAI, a custom endpoint or Ollama.
 * HttpURLConnection + org.json, like the app's other services. No sampling
 * parameters or output cap are sent: several newer models reject them.
 */
class OpenAiChatSession(
    private val baseUrl: String,
    private val key: String,
    override val model: String,
    system: String,
    private val tools: List<ToolSpec>,
    private val timeoutMs: Int = 60_000,
) : ChatSession {
    private val messages = JSONArray().put(JSONObject().put("role", "system").put("content", system))

    override suspend fun ask(text: String, tools: ToolRunner): String = withContext(Dispatchers.IO) {
        messages.put(JSONObject().put("role", "user").put("content", text))
        repeat(MAX_TOOL_ROUNDS) {
            val reply = request()
            val msg = reply.optJSONArray("choices")?.optJSONObject(0)?.optJSONObject("message")
                ?: throw AiException("Unexpected reply from the AI service")
            messages.put(echo(msg))
            val calls = parseToolCalls(msg)
            if (calls.isEmpty()) {
                return@withContext contentText(msg).ifBlank { throw AiException("The AI gave no answer") }
            }
            for (call in calls) {
                messages.put(JSONObject().put("role", "tool").put("tool_call_id", call.id)
                    .put("content", tools.run(call)))
            }
        }
        throw AiException("The AI kept calling tools without answering")
    }

    private fun request(): JSONObject {
        val body = JSONObject().put("model", model).put("messages", messages)
        if (tools.isNotEmpty()) {
            body.put("tools", JSONArray().apply {
                this@OpenAiChatSession.tools.forEach { t ->
                    put(JSONObject().put("type", "function").put("function", JSONObject()
                        .put("name", t.name).put("description", t.description)
                        .put("parameters", t.parameters)))
                }
            })
        }
        return JSONObject(post("$baseUrl/chat/completions", body.toString()))
    }

    private fun post(url: String, body: String): String {
        val host = runCatching { URL(url).host }.getOrDefault(url)
        val conn = URL(url).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 15_000
            conn.readTimeout = timeoutMs
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            conn.setRequestProperty("Accept", "application/json")
            if (key.isNotBlank()) conn.setRequestProperty("Authorization", "Bearer $key")
            conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            val text = (if (code < 400) conn.inputStream else conn.errorStream)
                ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            when {
                code == 401 || code == 403 -> throw AiException("$host refused the API key (HTTP $code)")
                code == 404 -> throw AiException("$host: model '$model' not found (HTTP 404)")
                code == 429 -> throw AiException("$host rate limit reached; try again shortly")
                code >= 400 -> throw AiException("$host returned HTTP $code: ${text.take(200)}")
            }
            return text
        } catch (e: IOException) {
            throw AiException("Could not reach $host: ${e.message}", e)
        } finally {
            conn.disconnect()
        }
    }

    companion object {
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

        /** Model ids the service offers (GET /models), for the settings picker. */
        fun listModels(baseUrl: String, key: String): List<String> {
            val conn = URL("${baseUrl.trimEnd('/')}/models").openConnection() as HttpURLConnection
            try {
                conn.connectTimeout = 15_000
                conn.readTimeout = 20_000
                if (key.isNotBlank()) conn.setRequestProperty("Authorization", "Bearer $key")
                val code = conn.responseCode
                if (code >= 400) throw AiException("Listing models failed (HTTP $code)")
                val data = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                    .optJSONArray("data") ?: return emptyList()
                return (0 until data.length()).mapNotNull { data.optJSONObject(it)?.optString("id") }
                    .filter { it.isNotBlank() }.sorted()
            } catch (e: IOException) {
                throw AiException("Could not reach the service: ${e.message}", e)
            } finally {
                conn.disconnect()
            }
        }
    }
}
