package com.odbscanner.service

import android.content.Context
import android.content.SharedPreferences

/**
 * Small preferences for the "car phone" mode: which adapter to auto-connect to,
 * and whether to start collecting on boot. Lives in SharedPreferences so it
 * survives app restarts without touching the Room schema.
 */
object CollectorPrefs {
    private const val NAME = "collector"
    private const val KEY_ADAPTER = "adapter_address"
    private const val KEY_AUTO_START = "auto_start"
    private const val KEY_COLLECTING = "collecting"

    private fun prefs(context: Context): SharedPreferences =
        context.getSharedPreferences(NAME, Context.MODE_PRIVATE)

    fun adapterAddress(context: Context): String? =
        prefs(context).getString(KEY_ADAPTER, null)

    fun setAdapter(context: Context, address: String) {
        prefs(context).edit().putString(KEY_ADAPTER, address).apply()
    }

    fun autoStart(context: Context): Boolean =
        prefs(context).getBoolean(KEY_AUTO_START, false)

    fun setAutoStart(context: Context, value: Boolean) {
        prefs(context).edit().putBoolean(KEY_AUTO_START, value).apply()
    }

    /**
     * Whether collection is meant to be running. Set on start, cleared only
     * by an explicit stop, so a START_STICKY restart after the system killed
     * the process -- which delivers a null intent -- knows to resume.
     */
    fun collecting(context: Context): Boolean =
        prefs(context).getBoolean(KEY_COLLECTING, false)

    fun setCollecting(context: Context, value: Boolean) {
        prefs(context).edit().putBoolean(KEY_COLLECTING, value).apply()
    }
}
