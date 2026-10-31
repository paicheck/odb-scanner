package com.odbscanner.ai

import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLHandshakeException

/**
 * Failures in their twelve categories, from the replies real services give
 * (shapes as OpenAI, Gemini, Groq, Mistral and Anthropic document them),
 * never with a key in them, and said out loud in a few words.
 */
class AiErrorsTest {
    private fun http(status: Int, body: String, model: String = "m") =
        AiErrors.fromHttp("Svc", status, body, model, listOf("sk-secret-1234567890abcdef"))

    @Test
    fun keysThatAreRefusedExpiredOrNotAllowed() {
        val openai = """{"error":{"message":"Incorrect API key provided: sk-secr****cdef.","type":"invalid_request_error","code":"invalid_api_key"}}"""
        assertEquals(AiErrorCategory.INVALID_CREDENTIALS, http(401, openai).category)
        val gemini = """[{"error":{"code":400,"message":"API key not valid. Please pass a valid API key.","status":"INVALID_ARGUMENT"}}]"""
        assertEquals(AiErrorCategory.INVALID_CREDENTIALS, http(400, gemini).category)
        val expired = """[{"error":{"code":400,"message":"API key expired. Please renew the API key.","status":"INVALID_ARGUMENT"}}]"""
        http(400, expired).let {
            assertEquals(AiErrorCategory.INVALID_CREDENTIALS, it.category)
            assertEquals("expired", it.reason)
            assertTrue(it.message.contains("expired"))
        }
        val anthropic = """{"type":"error","error":{"type":"permission_error","message":"Your API key does not have permission to use the specified resource."}}"""
        assertEquals(AiErrorCategory.PERMISSION_DENIED, http(403, anthropic).category)
        assertEquals("region", http(403, """{"error":{"message":"Country, region, or territory not supported"}}""").reason)
    }

    @Test
    fun quotaIsNotARateLimitAndARateLimitIsNotQuota() {
        val gemini = """[{"error":{"code":429,"message":"You exceeded your current quota, please check your plan and billing details.","status":"RESOURCE_EXHAUSTED"}}]"""
        assertEquals(AiErrorCategory.QUOTA_EXCEEDED, http(429, gemini).category)
        val groqDay = """{"error":{"message":"Rate limit reached for model `llama-3.3-70b-versatile` on tokens per day (TPD): Limit 100000","type":"tokens","code":"rate_limit_exceeded"}}"""
        assertEquals(AiErrorCategory.QUOTA_EXCEEDED, http(429, groqDay).category)
        val groqMinute = """{"error":{"message":"Rate limit reached for model on requests per minute (RPM): Limit 30","type":"requests","code":"rate_limit_exceeded"}}"""
        assertEquals(AiErrorCategory.RATE_LIMITED, http(429, groqMinute).category)
        val credit = """{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low to access the Anthropic API."}}"""
        assertEquals(AiErrorCategory.QUOTA_EXCEEDED, http(400, credit).category)
        assertEquals(AiErrorCategory.QUOTA_EXCEEDED, http(402, "{}").category)
    }

    @Test
    fun modelsToolsTimeoutsAndOutages() {
        assertEquals(AiErrorCategory.MODEL_UNAVAILABLE, http(404, """{"error":{"message":"The model `gpt-9` does not exist"}}""").category)
        assertEquals(AiErrorCategory.MODEL_UNAVAILABLE, http(400, """{"message":"Invalid model: mistral-huge","object":"error"}""").category)
        assertEquals(AiErrorCategory.UNSUPPORTED_CAPABILITY, http(400, """{"error":{"message":"This model does not support tools"}}""").category)
        assertEquals(AiErrorCategory.MODEL_UNAVAILABLE, http(529, """{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}""").category)
        assertEquals(AiErrorCategory.TIMEOUT, http(504, "gateway timeout").category)
        assertEquals(AiErrorCategory.UNKNOWN_ERROR, http(500, "oops").category)
    }

    @Test
    fun networkFailuresSayWhichPart() {
        fun e(t: Throwable, sent: Boolean = false) = AiErrors.fromException("Svc", "api.x", t, sent, 90)
        assertEquals("dns", e(UnknownHostException("api.x")).reason)
        assertEquals("tls", e(SSLHandshakeException("bad cert")).reason)
        assertEquals("connect", e(ConnectException("refused")).reason)
        e(SocketTimeoutException("connect timed out")).let {
            assertEquals(AiErrorCategory.TIMEOUT, it.category); assertEquals("connect", it.reason)
        }
        e(SocketTimeoutException("Read timed out"), sent = true).let {
            assertEquals(AiErrorCategory.TIMEOUT, it.category); assertEquals("read", it.reason)
            assertTrue(it.message.contains("90 s"))
        }
        assertEquals(AiErrorCategory.INVALID_RESPONSE, e(org.json.JSONException("x")).category)
    }

    @Test
    fun theKeyNeverComesOutInAnyMessage() {
        val key = "sk-secret-1234567890abcdef"
        val body = """{"error":{"message":"Invalid key $key sent as Bearer $key"}}"""
        val err = AiErrors.fromHttp("Svc", 401, body, "m", listOf(key))
        assertFalse(err.detail.contains(key)); assertFalse(err.message.contains(key))
        val text = "Authorization: Bearer abcdefgh12345678 x-api-key: sk-ant-api03-ZZZZZZZZZZZZZZ " +
            "https://x/y?key=AIzaSyA1234567890123456789012345 gsk_abcdefghijklmnop xai-abcdefghijklmnop"
        val clean = AiErrors.redact(text, emptyList())
        for (secret in listOf("abcdefgh12345678", "sk-ant-api03", "AIzaSyA", "gsk_abc", "xai-abc")) {
            assertFalse(clean, clean.contains(secret))
        }
    }

    @Test
    fun thePhonesSpeechEnginesFailuresAreNamed() {
        assertEquals(AiErrorCategory.PERMISSION_DENIED,
            AiErrors.fromRecognizer(SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS).category)
        assertEquals("silence", AiErrors.fromRecognizer(SpeechRecognizer.ERROR_SPEECH_TIMEOUT).reason)
        assertEquals(AiErrorCategory.NETWORK_ERROR,
            AiErrors.fromRecognizer(SpeechRecognizer.ERROR_NETWORK).category)
        assertEquals(AiErrorCategory.AUDIO_ERROR,
            AiErrors.fromRecognizer(SpeechRecognizer.ERROR_RECOGNIZER_BUSY).category)
        assertEquals(AiErrorCategory.MODEL_UNAVAILABLE,
            AiErrors.fromTts(TextToSpeech.ERROR_NOT_INSTALLED_YET).category)
        assertEquals(AiErrorCategory.NETWORK_ERROR, AiErrors.fromTts(TextToSpeech.ERROR_NETWORK).category)
    }

    @Test
    fun whatIsSaidIsShortAndNeverTheDetail() {
        val err = http(400, """{"error":{"message":"${"very long provider text ".repeat(20)}"}}""")
        assertTrue(err.spoken.length < 60)
        assertFalse(err.spoken.contains("provider text"))
        for (cat in AiErrorCategory.values()) assertTrue(AiErrors.spoken(cat).length < 60)
        assertEquals("Sorry, I didn't catch that.",
            AiErrors.fromRecognizer(SpeechRecognizer.ERROR_NO_MATCH).spoken)
    }
}
