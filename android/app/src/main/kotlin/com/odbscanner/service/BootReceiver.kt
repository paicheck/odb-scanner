package com.odbscanner.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Starts collection on boot when the user has enabled it, so a phone left in
 * the car begins logging as soon as it powers up. The service still needs the
 * paired adapter to be present and in range.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED && CollectorPrefs.autoStart(context)) {
            CollectorService.start(context)
        }
    }
}
