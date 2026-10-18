package com.odbscanner.net

import android.content.Context
import android.os.Build
import android.provider.Settings

/**
 * Where the phone is told to send its measurements.
 *
 * Stored in SharedPreferences, not BuildConfig, so the URL and token can be
 * changed on a device without rebuilding the APK. The token stored here is a
 * REVOCABLE ingest-API token, never a database password: the phone cannot reach
 * MySQL at all, by design. If a phone is lost the token is rotated on the
 * server and the device is locked out, with no database credential to leak.
 */
data class CloudSettings(
    val enabled: Boolean,
    val apiUrl: String,
    val token: String,
    val sourceId: String,
) {
    /** The one endpoint the app ever calls. */
    val uploadUrl: String get() = apiUrl.trimEnd('/') + "/ingest/measurements"

    val isUsable: Boolean get() = enabled && apiUrl.isNotBlank() && token.isNotBlank()
}

object CloudPrefs {
    private const val FILE = "cloud"
    private const val KEY_ENABLED = "enabled"
    private const val KEY_URL = "api_url"
    private const val KEY_TOKEN = "token"
    private const val KEY_SOURCE = "source_id"

    fun load(context: Context): CloudSettings {
        val prefs = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
        return CloudSettings(
            enabled = prefs.getBoolean(KEY_ENABLED, false),
            apiUrl = prefs.getString(KEY_URL, "") ?: "",
            token = prefs.getString(KEY_TOKEN, "") ?: "",
            sourceId = prefs.getString(KEY_SOURCE, "")
                ?.takeIf { it.isNotBlank() } ?: defaultSourceId(context),
        )
    }

    fun save(context: Context, settings: CloudSettings) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            .putBoolean(KEY_ENABLED, settings.enabled)
            .putString(KEY_URL, settings.apiUrl.trim())
            .putString(KEY_TOKEN, settings.token.trim())
            .putString(KEY_SOURCE, settings.sourceId.trim())
            .apply()
    }

    /**
     * A stable identity for this device in the cloud `sources` table.
     *
     * ANDROID_ID is per-app-signing-key and survives reinstalls on the same
     * device, which is exactly what a "which phone collected this" label needs.
     * It is not a secret and not used for authentication.
     */
    fun defaultSourceId(context: Context): String {
        val id = Settings.Secure.getString(
            context.contentResolver, Settings.Secure.ANDROID_ID,
        ) ?: Build.MODEL
        val tail = id.takeLast(8)
        return "android:${Build.MODEL}-$tail"
    }
}
