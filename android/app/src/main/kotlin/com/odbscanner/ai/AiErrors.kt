package com.odbscanner.ai

import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.io.InterruptedIOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException

/** What kind of failure: the same twelve words on every screen and in every log. */
enum class AiErrorCategory {
    NOT_CONFIGURED, INVALID_CREDENTIALS, PERMISSION_DENIED, QUOTA_EXCEEDED, RATE_LIMITED,
    MODEL_UNAVAILABLE, NETWORK_ERROR, TIMEOUT, UNSUPPORTED_CAPABILITY, INVALID_RESPONSE,
    AUDIO_ERROR, UNKNOWN_ERROR,
}

/**
 * One failure, said plainly. [message] is one sentence for the screen;
 * [detail] the provider's own words (shortened); [reason] narrows the
 * category where it helps the driver act ("dns", "tls", "expired",
 * "silence"...). Everything here has been through [AiErrors.redact]: it is
 * safe to show, store and log.
 */
data class AiError(
    val category: AiErrorCategory,
    val provider: String,
    val message: String,
    val detail: String = "",
    val status: Int? = null,
    val reason: String = "",
) {
    /** What is said out loud: short, never the detail. */
    val spoken: String get() = AiErrors.spoken(category, reason)

    /** "QUOTA_EXCEEDED · Groq: …" for a screen line. */
    val line: String get() = "$category · $message" + (if (detail.isNotBlank()) " ($detail)" else "")
}

/**
 * Turning failures into [AiError]s: HTTP replies (status and the provider's
 * error JSON, in the shapes OpenAI, Gemini, Mistral and Anthropic use),
 * exceptions on the way there, and the phone's speech recogniser and
 * text-to-speech codes. Pure, so every case is a unit test.
 */
object AiErrors {
    const val DETAIL_MAX = 300

    // -- HTTP -------------------------------------------------------------------------------

    /** The provider's own error message and type, from its error JSON, or the raw text. */
    fun providerMessage(body: String): Pair<String, String> {
        val text = body.trim()
        val obj: JSONObject? = runCatching {
            when {
                text.startsWith("[") -> JSONArray(text).optJSONObject(0)
                text.startsWith("{") -> JSONObject(text)
                else -> null
            }
        }.getOrNull()
        if (obj == null) return text.take(DETAIL_MAX) to ""
        val err = obj.optJSONObject("error")
        val message = err?.optString("message")?.ifBlank { null }
            ?: obj.optString("message").ifBlank { null }
            ?: obj.optString("detail").ifBlank { null } ?: text.take(DETAIL_MAX)
        val type = listOfNotNull(err?.optString("type"), err?.optString("code"),
            err?.optString("status"), obj.optString("type"))
            .filter { it.isNotBlank() && it != "error" }.joinToString(" ")
        return message to type
    }

    /** An HTTP reply of [status] (>= 400) from [provider] for [model]. */
    fun fromHttp(provider: String, status: Int, body: String, model: String = "",
                 secrets: Collection<String> = emptyList()): AiError {
        val (said, type) = providerMessage(redact(body, secrets))
        val words = "$said $type".lowercase()
        val detail = redact(said, secrets).take(DETAIL_MAX)
        fun any(vararg w: String) = w.any { it in words }
        val badKey = any("api key not valid", "api_key_invalid", "invalid api key",
            "invalid x-api-key", "incorrect api key", "invalid_api_key", "authentication_error",
            "unauthorized", "invalid authentication")
        val expired = any("expired")
        val quota = any("quota", "insufficient_quota", "credit balance", "billing", "credits",
            "per day", "tokens per day", "requests per day", "(tpd)", "(rpd)", "payment required")
        val modelGone = "model" in words && any("not found", "does not exist", "not supported",
            "decommissioned", "invalid model", "unknown model", "no such model", "not available",
            "unsupported model")
        val (category, reason) = when {
            status == 401 -> AiErrorCategory.INVALID_CREDENTIALS to (if (expired) "expired" else "")
            status == 402 -> AiErrorCategory.QUOTA_EXCEEDED to ""
            status == 403 && quota -> AiErrorCategory.QUOTA_EXCEEDED to ""
            status == 403 && badKey -> AiErrorCategory.INVALID_CREDENTIALS to ""
            status == 403 -> AiErrorCategory.PERMISSION_DENIED to
                (if (any("region", "location", "country", "territory")) "region" else "")
            status == 404 -> AiErrorCategory.MODEL_UNAVAILABLE to ""
            status == 408 || status == 504 -> AiErrorCategory.TIMEOUT to "service"
            status == 429 && quota -> AiErrorCategory.QUOTA_EXCEEDED to ""
            status == 429 -> AiErrorCategory.RATE_LIMITED to ""
            status == 400 && (badKey || (expired && "key" in words)) ->
                AiErrorCategory.INVALID_CREDENTIALS to (if (expired) "expired" else "")
            status == 400 && quota -> AiErrorCategory.QUOTA_EXCEEDED to ""
            (status == 400 || status == 422) && modelGone -> AiErrorCategory.MODEL_UNAVAILABLE to ""
            (status == 400 || status == 422) && any("tool", "function") &&
                any("not support", "unsupported", "does not support") ->
                AiErrorCategory.UNSUPPORTED_CAPABILITY to "tools"
            status == 503 || status == 529 || any("overloaded") ->
                AiErrorCategory.MODEL_UNAVAILABLE to "overloaded"
            else -> AiErrorCategory.UNKNOWN_ERROR to ""
        }
        val message = when (category) {
            AiErrorCategory.INVALID_CREDENTIALS -> if (reason == "expired")
                "$provider says the API key has expired: make a new one" else
                "$provider refused the API key"
            AiErrorCategory.PERMISSION_DENIED -> if (reason == "region")
                "$provider is not available from here for this key" else
                "$provider: this key is not allowed to do that (or to use $model)"
            AiErrorCategory.QUOTA_EXCEEDED -> "$provider: the account's quota or credit is used up"
            AiErrorCategory.RATE_LIMITED -> "$provider: too many requests; try again shortly"
            AiErrorCategory.MODEL_UNAVAILABLE -> if (reason == "overloaded")
                "$provider is overloaded or unavailable just now" else
                "$provider: model '${model.ifBlank { "?" }}' is not available"
            AiErrorCategory.TIMEOUT -> "$provider timed out (HTTP $status)"
            AiErrorCategory.UNSUPPORTED_CAPABILITY -> "$provider: this model cannot use the " +
                "assistant's tools"
            else -> "$provider returned HTTP $status"
        }
        return AiError(category, provider, message, detail, status, reason)
    }

    // -- exceptions ----------------------------------------------------------------------------

    /**
     * A failure before any HTTP reply. [sent]: whether the request had gone
     * out (a timeout after that is the service thinking, not the network).
     */
    fun fromException(provider: String, host: String, e: Throwable, sent: Boolean = false,
                      timeoutS: Int = 0): AiError {
        (e as? AiException)?.error?.let { return it }
        val why = redact(e.message ?: e.javaClass.simpleName, emptyList()).take(DETAIL_MAX)
        return when (e) {
            is UnknownHostException -> AiError(AiErrorCategory.NETWORK_ERROR, provider,
                "No internet connection, or $host could not be looked up", why, reason = "dns")
            is SSLException -> AiError(AiErrorCategory.NETWORK_ERROR, provider,
                "A secure connection to $host failed (the phone's date and time, a Wi-Fi " +
                    "sign-in page or a proxy)", why, reason = "tls")
            is ConnectException, is NoRouteToHostException -> AiError(
                AiErrorCategory.NETWORK_ERROR, provider, "Could not connect to $host", why,
                reason = "connect")
            is SocketTimeoutException -> if (!sent || why.contains("connect", ignoreCase = true))
                AiError(AiErrorCategory.TIMEOUT, provider,
                    "Could not connect to $host in time: check the phone's internet connection",
                    why, reason = "connect")
            else AiError(AiErrorCategory.TIMEOUT, provider,
                "$provider did not answer" + (if (timeoutS > 0) " within $timeoutS s" else "") +
                    "; try again, or a quicker service", why, reason = "read")
            is InterruptedIOException -> AiError(AiErrorCategory.TIMEOUT, provider,
                "$provider did not answer in time", why, reason = "read")
            is org.json.JSONException -> AiError(AiErrorCategory.INVALID_RESPONSE, provider,
                "$provider sent a reply that could not be read", why)
            is IOException -> AiError(AiErrorCategory.NETWORK_ERROR, provider,
                "Could not reach $host", why)
            else -> AiError(AiErrorCategory.UNKNOWN_ERROR, provider,
                "$provider failed (${e.javaClass.simpleName})", why)
        }
    }

    // -- the phone's speech engines ------------------------------------------------------

    const val RECOGNISER = "Speech recognition (phone)"
    const val SPEECH_OUT = "Speech output (phone)"

    /** A SpeechRecognizer error code. */
    fun fromRecognizer(code: Int): AiError {
        val (category, reason, message) = when (code) {
            SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> Triple(AiErrorCategory.TIMEOUT, "network",
                "the speech service did not answer in time")
            SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_SERVER_DISCONNECTED -> Triple(
                AiErrorCategory.NETWORK_ERROR, "", "no connection to the speech service")
            SpeechRecognizer.ERROR_AUDIO -> Triple(AiErrorCategory.AUDIO_ERROR, "microphone",
                "the microphone could not record")
            SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> Triple(AiErrorCategory.AUDIO_ERROR, "silence",
                "nothing was heard")
            SpeechRecognizer.ERROR_NO_MATCH -> Triple(AiErrorCategory.AUDIO_ERROR, "no_match",
                "no words were recognised")
            SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> Triple(AiErrorCategory.AUDIO_ERROR, "busy",
                "the speech recogniser is busy with something else")
            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> Triple(
                AiErrorCategory.PERMISSION_DENIED, "microphone", "no permission to use the microphone")
            SpeechRecognizer.ERROR_TOO_MANY_REQUESTS -> Triple(AiErrorCategory.RATE_LIMITED, "",
                "the speech service has had too many requests")
            SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED -> Triple(
                AiErrorCategory.UNSUPPORTED_CAPABILITY, "language",
                "British English is not supported by this recogniser")
            SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE -> Triple(AiErrorCategory.MODEL_UNAVAILABLE,
                "language", "the British English speech pack is not downloaded")
            else -> Triple(AiErrorCategory.UNKNOWN_ERROR, "", "the speech recogniser failed")
        }
        return AiError(category, RECOGNISER, message.replaceFirstChar { it.uppercase() },
            "SpeechRecognizer error $code", reason = reason)
    }

    /** A TextToSpeech error code (UtteranceProgressListener.onError, or init). */
    fun fromTts(code: Int): AiError {
        val (category, message) = when (code) {
            TextToSpeech.ERROR_NETWORK -> AiErrorCategory.NETWORK_ERROR to
                "the voice needs a connection it does not have"
            TextToSpeech.ERROR_NETWORK_TIMEOUT -> AiErrorCategory.TIMEOUT to
                "the voice's network connection timed out"
            TextToSpeech.ERROR_NOT_INSTALLED_YET -> AiErrorCategory.MODEL_UNAVAILABLE to
                "the voice's data is not installed yet"
            TextToSpeech.ERROR_INVALID_REQUEST -> AiErrorCategory.INVALID_RESPONSE to
                "the speech engine refused the text"
            TextToSpeech.ERROR_OUTPUT -> AiErrorCategory.AUDIO_ERROR to "the speech could not be played"
            TextToSpeech.ERROR_SERVICE -> AiErrorCategory.AUDIO_ERROR to "the speech engine stopped"
            TextToSpeech.ERROR_SYNTHESIS -> AiErrorCategory.AUDIO_ERROR to "the speech could not be made"
            else -> AiErrorCategory.AUDIO_ERROR to "speech output failed"
        }
        return AiError(category, SPEECH_OUT, message.replaceFirstChar { it.uppercase() },
            "TextToSpeech error $code")
    }

    // -- saying it -----------------------------------------------------------------------------

    /** The few words said out loud for a failure. */
    fun spoken(category: AiErrorCategory, reason: String = ""): String = when (category) {
        AiErrorCategory.NOT_CONFIGURED -> "The assistant isn't set up yet."
        AiErrorCategory.INVALID_CREDENTIALS -> "The AI service refused the key."
        AiErrorCategory.PERMISSION_DENIED -> if (reason == "microphone")
            "I can't use the microphone." else "The AI service won't allow that."
        AiErrorCategory.QUOTA_EXCEEDED -> "The AI service's allowance is used up."
        AiErrorCategory.RATE_LIMITED -> "The service is busy. Try again shortly."
        AiErrorCategory.MODEL_UNAVAILABLE -> "That AI model isn't available."
        AiErrorCategory.NETWORK_ERROR -> "There's no connection."
        AiErrorCategory.TIMEOUT -> "That took too long."
        AiErrorCategory.UNSUPPORTED_CAPABILITY -> "That isn't supported here."
        AiErrorCategory.INVALID_RESPONSE -> "The answer couldn't be used."
        AiErrorCategory.AUDIO_ERROR -> if (reason == "silence" || reason == "no_match")
            "Sorry, I didn't catch that." else "There's a problem with the audio."
        AiErrorCategory.UNKNOWN_ERROR -> "Sorry, that didn't work."
    }

    // -- secrets ---------------------------------------------------------------------------------

    private val PATTERNS = listOf(
        Regex("(?i)(bearer\\s+)[A-Za-z0-9._~+/=-]{8,}") to "$1[key]",
        Regex("(?i)(x-api-key[\"':=\\s]+)[^\\s\"',}]+") to "$1[key]",
        Regex("(?i)(authorization[\"':=\\s]+)[^\\s\"',}]+") to "$1[key]",
        Regex("(?i)([?&](?:key|api_key|apikey)=)[^&\\s\"]+") to "$1[key]",
        Regex("sk-(?:ant-)?[A-Za-z0-9_-]{12,}") to "[key]",
        Regex("AIza[0-9A-Za-z_-]{20,}") to "[key]",
        Regex("gsk_[A-Za-z0-9]{12,}") to "[key]",
        Regex("xai-[A-Za-z0-9]{12,}") to "[key]",
    )

    /**
     * [text] with every secret taken out: the keys given, and anything
     * shaped like a key or an authorisation header. Applied before anything
     * is shown, stored or logged.
     */
    fun redact(text: String, secrets: Collection<String>): String {
        var out = text
        secrets.filter { it.length >= 6 }.forEach { out = out.replace(it, "[key]") }
        PATTERNS.forEach { (re, to) -> out = re.replace(out, to) }
        return out
    }
}
