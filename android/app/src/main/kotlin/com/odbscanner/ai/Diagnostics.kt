package com.odbscanner.ai

import android.content.Context
import android.os.SystemClock
import android.speech.SpeechRecognizer
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.URL

/** What a provider can do here. The cloud AI services are chat only. */
enum class Capability { STT, LLM, TTS, WAKE_WORD }

/** A provider: what it does, and what it receives -- shown on the setup screen. */
data class ProviderInfo(
    val id: String,
    val label: String,
    val capabilities: Set<Capability>,
    val receives: String,
    val onPhone: Boolean,
)

enum class TestKind { CONNECTION, AUTH, LLM, STT, TTS, END_TO_END }

/**
 * One test's outcome. [summary] is what happened in a line; [error] the
 * failure, in its category; [stage] where an end-to-end test stopped;
 * [stages] how long each part took. Never holds a key: everything in it has
 * been through AiErrors.redact.
 */
data class TestResult(
    val provider: String,
    val kind: TestKind,
    val ok: Boolean,
    val atMs: Long,
    val ms: Long?,
    val summary: String,
    val error: AiError? = null,
    val stage: String = "",
    val stages: Map<String, Long> = emptyMap(),
)

/**
 * The setup screen's tests. Each uses the providers' own code -- the same
 * request shapes, the same transport, the same error classification as the
 * assistant -- so a test that passes means the assistant can do it.
 *
 * What is sent is synthetic: a fixed test sentence for the AI test, a fixed
 * phrase for speech output, and for the end-to-end test only the words the
 * driver chose to say into it. No vehicle data, no VIN, no position.
 */
object Diagnostics {
    const val TEST_PROMPT = "This is a connection test from a phone app. Reply with the single " +
        "word: ready"
    const val TEST_PHRASE = "This is the O D B Scanner voice test. You can stop me at any time."
    private const val E2E_PROMPT = "You are being tested as part of a voice pipeline check. " +
        "Reply in one short sentence to what the user said: "

    const val PHONE_STT = "phone_stt"
    const val PHONE_TTS = "phone_tts"
    const val WAKE = "vosk"

    /** Every provider and what it can do. */
    fun providers(): List<ProviderInfo> = listOf(
        ProviderInfo(PHONE_STT, "Speech recognition (the phone's)", setOf(Capability.STT),
            "your voice, while a question is asked -- the phone's speech service " +
                "(Google's on most phones), unless on-the-phone-only is on and the phone can",
            onPhone = false),
        ProviderInfo(PHONE_TTS, "Speech output (the phone's voice)", setOf(Capability.TTS),
            "the answer's text -- the phone's speech engine (online voices use the network)",
            onPhone = true),
        ProviderInfo(WAKE, "Wake word (Vosk, on the phone)", setOf(Capability.WAKE_WORD),
            "nothing leaves the phone", onPhone = true),
    ) + AiPresets.ALL.map {
        ProviderInfo(it.id, it.label, setOf(Capability.LLM),
            "your question and the app data the assistant looks up (never the VIN or your " +
                "exact position)", onPhone = false)
    }

    private fun now() = System.currentTimeMillis()
    private fun clock() = System.nanoTime() / 1_000_000

    private fun failed(provider: String, kind: TestKind, e: Throwable, ms: Long?,
                       stage: String = "", stages: Map<String, Long> = emptyMap()): TestResult {
        val err = (e as? AiException)?.error ?: AiErrors.fromException(provider, provider, e)
        return TestResult(provider, kind, false, now(), ms, err.message, err, stage, stages)
    }

    private fun notConfigured(provider: String, kind: TestKind, what: String) = TestResult(
        provider, kind, false, now(), null, what,
        AiError(AiErrorCategory.NOT_CONFIGURED, provider, what))

    // -- the AI services ---------------------------------------------------------------------

    /**
     * Can the phone reach the service at all? A request without a key: any
     * HTTP reply (401 included) means the network, DNS and TLS are fine.
     */
    suspend fun connection(preset: AiPreset, baseUrl: String,
                           transport: HttpTransport = UrlTransport): TestResult {
        val base = baseUrl.ifBlank { preset.baseUrl }.trimEnd('/')
        if (base.isBlank()) return notConfigured(preset.label, TestKind.CONNECTION,
            "No service address set")
        val url = if (preset.protocol == "anthropic") "$base/v1/models" else "$base/models"
        val host = runCatching { URL(url).host }.getOrDefault(url)
        val begun = clock()
        return try {
            val r = transport.send(HttpRequest("GET", url, mapOf("Accept" to "application/json"),
                connectTimeoutMs = 10_000, readTimeoutMs = 15_000))
            val ms = clock() - begun
            TestResult(preset.label, TestKind.CONNECTION, true, now(), ms,
                "Reached $host in $ms ms (DNS, TLS and HTTP fine; HTTP ${r.status} without a key)")
        } catch (e: CancellationException) {
            throw e
        } catch (e: HttpFailure) {
            failed(preset.label, TestKind.CONNECTION, AiException(AiErrors.fromException(
                preset.label, host, e.cause ?: e, e.sent, 15)), clock() - begun)
        } catch (e: Exception) {
            failed(preset.label, TestKind.CONNECTION, AiException(AiErrors.fromException(
                preset.label, host, e)), clock() - begun)
        }
    }

    /**
     * Is the key accepted? Lists the models with it: a refused, expired or
     * unpermitted key fails here, in its category. Also says whether the
     * chosen model is among them.
     */
    suspend fun auth(preset: AiPreset, key: String, model: String, baseUrl: String,
                     transport: HttpTransport = UrlTransport): TestResult {
        if (key.isBlank() && preset.id != "custom") {
            return notConfigured(preset.label, TestKind.AUTH, "No API key stored for ${preset.label}")
        }
        val base = baseUrl.ifBlank { preset.baseUrl }.trimEnd('/')
        val begun = clock()
        return try {
            val models = if (preset.protocol == "anthropic") ClaudeChatSession.listModels(key, baseUrl)
            else OpenAiChatSession.listModels(base, key, preset.label, transport)
            val ms = clock() - begun
            val note = when {
                model.isBlank() -> ""
                models.isEmpty() -> "; it listed no models"
                model in models -> "; '$model' is available"
                else -> "; '$model' is NOT among its ${models.size} models"
            }
            TestResult(preset.label, TestKind.AUTH, true, now(), ms,
                "Key accepted in $ms ms$note",
                if (model.isNotBlank() && models.isNotEmpty() && model !in models)
                    AiError(AiErrorCategory.MODEL_UNAVAILABLE, preset.label,
                        "${preset.label}: model '$model' is not in the key's list")
                else null)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            failed(preset.label, TestKind.AUTH, e, clock() - begun)
        }
    }

    /** One synthetic question, no tools: the answer, model, latency and tokens. */
    suspend fun llm(preset: AiPreset, key: String, model: String, baseUrl: String,
                    transport: HttpTransport = UrlTransport,
                    prompt: String = TEST_PROMPT): TestResult {
        if (key.isBlank() && preset.id != "custom") {
            return notConfigured(preset.label, TestKind.LLM, "No API key stored for ${preset.label}")
        }
        if (model.isBlank()) return notConfigured(preset.label, TestKind.LLM, "No model chosen")
        val base = baseUrl.ifBlank { preset.baseUrl }.trimEnd('/')
        val begun = clock()
        return try {
            val p = if (preset.protocol == "anthropic") ClaudeChatSession.probe(key, model, prompt, baseUrl)
            else OpenAiChatSession.probe(base, key, model, preset.label, prompt,
                preset.reasoningEffort, transport)
            val ms = clock() - begun
            val tokens = if (p.inputTokens != null || p.outputTokens != null)
                ", ${p.inputTokens ?: "?"} tokens in, ${p.outputTokens ?: "?"} out" else ""
            if (p.text.isBlank()) {
                TestResult(preset.label, TestKind.LLM, false, now(), ms, "An empty answer",
                    AiError(AiErrorCategory.INVALID_RESPONSE, preset.label,
                        "${preset.label} answered with nothing"))
            } else {
                TestResult(preset.label, TestKind.LLM, true, now(), ms,
                    "“${p.text.take(120)}” from ${p.model} in $ms ms$tokens")
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            failed(preset.label, TestKind.LLM, e, clock() - begun)
        }
    }

    // -- the phone's speech -----------------------------------------------------------------

    /** Whether the phone can recognise speech, and on the phone itself. */
    fun recogniser(context: Context): Pair<Boolean, Boolean?> {
        val any = SpeechRecognizer.isRecognitionAvailable(context)
        val local = if (android.os.Build.VERSION.SDK_INT >= 31)
            runCatching { SpeechRecognizer.isOnDeviceRecognitionAvailable(context) }.getOrNull()
        else null
        return any to local
    }

    /** Listen to one short sentence: what was recognised, how long it took. */
    suspend fun stt(context: Context, offline: Boolean): TestResult {
        val label = AiErrors.RECOGNISER
        return when (val r = Listener.listen(context, VoiceAssistant.LISTEN_MS, offline)) {
            is ListenResult.Heard -> TestResult(label, TestKind.STT, true, now(), r.ms,
                "Heard “${r.text}” in ${r.ms} ms")
            is ListenResult.Failed -> TestResult(label, TestKind.STT, false, now(), r.ms,
                r.error.message, r.error)
        }
    }

    /** Say the test phrase; [turn] makes it the assistant's, so Stop silences it. */
    suspend fun tts(context: Context, turn: Int): TestResult {
        val r = Speaker.timed(context, TEST_PHRASE, SpeechOwner.ASSISTANT, turn)
        val label = AiErrors.SPEECH_OUT
        return if (r.error == null) TestResult(label, TestKind.TTS, true, now(), r.totalMs,
            "Spoken: started after ${r.startMs ?: "?"} ms, ${r.totalMs} ms in all")
        else TestResult(label, TestKind.TTS, false, now(), r.totalMs, r.error.message, r.error)
    }

    /**
     * The whole chain: microphone, recognition, the AI (the chosen service,
     * no tools, the driver's own test words), speech. Says the stage that
     * failed and how long each took.
     */
    suspend fun endToEnd(context: Context, turn: Int, settings: AiSettings, key: String,
                         transport: HttpTransport = UrlTransport): TestResult {
        val stages = linkedMapOf<String, Long>()
        val label = "Voice chain"
        fun stop(stage: String, error: AiError) = TestResult(label, TestKind.END_TO_END, false,
            now(), stages.values.sum(), "Failed at $stage: ${error.message}", error, stage, stages)

        val (available, _) = withContext(Dispatchers.Main) { recogniser(context) }
        if (!available) return stop("microphone", AiError(AiErrorCategory.UNSUPPORTED_CAPABILITY,
            AiErrors.RECOGNISER, "This phone has no speech recognition"))
        val heard = when (val r = Listener.listen(context, VoiceAssistant.LISTEN_MS, settings.sttOffline)) {
            is ListenResult.Failed -> {
                stages["recognition"] = r.ms
                return stop(if (r.error.reason == "microphone") "microphone" else "recognition",
                    r.error)
            }
            is ListenResult.Heard -> r.also { stages["recognition"] = it.ms }
        }
        val ai = llm(settings.preset, key, settings.effectiveModel, settings.baseUrl, transport,
            E2E_PROMPT + heard.text)
        stages["AI"] = ai.ms ?: 0
        if (!ai.ok) return stop("AI", ai.error ?: AiError(AiErrorCategory.UNKNOWN_ERROR,
            settings.preset.label, ai.summary))
        val said = ai.summary.substringAfter("“").substringBefore("”")
        val spoken = Speaker.timed(context, said, SpeechOwner.ASSISTANT, turn)
        stages["speech"] = spoken.totalMs
        spoken.error?.let { return stop("speech", it) }
        return TestResult(label, TestKind.END_TO_END, true, now(), stages.values.sum(),
            "Heard “${heard.text}”, ${settings.preset.label} answered, and it was spoken",
            stages = stages)
    }

    // -- results ---------------------------------------------------------------------------

    fun encode(r: TestResult): String = JSONObject()
        .put("provider", r.provider).put("kind", r.kind.name).put("ok", r.ok).put("at", r.atMs)
        .put("ms", r.ms ?: JSONObject.NULL).put("summary", r.summary).put("stage", r.stage)
        .put("stages", JSONObject(r.stages as Map<*, *>))
        .put("error", r.error?.let {
            JSONObject().put("category", it.category.name).put("provider", it.provider)
                .put("message", it.message).put("detail", it.detail)
                .put("status", it.status ?: JSONObject.NULL).put("reason", it.reason)
        } ?: JSONObject.NULL)
        .toString()

    fun decode(text: String?): TestResult? = runCatching {
        val o = JSONObject(text ?: return null)
        val e = o.optJSONObject("error")
        val st = o.optJSONObject("stages")
        TestResult(o.getString("provider"), TestKind.valueOf(o.getString("kind")),
            o.getBoolean("ok"), o.getLong("at"), if (o.isNull("ms")) null else o.getLong("ms"),
            o.getString("summary"),
            e?.let {
                AiError(AiErrorCategory.valueOf(it.getString("category")), it.getString("provider"),
                    it.getString("message"), it.optString("detail"),
                    if (it.isNull("status")) null else it.getInt("status"), it.optString("reason"))
            }, o.optString("stage"),
            st?.keys()?.asSequence()?.associateWith { st.getLong(it) } ?: emptyMap())
    }.getOrNull()

    private const val FILE = "ai_diag"

    fun save(context: Context, id: String, r: TestResult) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            .putString("$id:${r.kind.name}", encode(r)).apply()
    }

    fun last(context: Context, id: String, kind: TestKind): TestResult? =
        decode(context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .getString("$id:${kind.name}", null))
}
