package com.odbscanner.route

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Bundle

/** The "Switch" and "Keep my plan" buttons on a suggested-plan notification. */
class TripActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val pending = goAsync()
        TripMonitor.handleAction(context, intent.action) { pending.finish() }
    }
}

/**
 * Opened by a notification's tap: hands Google Maps the trip's current
 * revision from where the phone is, records it (TripMonitor.markHandedOff),
 * and closes. A notification tap may open an activity; this is that
 * activity, so the tap -- not a background start -- opens Google Maps.
 */
class HandoffActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        TripMonitor.state.value?.let { MapsHandoff.open(this, it.result) }
        finish()
    }
}
