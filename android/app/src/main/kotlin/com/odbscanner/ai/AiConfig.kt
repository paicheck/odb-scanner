package com.odbscanner.ai

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Which cloud AI the assistant uses. The same services as the dashboard's
 * ai/cloud.py: Claude through Anthropic's SDK, the others through the
 * OpenAI-style chat completions request they share.
 */
data class AiPreset(
    val id: String,
    val label: String,
    val protocol: String,        // "anthropic" | "openai"
    val baseUrl: String,
    val keyUrl: String,
    /** The model used when the Model box is left blank; "" when one must be named. */
    val defaultModel: String = "",
    /** How its free tier works, shown under the key box; "" when it has none. */
    val freeNote: String = "",
    /** reasoning_effort to send, for services whose models think by default
     *  and accept it; "" sends none (other models reject the field). */
    val reasoningEffort: String = "",
)

object AiPresets {
    const val CLAUDE_DEFAULT_MODEL = "claude-opus-5-5"

    // The free tiers first: each needs only a key, and its default model can
    // call the tools the assistant reads the car with (checked October 2026).
    val ALL = listOf(
        AiPreset("anthropic", "Claude (Anthropic)", "anthropic", "https://api.anthropic.com",
            "console.anthropic.com", defaultModel = CLAUDE_DEFAULT_MODEL),
        AiPreset("gemini", "Google Gemini (free tier)", "openai",
            "https://generativelanguage.googleapis.com/v1beta/openai", "aistudio.google.com",
            defaultModel = "gemini-3.8-flash", reasoningEffort = "low",
            freeNote = "Free, no card needed. On the free tier Google may use what is sent " +
                "to improve its models."),
        AiPreset("groq", "Groq (free tier)", "openai", "https://api.groq.com/openai/v1",
            "console.groq.com", defaultModel = "openai/gpt-oss-120b", reasoningEffort = "low",
            freeNote = "Free, with daily limits."),
        AiPreset("mistral", "Mistral (free Experiment plan)", "openai", "https://api.mistral.ai/v1",
            "console.mistral.ai", defaultModel = "mistral-small-latest",
            freeNote = "Free with a verified phone number. On the free plan Mistral may use " +
                "what is sent to train its models."),
        AiPreset("openai", "OpenAI", "openai", "https://api.openai.com/v1", "platform.openai.com"),
        AiPreset("openrouter", "OpenRouter (many models, one key)", "openai",
            "https://openrouter.ai/api/v1", "openrouter.ai/keys"),
        AiPreset("deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1",
            "platform.deepseek.com"),
        AiPreset("xai", "xAI Grok", "openai", "https://api.x.ai/v1", "console.x.ai"),
        AiPreset("custom", "Other OpenAI-compatible service (https)", "openai", "", ""),
    )

    fun byId(id: String): AiPreset = ALL.firstOrNull { it.id == id } ?: ALL.first()

    /**
     * Whether [id] can be a backup: only the chosen service's model can be
     * set, so a backup runs on its default model and needs one.
     */
    fun canBackUp(id: String): Boolean = ALL.any { it.id == id && it.defaultModel.isNotBlank() }
}

/** The assistant's settings. The API key is held apart, encrypted (KeyVault). */
data class AiSettings(
    val provider: String = "anthropic",
    val model: String = "",
    val baseUrl: String = "",            // custom only
    val effort: String = "low",          // Claude: a quick spoken answer matters most
    val speakAlerts: Boolean = true,     // say trip events out loud
    val wakeWord: Boolean = false,       // always listening for the wake phrase
    val wakePhrase: String = "hey scanner",
    val backups: List<String> = emptyList(),   // tried in order when the service fails
    val disabled: Set<String> = emptySet(),    // turned off: never asked, kept configured
    val sttOffline: Boolean = false,           // recognise on the phone only, where it can
    val ttsVoice: String = "",                 // a TextToSpeech voice name; "" the engine's own
    /** May recognition fall back to the online service when on-the-phone fails? Your consent. */
    val sttOnlineFallback: Boolean = false,
    val memory: Boolean = true,                // follow-up questions know what came before
    val style: String = "auto",                // auto (brief while driving) | brief | normal | detailed
    val speechRate: Float = 1.0f,
    val language: String = "en-GB",            // en-GB | en-US: recognition and speech
    val limits: UsageLimits = UsageLimits(),
    val prices: Map<String, Price> = emptyMap(),   // yours, per million tokens; never assumed
    val historyDays: Int = 30,                 // interaction history kept; 0 keeps none
    val historyText: Boolean = false,          // keep the question's words in it
    val streaming: Boolean = true,             // speak answers as they arrive, where it can
) {
    val preset: AiPreset get() = AiPresets.byId(provider)
    val effectiveModel: String
        get() = model.ifBlank { preset.defaultModel }
    val effectiveBaseUrl: String get() = baseUrl.ifBlank { preset.baseUrl }.trimEnd('/')

    /** The services asked, in order: the chosen one, then the backups, none turned off. */
    val chain: List<String> get() = (listOf(provider) + backups).filter { it !in disabled }

    /**
     * What [id] is asked with: the chosen service's model, address and effort
     * can be set; the others run on their defaults.
     */
    fun askedWith(id: String): List<String> =
        if (id == provider) listOf(effectiveModel, effectiveBaseUrl, effort)
        else AiPresets.byId(id).let { listOf(it.defaultModel, it.baseUrl.trimEnd('/')) }
}

object AiPrefs {
    private const val FILE = "ai"
    val EFFORTS = listOf("low", "medium", "high", "xhigh", "max")

    fun load(context: Context): AiSettings {
        val p = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
        return AiSettings(
            provider = p.getString("provider", "anthropic") ?: "anthropic",
            model = p.getString("model", "") ?: "",
            baseUrl = p.getString("base_url", "") ?: "",
            effort = (p.getString("effort", "low") ?: "low").takeIf { it in EFFORTS } ?: "low",
            speakAlerts = p.getBoolean("speak_alerts", true),
            wakeWord = p.getBoolean("wake_word", false),
            wakePhrase = p.getString("wake_phrase", "hey scanner") ?: "hey scanner",
            backups = cleanBackups(p.getString("backups", "") ?: "",
                p.getString("provider", "anthropic") ?: "anthropic"),
            disabled = (p.getString("disabled", "") ?: "").split(',').map { it.trim() }
                .filter { id -> AiPresets.ALL.any { it.id == id } }.toSet(),
            sttOffline = p.getBoolean("stt_offline", false),
            ttsVoice = p.getString("tts_voice", "") ?: "",
            sttOnlineFallback = p.getBoolean("stt_online_fallback", false),
            memory = p.getBoolean("memory", true),
            style = (p.getString("style", "auto") ?: "auto").takeIf { it in STYLES } ?: "auto",
            speechRate = p.getFloat("speech_rate", 1.0f).coerceIn(0.6f, 1.6f),
            language = (p.getString("language", "en-GB") ?: "en-GB").takeIf { it in LANGUAGES }
                ?: "en-GB",
            limits = UsageLimits(p.getInt("limit_requests", 0).coerceAtLeast(0),
                p.getFloat("limit_spend", 0f).toDouble().coerceAtLeast(0.0)),
            prices = decodePrices(p.getString("prices", "") ?: ""),
            historyDays = p.getInt("history_days", 30).coerceIn(0, 365),
            historyText = p.getBoolean("history_text", false),
            streaming = p.getBoolean("streaming", true),
        )
    }

    /**
     * The saved backup list, kept to services that can be backups, without
     * the chosen service itself and without repeats, in the saved order.
     */
    fun cleanBackups(raw: String, provider: String): List<String> =
        raw.split(',').map { it.trim() }
            .filter { it.isNotEmpty() && it != provider && AiPresets.canBackUp(it) }
            .distinct()

    fun save(context: Context, s: AiSettings) {
        val before = load(context)
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            .putString("provider", AiPresets.byId(s.provider).id)
            .putString("model", s.model.trim())
            .putString("base_url", s.baseUrl.trim())
            .putString("effort", s.effort.takeIf { it in EFFORTS } ?: "low")
            .putBoolean("speak_alerts", s.speakAlerts)
            .putBoolean("wake_word", s.wakeWord)
            .putString("wake_phrase", s.wakePhrase.trim().lowercase().ifBlank { "hey scanner" })
            .putString("backups", cleanBackups(s.backups.joinToString(","), s.provider)
                .joinToString(","))
            .putString("disabled", s.disabled.joinToString(","))
            .putBoolean("stt_offline", s.sttOffline)
            .putString("tts_voice", s.ttsVoice.trim())
            .putBoolean("stt_online_fallback", s.sttOnlineFallback)
            .putBoolean("memory", s.memory)
            .putString("style", s.style.takeIf { it in STYLES } ?: "auto")
            .putFloat("speech_rate", s.speechRate.coerceIn(0.6f, 1.6f))
            .putString("language", s.language.takeIf { it in LANGUAGES } ?: "en-GB")
            .putInt("limit_requests", s.limits.requestsPerDay.coerceAtLeast(0))
            .putFloat("limit_spend", s.limits.spendPerDay.toFloat().coerceAtLeast(0f))
            .putString("prices", encodePrices(s.prices))
            .putInt("history_days", s.historyDays.coerceIn(0, 365))
            .putBoolean("history_text", s.historyText)
            .putBoolean("streaming", s.streaming)
            .apply()
        healthResets(before, load(context)).forEach { ProviderHealth.breaker.reset(it) }
    }

    /**
     * The services whose health starts afresh when [old] becomes [new]: those
     * now asked with another model, address or effort, and those turned back
     * on. Anything else -- speech speed, history, limits -- leaves a refused
     * key or a rest where it was, so a dead service is not asked again just
     * because a setting was touched. (A new key resets its service: setKey.)
     */
    fun healthResets(old: AiSettings, new: AiSettings): Set<String> =
        AiPresets.ALL.map { it.id }.filter { id ->
            old.askedWith(id) != new.askedWith(id) || (id in old.disabled && id !in new.disabled)
        }.toSet()

    val STYLES = listOf("auto", "brief", "normal", "detailed")
    val LANGUAGES = listOf("en-GB", "en-US")

    fun encodePrices(prices: Map<String, Price>): String = org.json.JSONObject().apply {
        prices.forEach { (id, pr) ->
            put(id, org.json.JSONObject().put("in", pr.inPerMillion).put("out", pr.outPerMillion))
        }
    }.toString()

    fun decodePrices(text: String): Map<String, Price> = runCatching {
        val o = org.json.JSONObject(text)
        o.keys().asSequence().mapNotNull { id ->
            val pr = o.optJSONObject(id) ?: return@mapNotNull null
            val i = pr.optDouble("in", -1.0)
            val out = pr.optDouble("out", -1.0)
            if (i < 0 || out < 0) null else id to Price(i, out)
        }.toMap()
    }.getOrDefault(emptyMap())



    /** The API key for [provider], decrypted; "" when none is stored. */
    fun key(context: Context, provider: String): String =
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .getString("key_$provider", null)?.let { KeyVault.decrypt(it) } ?: ""

    fun setKey(context: Context, provider: String, key: String) {
        val edit = context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
        if (key.isBlank()) edit.remove("key_$provider")
        else edit.putString("key_$provider", KeyVault.encrypt(key.trim()))
        edit.apply()
        ProviderHealth.breaker.reset(provider)
    }
}

/**
 * API keys are billed credentials, so they are stored encrypted with AES-GCM
 * under a key that lives in the Android Keystore (hardware-backed where the
 * phone has it) and never leaves it. A key that cannot be decrypted -- e.g.
 * after the keystore was reset -- reads as absent.
 */
object KeyVault {
    private const val ALIAS = "odb_ai_keys"
    private const val TRANSFORM = "AES/GCM/NoPadding"

    private fun secretKey(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(KeyGenParameterSpec.Builder(ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build())
        return gen.generateKey()
    }

    fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val out = cipher.iv + cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(out, Base64.NO_WRAP)
    }

    fun decrypt(stored: String): String? = runCatching {
        val all = Base64.decode(stored, Base64.NO_WRAP)
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(128, all, 0, 12))
        String(cipher.doFinal(all, 12, all.size - 12), Charsets.UTF_8)
    }.getOrNull()
}
