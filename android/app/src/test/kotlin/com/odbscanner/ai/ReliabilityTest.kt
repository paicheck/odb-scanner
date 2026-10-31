package com.odbscanner.ai

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.time.ZoneOffset

/**
 * Keeping the assistant answering: services rested and restored by a
 * circuit breaker, requests retried only when they cannot have been
 * processed, the conversation carried to a backup, your limits kept, and
 * speech falling back the private way. Simulated services and a fake clock.
 */
class ReliabilityTest {
    private var now = 1_000_000L
    private val net = AiError(AiErrorCategory.NETWORK_ERROR, "S", "down", reason = "connect")
    private val key = AiError(AiErrorCategory.INVALID_CREDENTIALS, "S", "refused")

    @Test
    fun aServiceThatKeepsFailingIsRestedThenTriedOnceThenRestored() {
        val b = CircuitBreaker(threshold = 3, baseCooldownMs = 60_000, clock = { now })
        repeat(2) { b.failure("g", net) }
        assertTrue(b.allow("g"))                       // two failures: still used
        b.failure("g", net)
        assertEquals(Health.RESTING, b.status("g").state)
        assertFalse(b.allow("g"))
        now += 60_000
        assertEquals(Health.TRIAL, b.status("g").state)
        assertTrue(b.allow("g"))                       // one trial...
        assertFalse(b.allow("g"))                      // ...at a time
        b.failure("g", net)                            // the trial failed: rested longer
        assertEquals(Health.RESTING, b.status("g").state)
        now += 60_000
        assertFalse(b.allow("g"))                      // 120 s this time
        now += 60_000
        assertTrue(b.allow("g"))
        b.success("g")
        assertEquals(Health.HEALTHY, b.status("g").state)
        assertEquals(0, b.status("g").failures)
    }

    @Test
    fun aRefusedKeyOrUsedUpQuotaIsNotAskedAgainUntilItChanges() {
        val b = CircuitBreaker(clock = { now })
        b.failure("a", key)
        assertEquals(Health.NEEDS_ATTENTION, b.status("a").state)
        assertFalse(b.allow("a"))
        b.reset("a")                                   // a new key was saved
        assertTrue(b.allow("a"))
        b.failure("a", AiError(AiErrorCategory.QUOTA_EXCEEDED, "S", "used up"))
        assertFalse(b.allow("a"))
    }

    @Test
    fun retriesOnlyWhatCannotHaveBeenProcessed() {
        fun e(c: AiErrorCategory, reason: String = "") = AiError(c, "S", "x", reason = reason)
        assertEquals(800L, RetryPolicy.delayFor(e(AiErrorCategory.NETWORK_ERROR, "connect"), 1))
        assertEquals(800L, RetryPolicy.delayFor(e(AiErrorCategory.TIMEOUT, "connect"), 1))
        assertNull(RetryPolicy.delayFor(e(AiErrorCategory.TIMEOUT, "read"), 1))      // may have answered
        assertNull(RetryPolicy.delayFor(e(AiErrorCategory.NETWORK_ERROR, "after_send"), 1))
        assertNull(RetryPolicy.delayFor(e(AiErrorCategory.NETWORK_ERROR, "dns"), 1))
        assertEquals(2_000L, RetryPolicy.delayFor(e(AiErrorCategory.RATE_LIMITED), 1, 2_000L))
        assertNull(RetryPolicy.delayFor(e(AiErrorCategory.RATE_LIMITED), 1, 30_000L))  // a backup instead
        assertEquals(1_500L, RetryPolicy.delayFor(e(AiErrorCategory.MODEL_UNAVAILABLE, "overloaded"), 1))
        for (c in listOf(AiErrorCategory.INVALID_CREDENTIALS, AiErrorCategory.QUOTA_EXCEEDED,
                AiErrorCategory.PERMISSION_DENIED, AiErrorCategory.INVALID_RESPONSE)) {
            assertNull(c.name, RetryPolicy.delayFor(e(c), 1))
        }
        assertNull(RetryPolicy.delayFor(e(AiErrorCategory.NETWORK_ERROR, "connect"), 2))  // bounded
        assertEquals(1_500L, RetryPolicy.retryAfterMs(mapOf("retry-after" to "1.5")))
        assertEquals(250L, RetryPolicy.retryAfterMs(mapOf("retry-after-ms" to "250")))
    }

    private val ok = """{"choices":[{"message":{"role":"assistant","content":"fine"}}],
        "usage":{"prompt_tokens":10,"completion_tokens":3}}"""

    private fun session(usage: UsageSink? = null, t: HttpTransport) = OpenAiChatSession(
        "https://api.example.test/v1", "k-000000", "m", "s", emptyList(), provider = "Ex",
        transport = t, usageId = "groq", usage = usage)

    @Test
    fun aRateLimitIsWaitedOutOnceAndARefusalIsNotRetried() = runBlocking {
        var calls = 0
        val limited = session { calls++; if (calls == 1) HttpResponse(429, "{}", 1,
            mapOf("retry-after" to "0.05")) else HttpResponse(200, ok, 1) }
        assertEquals("fine", limited.ask("q", ToolRunner { "" }))
        assertEquals(2, calls)
        calls = 0
        val refused = session { calls++; HttpResponse(401, "{}", 1) }
        try { refused.ask("q", ToolRunner { "" }); fail() } catch (e: AiException) { }
        assertEquals(1, calls)
        calls = 0
        val slow = session { calls++; throw HttpFailure(true, SocketTimeoutException("Read timed out")) }
        try { slow.ask("q", ToolRunner { "" }); fail() } catch (e: AiException) { }
        assertEquals("a read timeout is never re-sent", 1, calls)
    }

    @Test
    fun aStopDuringTheWaitIsNeverRetried() = runBlocking {
        var calls = 0
        val s = session { calls++; HttpResponse(429, "{}", 1, mapOf("retry-after" to "3")) }
        val turn = async { s.ask("q", ToolRunner { "" }) }
        delay(100)
        turn.cancel()
        try { turn.await(); fail() } catch (e: CancellationException) { }
        delay(100)
        assertEquals(1, calls)
    }

    @Test
    fun tokenUseIsRecordedFromTheReply() = runBlocking {
        val seen = mutableListOf<Triple<String, Int?, Int?>>()
        session(usage = { p, i, o -> seen += Triple(p, i, o) }) { HttpResponse(200, ok, 1) }
            .ask("q", ToolRunner { "" })
        assertEquals(listOf(Triple("groq", 10, 3)), seen)
    }

    private class Fake(val answer: String?, val err: AiError? = null) : ChatSession {
        override val model = "m"
        val seeded = mutableListOf<Pair<String, String>>()
        val asked = mutableListOf<String>()
        override fun seed(turns: List<Pair<String, String>>) { seeded += turns }
        override suspend fun ask(text: String, tools: ToolRunner): String {
            asked += text
            err?.let { throw AiException(it) }
            return answer!!
        }
    }

    @Test
    fun aBackupTakingOverIsToldWhatWasSaid() = runBlocking {
        var mainFails = false
        val main = object : ChatSession {
            override val model = "m"
            override suspend fun ask(text: String, tools: ToolRunner): String =
                if (mainFails) throw AiException(net) else "Cobham, 18 miles"
        }
        val backup = Fake("About 32 % when you get there")
        val chain = FallbackChatSession(listOf("Main" to { main }, "Backup" to { backup }),
            changesState = { false }, ids = listOf("main", "backup"))
        chain.ask("Where is my next charging stop?", ToolRunner { "" })
        mainFails = true
        assertEquals("About 32 % when you get there",
            chain.ask("How much battery will I have there?", ToolRunner { "" }))
        assertEquals(listOf("Where is my next charging stop?" to "Cobham, 18 miles"), backup.seeded)
    }

    @Test
    fun aRestingServiceIsSkippedAndAllRestingStillTriesTheSoonest() = runBlocking {
        val b = CircuitBreaker(threshold = 1, clock = { now })
        b.failure("main", net)
        val main = Fake("from main")
        val backup = Fake("from backup")
        val chain = FallbackChatSession(listOf("Main" to { main }, "Backup" to { backup }),
            changesState = { false }, ids = listOf("main", "backup"), health = b)
        assertEquals("from backup", chain.ask("q", ToolRunner { "" }))
        assertTrue(main.asked.isEmpty())
        assertTrue(chain.takeNote()!!.contains("Main skipped"))
        // Both resting: the one whose rest ends first is tried anyway.
        val only = Fake("still here")
        b.failure("solo", net)
        val alone = FallbackChatSession(listOf("Solo" to { only }), changesState = { false },
            ids = listOf("solo"), health = b)
        assertEquals("still here", alone.ask("q", ToolRunner { "" }))
        assertEquals(Health.HEALTHY, b.status("solo").state)
    }

    @Test
    fun yourDailyLimitsAreKeptAndCostIsOnlyEverFromYourPrices() {
        val book = UsageBook(clock = { now }, zone = ZoneOffset.UTC)
        val limits = UsageLimits(requestsPerDay = 3)
        repeat(2) { book.record("groq", 1000, 100) }
        assertNull(book.check("groq", "Groq", limits, emptyMap()))
        assertNull(book.warning(limits, emptyMap()))                 // 67 %: not yet
        assertTrue(book.warning(UsageLimits(requestsPerDay = 2), emptyMap())!!
            .contains("2 of today's 2"))
        book.record("gemini", null, null)                      // no counts in the reply
        assertEquals(AiErrorCategory.QUOTA_EXCEEDED,
            book.check("groq", "Groq", limits, emptyMap())?.category)
        val price = Price(0.5, 1.5)
        assertEquals(2000 / 1e6 * 0.5 + 200 / 1e6 * 1.5, book.day("groq").cost(price) ?: -1.0, 1e-12)
        assertNull("no price, no cost", book.day("groq").cost(null))
        assertNull("unreported tokens: no estimate", book.day("gemini").cost(price))
        val spend = UsageLimits(spendPerDay = 0.001)
        assertEquals("local_limit", book.check("groq", "Groq", spend, mapOf("groq" to price))?.reason)
        val copy = UsageBook(clock = { now }, zone = ZoneOffset.UTC).apply { load(book.encode()) }
        assertEquals(book.day("groq"), copy.day("groq"))
        now += 86_400_000L
        assertEquals(0, book.day("groq").requests)              // a new day
    }

    @Test
    fun speechFallsBackThePrivateWayFreelyAndTheOtherOnlyWithConsent() {
        val noNet = AiError(AiErrorCategory.NETWORK_ERROR, AiErrors.RECOGNISER, "x")
        assertEquals(true, SpeechFallback.recogniserRetry(noNet, false, true, false))
        assertNull(SpeechFallback.recogniserRetry(noNet, false, false, false))   // no local one
        val noPack = AiError(AiErrorCategory.MODEL_UNAVAILABLE, AiErrors.RECOGNISER, "x")
        assertNull(SpeechFallback.recogniserRetry(noPack, true, true, false))
        assertEquals(false, SpeechFallback.recogniserRetry(noPack, true, true, true))
        val silence = AiError(AiErrorCategory.AUDIO_ERROR, AiErrors.RECOGNISER, "x", reason = "silence")
        assertNull(SpeechFallback.recogniserRetry(silence, false, true, true))
        assertTrue(SpeechFallback.voiceShouldFallBack(AiErrors.fromTts(-6)))      // ERROR_NETWORK
        assertFalse(SpeechFallback.voiceShouldFallBack(AiErrors.fromTts(-5)))     // ERROR_OUTPUT
    }

    @Test
    fun theAnswerStyleIsBriefWhileDriving() {
        assertEquals("brief", Assistant.effectiveStyle("auto", driving = true))
        assertEquals("normal", Assistant.effectiveStyle("auto", driving = false))
        assertEquals("detailed", Assistant.effectiveStyle("detailed", driving = true))
        assertTrue(AssistantTools.prompt("brief").contains("Style: brief"))
        assertTrue(AssistantTools.prompt("brief").startsWith(AssistantTools.SYSTEM_PROMPT))
        assertFalse(JSONObject().has("x"))
    }

    @Test
    fun aStoppedTrialIsLetGoSoTheServiceCanBeTriedAgain() = runBlocking {
        val b = CircuitBreaker(threshold = 1, baseCooldownMs = 60_000, clock = { now })
        b.failure("main", net)
        now += 60_000                                           // its rest is over: a trial
        val stopped = object : ChatSession {
            override val model = "m"
            override suspend fun ask(text: String, tools: ToolRunner): String =
                throw CancellationException("the driver pressed Stop")
        }
        val chain = FallbackChatSession(listOf("Main" to { stopped }), changesState = { false },
            ids = listOf("main"), health = b)
        try {
            chain.ask("q", ToolRunner { "" }); fail("a stop goes on up")
        } catch (_: CancellationException) {}
        // Neither a success nor a failure, and its trial is not left out for ever.
        assertEquals(Health.TRIAL, b.status("main").state)
        assertTrue(b.allow("main"))
    }

    @Test
    fun aDailyLimitReachedOnATrialDoesNotLeaveItStuck() = runBlocking {
        val b = CircuitBreaker(threshold = 1, baseCooldownMs = 60_000, clock = { now })
        b.failure("main", net)
        now += 60_000
        val main = Fake("from main")
        val backup = Fake("from backup")
        val limit = { id: String, label: String ->
            if (id == "main") AiError(AiErrorCategory.QUOTA_EXCEEDED, label, "your limit",
                reason = "local_limit") else null
        }
        val chain = FallbackChatSession(listOf("Main" to { main }, "Backup" to { backup }),
            changesState = { false }, ids = listOf("main", "backup"), health = b, limit = limit)
        assertEquals("from backup", chain.ask("q", ToolRunner { "" }))
        assertTrue(main.asked.isEmpty())
        assertTrue(b.allow("main"))                             // still free for its trial
    }

    @Test
    fun servicesThatNeedAttentionAreNotAskedAgainAndTheReasonIsGiven() = runBlocking {
        val b = CircuitBreaker(clock = { now })
        b.failure("main", key)
        b.failure("backup", AiError(AiErrorCategory.QUOTA_EXCEEDED, "B", "used up"))
        val main = Fake("from main")
        val backup = Fake("from backup")
        val chain = FallbackChatSession(listOf("Main" to { main }, "Backup" to { backup }),
            changesState = { false }, ids = listOf("main", "backup"), health = b)
        try {
            chain.ask("q", ToolRunner { "" }); fail("nothing could be asked")
        } catch (e: AiException) {
            assertEquals(AiErrorCategory.INVALID_CREDENTIALS, e.category)
            assertTrue(e.message!!.contains("AI & Voice Setup"))
        }
        assertTrue(main.asked.isEmpty() && backup.asked.isEmpty())
        // A resting backup is still tried while the main one needs attention.
        b.reset("backup")
        b.failure("backup", net); b.failure("backup", net); b.failure("backup", net)
        assertEquals(Health.RESTING, b.status("backup").state)
        // (A fresh chain, as the assistant makes after a failed turn.)
        val again = FallbackChatSession(listOf("Main" to { main }, "Backup" to { backup }),
            changesState = { false }, ids = listOf("main", "backup"), health = b)
        assertEquals("from backup", again.ask("q2", ToolRunner { "" }))
        assertTrue(main.asked.isEmpty())
    }

    @Test
    fun onlyAServiceWhoseSettingsChangedStartsAfresh() {
        val s = AiSettings(provider = "groq", backups = listOf("gemini"), disabled = setOf("mistral"))
        assertEquals(emptySet<String>(), AiPrefs.healthResets(s, s.copy(speechRate = 1.2f,
            historyDays = 7, limits = UsageLimits(requestsPerDay = 3), style = "brief")))
        assertEquals(setOf("groq"), AiPrefs.healthResets(s, s.copy(model = "llama-3.1-8b-instant")))
        assertEquals(setOf("mistral"), AiPrefs.healthResets(s, s.copy(disabled = emptySet())))
        // A new main service: it and the old one are asked differently now.
        assertEquals(setOf("groq", "gemini"), AiPrefs.healthResets(s, s.copy(provider = "gemini",
            backups = listOf("groq"))))
    }

    @Test
    fun aRetiredModelNeedsAttentionButAnOverloadedOneOnlyRests() = runBlocking {
        // Groq's own words for a model it shut down (llama-3.3-70b-versatile, 2026-08-16).
        val gone = AiErrors.fromHttp("Groq (free tier)", 400, """{"error":{"message":"The model """ +
            """`llama-3.3-70b-versatile` has been decommissioned and is no longer supported.",""" +
            """"type":"invalid_request_error","code":"model_decommissioned"}}""", "llama-3.3-70b-versatile")
        assertEquals(AiErrorCategory.MODEL_UNAVAILABLE, gone.category)
        assertEquals("That AI model isn't available.", gone.spoken)
        val b = CircuitBreaker(clock = { now })
        b.failure("groq", gone)
        assertEquals(Health.NEEDS_ATTENTION, b.status("groq").state)     // not asked every turn
        val busy = AiErrors.fromHttp("Gemini", 503, """{"error":{"message":"The model is overloaded."}}""",
            "gemini-3.8-flash")
        assertEquals("overloaded", busy.reason)
        b.failure("gemini", busy)
        assertEquals(Health.HEALTHY, b.status("gemini").state)          // one failure: still used
        // A backup whose model is gone is skipped, with no wait, once it has said so.
        val main = Fake(null, gone)
        val backup = Fake("from gemini")
        val chain = FallbackChatSession(listOf("Groq" to { main }, "Gemini" to { backup }),
            changesState = { false }, ids = listOf("groq", "gemini"), health = b)
        assertEquals("from gemini", chain.ask("q", ToolRunner { "" }))
        assertTrue(main.asked.isEmpty())
    }
}
