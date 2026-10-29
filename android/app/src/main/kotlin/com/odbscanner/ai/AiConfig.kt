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
            defaultModel = "gemini-3.8-flash",
            freeNote = "Free, no card needed. On the free tier Google may use what is sent " +
                "to improve its models."),
        AiPreset("groq", "Groq (free tier)", "openai", "https://api.groq.com/openai/v1",
            "console.groq.com", defaultModel = "llama-3.3-70b-versatile",
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
) {
    val preset: AiPreset get() = AiPresets.byId(provider)
    val effectiveModel: String
        get() = model.ifBlank { preset.defaultModel }
    val effectiveBaseUrl: String get() = baseUrl.ifBlank { preset.baseUrl }.trimEnd('/')
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
        )
    }

    fun save(context: Context, s: AiSettings) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            .putString("provider", AiPresets.byId(s.provider).id)
            .putString("model", s.model.trim())
            .putString("base_url", s.baseUrl.trim())
            .putString("effort", s.effort.takeIf { it in EFFORTS } ?: "low")
            .putBoolean("speak_alerts", s.speakAlerts)
            .putBoolean("wake_word", s.wakeWord)
            .putString("wake_phrase", s.wakePhrase.trim().lowercase().ifBlank { "hey scanner" })
            .apply()
    }

    /** The API key for [provider], decrypted; "" when none is stored. */
    fun key(context: Context, provider: String): String =
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .getString("key_$provider", null)?.let { KeyVault.decrypt(it) } ?: ""

    fun setKey(context: Context, provider: String, key: String) {
        val edit = context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
        if (key.isBlank()) edit.remove("key_$provider")
        else edit.putString("key_$provider", KeyVault.encrypt(key.trim()))
        edit.apply()
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
