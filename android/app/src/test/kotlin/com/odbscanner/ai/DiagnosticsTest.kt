package com.odbscanner.ai

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.UnknownHostException
import javax.net.ssl.SSLHandshakeException

/**
 * The setup screen's tests against fake services: each failure in its
 * category and the part that failed, the answer and tokens when it works,
 * results stored without a key, and only synthetic words sent.
 */
class DiagnosticsTest {
    private val groq = AiPresets.byId("groq")
    private val key = "gsk_abcdefghijklmnop0123"

    @Test
    fun connectionSaysWhetherDnsTlsOrNothingIsWrong() = runBlocking {
        val ok = Diagnostics.connection(groq, "") { HttpResponse(401, "{}", 12) }
        assertTrue(ok.ok); assertTrue(ok.summary.contains("HTTP 401 without a key"))
        val dns = Diagnostics.connection(groq, "") { throw HttpFailure(false, UnknownHostException("x")) }
        assertFalse(dns.ok); assertEquals("dns", dns.error?.reason)
        val tls = Diagnostics.connection(groq, "") { throw HttpFailure(false, SSLHandshakeException("x")) }
        assertEquals("tls", tls.error?.reason)
    }

    @Test
    fun theKeyTestTellsRefusedFromExpiredAndChecksTheModel() = runBlocking {
        val refused = Diagnostics.auth(groq, key, "m", "") {
            HttpResponse(401, """{"error":{"message":"Invalid API Key $key"}}""", 9)
        }
        assertEquals(AiErrorCategory.INVALID_CREDENTIALS, refused.error?.category)
        assertFalse(Diagnostics.encode(refused).contains(key))
        val listed = """{"data":[{"id":"llama-3.3-70b-versatile"},{"id":"other"}]}"""
        val good = Diagnostics.auth(groq, key, "llama-3.3-70b-versatile", "") { HttpResponse(200, listed, 9) }
        assertTrue(good.ok); assertNull(good.error)
        val missing = Diagnostics.auth(groq, key, "gone-model", "") { HttpResponse(200, listed, 9) }
        assertTrue(missing.ok)
        assertEquals(AiErrorCategory.MODEL_UNAVAILABLE, missing.error?.category)
        val none = Diagnostics.auth(groq, "", "m", "") { error("never sent") }
        assertEquals(AiErrorCategory.NOT_CONFIGURED, none.error?.category)
    }

    @Test
    fun theAiTestSendsOnlyTheTestSentenceAndReportsTheAnswer() = runBlocking {
        var sent = ""
        val r = Diagnostics.llm(groq, key, "llama-3.3-70b-versatile", "", transport = { req ->
            sent = req.body ?: ""
            HttpResponse(200, """{"model":"llama-3.3-70b-versatile","choices":[{"message":
                {"role":"assistant","content":"ready"}}],"usage":{"prompt_tokens":21,
                "completion_tokens":2}}""", 300)
        })
        assertTrue(r.ok)
        assertTrue(r.summary, r.summary.contains("“ready”") && r.summary.contains("21 tokens in"))
        assertTrue(sent.contains(Diagnostics.TEST_PROMPT))
        assertFalse(sent.contains("tools"))                 // no tools, so no car data reachable
        val quota = Diagnostics.llm(groq, key, "m", "", transport = {
            HttpResponse(429, """{"error":{"message":"tokens per day (TPD) limit"}}""", 5)
        })
        assertEquals(AiErrorCategory.QUOTA_EXCEEDED, quota.error?.category)
    }

    @Test
    fun aResultIsStoredAndReadBackWithoutChange() {
        val r = TestResult("Groq", TestKind.END_TO_END, false, 1_000L, 1234, "Failed at AI",
            AiError(AiErrorCategory.TIMEOUT, "Groq", "slow", "detail", 504, "service"), "AI",
            mapOf("recognition" to 800L, "AI" to 434L))
        assertEquals(r, Diagnostics.decode(Diagnostics.encode(r)))
        assertNull(Diagnostics.decode("not json"))
    }

    @Test
    fun everyProviderSaysWhatItCanDoAndWhatItReceives() {
        val all = Diagnostics.providers()
        val llms = all.filter { Capability.LLM in it.capabilities }
        assertEquals(AiPresets.ALL.size, llms.size)
        assertTrue(llms.none { Capability.STT in it.capabilities || Capability.TTS in it.capabilities })
        assertTrue(all.all { it.receives.isNotBlank() })
        assertTrue(all.first { it.id == Diagnostics.WAKE }.onPhone)
    }
}
