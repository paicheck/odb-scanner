package com.odbscanner.ai

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.net.UnknownHostException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Stopping a request: blocking work walked away from at once, a request in
 * flight aborted and its turn taken out of the conversation, and a stop
 * never handed on to a backup. The services are fakes; nothing is sent.
 */
class CancelTest {

    @Test
    fun blockingWorkIsLeftAtOnceAndItsLateResultDropped() = runBlocking {
        val release = CountDownLatch(1)
        val aborted = AtomicBoolean(false)
        val finished = CountDownLatch(1)
        val job = async(Dispatchers.Default) {
            Cancellable.blocking(abort = { aborted.set(true); release.countDown() }) {
                release.await(10, TimeUnit.SECONDS)
                finished.countDown()
                "late answer"
            }
        }
        delay(50)
        val begun = System.nanoTime()
        job.cancel()
        try {
            job.await(); fail("a cancelled call must not return")
        } catch (e: CancellationException) {
            // expected
        }
        assertTrue((System.nanoTime() - begun) / 1_000_000 < 1_000)
        assertTrue(aborted.get())
        assertTrue(finished.await(5, TimeUnit.SECONDS))   // it ended; its answer went nowhere
    }

    private fun reply(content: String?, tools: JSONArray? = null) = JSONObject().put("choices",
        JSONArray().put(JSONObject().put("message", JSONObject().put("role", "assistant")
            .put("content", content ?: JSONObject.NULL).apply { tools?.let { put("tool_calls", it) } })))
        .toString()

    private fun session(transport: HttpTransport) = OpenAiChatSession("https://api.example.test/v1",
        "sk-test-0123456789abcdef", "m", "system", emptyList(), provider = "Example",
        transport = transport)

    @Test
    fun aRequestInFlightIsAbortedAndTheTurnTakenOutOfTheConversation() = runBlocking {
        val bodies = mutableListOf<String>()
        val hang = CompletableDeferred<Unit>()
        var call = 0
        val transport = HttpTransport { req ->
            bodies += req.body ?: ""
            call++
            if (call == 2) { hang.await(); error("never") }     // the second question hangs
            HttpResponse(200, reply("answer $call"), 5)
        }
        val s = session(transport)
        assertEquals("answer 1", s.ask("first", ToolRunner { "" }))
        val turn = async { s.ask("second", ToolRunner { "" }) }
        delay(50)
        turn.cancel()
        try { turn.await(); fail() } catch (e: CancellationException) { }
        // The next question is asked on the conversation as it was before the
        // stopped one: "second" is not in it.
        assertEquals("answer 3", s.ask("third", ToolRunner { "" }))
        val sent = JSONObject(bodies.last()).getJSONArray("messages")
        val said = (0 until sent.length()).map { sent.getJSONObject(it).optString("content") }
        assertEquals(listOf("system", "first", "answer 1", "third"), said)
    }

    @Test
    fun aRejectionComesBackInItsCategoryWithoutTheKey() = runBlocking {
        val s = session { HttpResponse(401,
            """{"error":{"message":"Incorrect API key provided: sk-test-0123456789abcdef"}}""", 3) }
        try {
            s.ask("hi", ToolRunner { "" }); fail()
        } catch (e: AiException) {
            assertEquals(AiErrorCategory.INVALID_CREDENTIALS, e.category)
            assertFalse(e.error!!.detail.contains("sk-test-0123456789abcdef"))
            assertTrue(e.message!!.startsWith("Example"))
        }
    }

    @Test
    fun noNetworkIsANetworkErrorNamingTheLookup() = runBlocking {
        val s = session { throw HttpFailure(false, UnknownHostException("api.example.test")) }
        try {
            s.ask("hi", ToolRunner { "" }); fail()
        } catch (e: AiException) {
            assertEquals(AiErrorCategory.NETWORK_ERROR, e.category)
            assertEquals("dns", e.error!!.reason)
        }
    }

    @Test
    fun aStopIsNeverHandedToTheBackup() = runBlocking {
        val backupAsked = AtomicBoolean(false)
        val hang = CompletableDeferred<String>()
        val chain = FallbackChatSession(listOf(
            "Main" to { object : ChatSession {
                override val model = "m"
                override suspend fun ask(text: String, tools: ToolRunner) = hang.await()
            } },
            "Backup" to { object : ChatSession {
                override val model = "b"
                override suspend fun ask(text: String, tools: ToolRunner): String {
                    backupAsked.set(true); return "backup"
                }
            } },
        ), changesState = { false })
        val turn = async { chain.ask("q", ToolRunner { "" }) }
        delay(50)
        turn.cancel()
        try { turn.await() } catch (e: CancellationException) { }
        assertFalse(backupAsked.get())
        assertEquals("Main", chain.activeLabel)
    }

    @Test
    fun whenTheMainServiceFailsTheBackupIsVisible() = runBlocking {
        val chain = FallbackChatSession(listOf(
            "Main" to { object : ChatSession {
                override val model = "m"
                override suspend fun ask(text: String, tools: ToolRunner): String =
                    throw AiException(AiError(AiErrorCategory.QUOTA_EXCEEDED, "Main", "used up"))
            } },
            "Backup" to { object : ChatSession {
                override val model = "b"
                override suspend fun ask(text: String, tools: ToolRunner) = "from the backup"
            } },
        ), changesState = { false })
        assertEquals("from the backup", withTimeout(5_000) { chain.ask("q", ToolRunner { "" }) })
        assertTrue(chain.onBackup)
        assertEquals("Backup", chain.activeLabel)
        assertTrue(chain.takeNote()!!.contains("QUOTA_EXCEEDED"))
    }

    @Test
    fun turnedOffServicesAreNeverAsked() {
        val s = AiSettings(provider = "anthropic", backups = listOf("groq", "gemini"),
            disabled = setOf("anthropic", "gemini"))
        assertEquals(listOf("groq"), s.chain)
    }
}
