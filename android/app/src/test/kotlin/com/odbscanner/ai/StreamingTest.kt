package com.odbscanner.ai

import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * Answers spoken as they arrive: server-sent events put back together,
 * words cut into sentences that are safe to speak, a service that will not
 * stream asked normally, and a backup never answering over words already
 * spoken. Fake services; nothing is sent.
 */
class StreamingTest {
    private fun ev(o: String) = "data: $o"
    private fun words(s: String) = ev("""{"choices":[{"delta":{"content":${JSONObject.quote(s)}}}]}""")

    @Test
    fun eventsComeBackAsTheReplyAnUnstreamedRequestGives() {
        val heard = mutableListOf<String>()
        val acc = SseAccumulator { heard += it }
        listOf(words("Your next "), words("stop is Cobham."), ": keep-alive", "",
            ev("""{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"get_","arguments":"{\"co"}}]}}]}"""),
            ev("""{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"trip","arguments":"unt\":2}"}}]}}]}"""),
            ev("""{"usage":{"prompt_tokens":5,"completion_tokens":7},"choices":[]}"""),
            "data: [DONE]").forEach(acc::line)
        assertEquals(listOf("Your next ", "stop is Cobham."), heard)
        val msg = acc.message()
        assertEquals("Your next stop is Cobham.", msg.getString("content"))
        val call = OpenAiChatSession.parseToolCalls(msg).single()
        assertEquals("get_trip", call.name)
        assertEquals(2, call.args.getInt("count"))
        assertEquals(7, acc.usage!!.getInt("completion_tokens"))
        assertTrue(acc.done && acc.started)
    }

    @Test
    fun anErrorEventIsKept() {
        val acc = SseAccumulator { }
        acc.line(ev("""{"error":{"code":429,"message":"quota exceeded"}}"""))
        assertEquals(429, acc.error!!.getInt("code"))
    }

    @Test
    fun wordsBecomeSentencesSafeToSay() {
        val c = SentenceChunker(minChars = 24)
        val out = mutableListOf<String>()
        listOf("Sure. Your battery ", "is at 62.5 per cent", ". The next stop ", "is 18 miles away.",
            " Arrival is 32 per cent").forEach { out += c.feed(it) }
        assertEquals(listOf("Sure. Your battery is at 62.5 per cent.",
            "The next stop is 18 miles away."), out)            // "Sure." waited; 62.5 not cut
        assertEquals("Arrival is 32 per cent", c.flush())
        assertNull(c.flush())
    }

    private fun session(t: HttpTransport) = OpenAiChatSession("https://api.example.test/v1", "k-0000",
        "m", "s", emptyList(), provider = "Ex", transport = t, streaming = true)

    @Test
    fun aStreamedAnswerArrivesInOrderAndComesBackWhole() = runBlocking {
        var sent = JSONObject()
        val s = session { req ->
            sent = JSONObject(req.body!!)
            HttpResponse(200, listOf(words("It's "), words("62 per cent."), "data: [DONE]")
                .joinToString("\n"), 3)
        }
        val heard = mutableListOf<String>()
        assertEquals("It's 62 per cent.", s.ask("battery?", ToolRunner { "" }) { heard += it })
        assertEquals(listOf("It's ", "62 per cent."), heard)
        assertTrue(sent.getBoolean("stream"))
    }

    @Test
    fun aServiceThatWillNotStreamIsAskedNormallyFromThenOn() = runBlocking {
        val streamed = mutableListOf<Boolean>()
        val s = session { req ->
            val stream = JSONObject(req.body!!).optBoolean("stream")
            streamed += stream
            if (stream) HttpResponse(400, """{"error":{"message":"stream is not supported"}}""", 1)
            else HttpResponse(200, """{"choices":[{"message":{"role":"assistant","content":"62"}}]}""", 1)
        }
        assertEquals("62", s.ask("q", ToolRunner { "" }) { })
        assertEquals("62", s.ask("q2", ToolRunner { "" }) { })
        assertEquals(listOf(true, false, false), streamed)
    }

    @Test
    fun noBackupAnswersOverWordsAlreadySpoken() = runBlocking {
        var backupAsked = false
        val main = object : ChatSession {
            override val model = "m"
            override suspend fun ask(text: String, tools: ToolRunner) = error("not used")
            override suspend fun ask(text: String, tools: ToolRunner, onText: ((String) -> Unit)?): String {
                onText?.invoke("Your battery is ")
                throw AiException(AiError(AiErrorCategory.NETWORK_ERROR, "Main", "dropped"))
            }
        }
        val backup = object : ChatSession {
            override val model = "b"
            override suspend fun ask(text: String, tools: ToolRunner): String {
                backupAsked = true; return "from the backup"
            }
        }
        val chain = FallbackChatSession(listOf("Main" to { main }, "Backup" to { backup }),
            changesState = { false })
        try { chain.ask("q", ToolRunner { "" }) { }; fail() } catch (e: AiException) { }
        assertTrue(!backupAsked)
    }
}
