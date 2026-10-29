package com.odbscanner.ai

import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket
import kotlin.concurrent.thread

/**
 * Both chat sessions against a local stand-in for the AI service: the real
 * request code (Anthropic's SDK for Claude, HttpURLConnection for the
 * OpenAI-compatible ones) talking HTTP to a server that answers with a tool
 * call and then a text reply. Checks the request shape, the tool loop and
 * that the history is append-only. No network, no key.
 */
class ChatTest {
    // A minimal HTTP/1.1 server on a plain socket (android.jar has no
    // com.sun.net.httpserver): one request per connection, JSON replies in order.
    private lateinit var server: ServerSocket
    private val requests = mutableListOf<Pair<Map<String, List<String>>, JSONObject>>()
    private val replies = ArrayDeque<String>()

    @Before
    fun start() {
        server = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
        thread(isDaemon = true) {
            while (!server.isClosed) {
                val sock = runCatching { server.accept() }.getOrNull() ?: break
                sock.use { s ->
                    val input = s.getInputStream()
                    val headers = mutableMapOf<String, MutableList<String>>()
                    fun line(): String {
                        val sb = StringBuilder()
                        while (true) {
                            val c = input.read()
                            if (c < 0 || c == '\n'.code) break
                            if (c != '\r'.code) sb.append(c.toChar())
                        }
                        return sb.toString()
                    }
                    line()                                       // request line
                    while (true) {
                        val h = line()
                        if (h.isEmpty()) break
                        val i = h.indexOf(':')
                        if (i > 0) {
                            headers.getOrPut(h.substring(0, i).trim().lowercase()) { mutableListOf() }
                                .add(h.substring(i + 1).trim())
                        }
                    }
                    val len = headers["content-length"]?.firstOrNull()?.toIntOrNull() ?: 0
                    val buf = ByteArray(len)
                    var got = 0
                    while (got < len) {
                        val n = input.read(buf, got, len - got)
                        if (n < 0) break
                        got += n
                    }
                    val body = String(buf, 0, got, Charsets.UTF_8)
                    synchronized(requests) {
                        requests.add(headers to (if (body.isBlank()) JSONObject() else JSONObject(body)))
                    }
                    val out = (synchronized(replies) { replies.removeFirstOrNull() } ?: "{}")
                        .toByteArray(Charsets.UTF_8)
                    s.getOutputStream().apply {
                        write(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n" +
                            "Content-Length: ${out.size}\r\nConnection: close\r\n\r\n").toByteArray())
                        write(out)
                        flush()
                    }
                }
            }
        }
    }

    @After
    fun stop() = server.close()

    private val base get() = "http://127.0.0.1:${server.localPort}"

    private val battery = ToolSpec("get_battery", "Battery readings",
        JSONObject().put("type", "object").put("properties", JSONObject()).put("required", JSONArray()))
    private val directions = ToolSpec("get_directions", "Steps ahead", JSONObject()
        .put("type", "object")
        .put("properties", JSONObject().put("count", JSONObject().put("type", "integer")))
        .put("required", JSONArray()))
    private val ran = mutableListOf<ToolCall>()
    private val runner = ToolRunner { call -> ran.add(call); """{"charge_pct": 80}""" }

    @Test
    fun claudeRequestShapeAndAppendOnlyToolLoop() = runBlocking {
        replies.add("""{"id":"msg_1","type":"message","role":"assistant","model":"claude-opus-5-5",
            "content":[{"type":"thinking","thinking":"","signature":"sig-1"},
                       {"type":"tool_use","id":"toolu_1","name":"get_battery","input":{}}],
            "stop_reason":"tool_use","stop_sequence":null,
            "usage":{"input_tokens":10,"output_tokens":5}}""")
        replies.add("""{"id":"msg_2","type":"message","role":"assistant","model":"claude-opus-5-5",
            "content":[{"type":"text","text":"Your battery is at 80 percent."}],
            "stop_reason":"end_turn","stop_sequence":null,
            "usage":{"input_tokens":20,"output_tokens":8}}""")
        val chat = ClaudeChatSession("sk-ant-test", "claude-opus-5-5", "low", "Be brief.",
            listOf(battery, directions), baseUrl = base)
        assertEquals("Your battery is at 80 percent.", chat.ask("How's the battery?", runner))
        assertEquals(listOf("get_battery"), ran.map { it.name })

        val (headers, first) = requests[0]
        // The SDK (3.2.0) sends the key as "Authorization: Bearer"; older ones
        // used x-api-key. Either way it is exactly this key and nothing else.
        val sent = headers["x-api-key"]?.single()
            ?: headers["authorization"]?.single()?.removePrefix("Bearer ")
        assertEquals("sk-ant-test", sent)
        assertTrue(headers["anthropic-beta"].orEmpty().joinToString(",")
            .contains("server-side-fallback-2026-07-01"))
        assertEquals("claude-opus-5-5", first.getString("model"))
        assertEquals(16000, first.getInt("max_tokens"))
        assertEquals("Be brief.", first.get("system").toString())
        assertEquals("default", first.getString("fallbacks"))
        assertEquals("low", first.getJSONObject("output_config").getString("effort"))
        assertFalse(first.has("temperature") || first.has("tool_choice") || first.has("thinking"))
        val tools = first.getJSONArray("tools")
        assertEquals("get_directions", tools.getJSONObject(1).getString("name"))
        assertEquals("integer", tools.getJSONObject(1).getJSONObject("input_schema")
            .getJSONObject("properties").getJSONObject("count").getString("type"))

        // Second request: the first reply echoed whole (thinking block and its
        // signature included), then ONE user message with the tool result.
        val msgs = requests[1].second.getJSONArray("messages")
        assertEquals(3, msgs.length())
        val echoed = msgs.getJSONObject(1)
        assertEquals("assistant", echoed.getString("role"))
        val blocks = echoed.getJSONArray("content")
        assertEquals("thinking", blocks.getJSONObject(0).getString("type"))
        assertEquals("sig-1", blocks.getJSONObject(0).getString("signature"))
        assertEquals("toolu_1", blocks.getJSONObject(1).getString("id"))
        val result = msgs.getJSONObject(2).getJSONArray("content").getJSONObject(0)
        assertEquals("tool_result", result.getString("type"))
        assertEquals("toolu_1", result.getString("tool_use_id"))
        assertTrue(result.get("content").toString().contains("charge_pct"))
        // The first request's messages are a prefix of the second's: append-only.
        assertEquals(first.getJSONArray("messages").getJSONObject(0).toString(),
            msgs.getJSONObject(0).toString())
    }

    @Test
    fun claudeRefusalIsASentenceNotACrash() = runBlocking {
        replies.add("""{"id":"msg_r","type":"message","role":"assistant","model":"claude-opus-5-5",
            "content":[],"stop_reason":"refusal","stop_sequence":null,
            "stop_details":{"type":"refusal","category":null,"explanation":null},
            "usage":{"input_tokens":5,"output_tokens":0}}""")
        val chat = ClaudeChatSession("k", "claude-opus-5-5", "low", "s", emptyList(), baseUrl = base)
        assertTrue(chat.ask("?", runner).startsWith("I can't help with that one"))
    }

    @Test
    fun geminiIsAskedToThinkBriefly() = runBlocking {
        replies.add("""{"choices":[{"message":{"role":"assistant","content":"80 %"},
            "finish_reason":"stop"}]}""")
        val chat = OpenAiChatSession(base, "k", "gemini-3.8-flash", "Be brief.", listOf(battery),
            reasoningEffort = AiPresets.byId("gemini").reasoningEffort)
        assertEquals("80 %", chat.ask("Charge?", runner))
        assertEquals("low", requests[0].second.getString("reasoning_effort"))
    }

    @Test
    fun aSlowServiceIsToldApartFromNoConnection() {
        // A server that accepts and never answers: the read wait runs out.
        val silent = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
        thread(isDaemon = true) { runCatching { silent.accept(); Thread.sleep(10_000) } }
        val chat = OpenAiChatSession("http://127.0.0.1:${silent.localPort}", "k", "m", "s",
            emptyList(), timeoutMs = 500)
        val e = runCatching { runBlocking { chat.ask("hi", runner) } }.exceptionOrNull()
        silent.close()
        assertTrue(e?.message, e is AiException && e.message!!.contains("did not answer within"))
    }

    @Test
    fun openAiCompatibleToolLoop() = runBlocking {
        replies.add("""{"choices":[{"message":{"role":"assistant","content":null,
            "tool_calls":[{"id":"call_1","type":"function",
              "function":{"name":"get_directions","arguments":"{\"count\": 3}"}}]},
            "finish_reason":"tool_calls"}]}""")
        replies.add("""{"choices":[{"message":{"role":"assistant",
            "content":"In two miles, keep left for the M6."},"finish_reason":"stop"}]}""")
        val chat = OpenAiChatSession(base, "sk-test", "some-model", "Be brief.",
            listOf(battery, directions))
        assertEquals("In two miles, keep left for the M6.", chat.ask("What's next?", runner))
        assertEquals(3, ran.single().args.getInt("count"))

        val (headers, first) = requests[0]
        assertEquals("Bearer sk-test", headers["authorization"]?.single())
        assertEquals("system", first.getJSONArray("messages").getJSONObject(0).getString("role"))
        assertEquals("function", first.getJSONArray("tools").getJSONObject(0).getString("type"))
        assertFalse(first.has("temperature") || first.has("max_tokens"))
        assertFalse("only sent when the service asks for it", first.has("reasoning_effort"))
        val msgs = requests[1].second.getJSONArray("messages")
        assertEquals("call_1", msgs.getJSONObject(2).getJSONArray("tool_calls")
            .getJSONObject(0).getString("id"))
        assertEquals("tool", msgs.getJSONObject(3).getString("role"))
        assertEquals("call_1", msgs.getJSONObject(3).getString("tool_call_id"))
    }

    @Test
    fun contentPartsAndBadArgumentsAreTolerated() {
        val parts = JSONObject(
            """{"content":[{"type":"text","text":"Hello "},{"type":"text","text":"there"}]}""")
        assertEquals("Hello there", OpenAiChatSession.contentText(parts))
        val calls = OpenAiChatSession.parseToolCalls(JSONObject(
            """{"tool_calls":[{"id":"c","function":{"name":"get_battery","arguments":"not json"}}]}"""))
        assertEquals(0, calls.single().args.length())
    }
}
