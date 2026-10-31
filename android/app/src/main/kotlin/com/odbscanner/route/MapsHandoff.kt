package com.odbscanner.route

import android.app.ActivityManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import com.odbscanner.ai.AiPrefs
import com.odbscanner.ai.Listener
import com.odbscanner.ai.Speaker
import com.odbscanner.ai.VoiceAssistant
import com.odbscanner.ai.WakeWordService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext

/**
 * Getting a changed plan into Google Maps. Google Maps navigates on its own
 * once it has the trip, and no other app can change a route it is driving;
 * it can only be handed a new one. And Android lets an app in the background
 * bring Google Maps to the front only from a tap on that app's notification,
 * or once the driver has allowed it to "display over other apps".
 *
 * So when a re-plan changes the stops while trips go to Google Maps:
 *  * the "Route updated" notification opens Google Maps with the new stops
 *    when tapped -- that always works;
 *  * the phone says so and asks; a "yes" sends it -- straight away when
 *    Android allows it, else it says to tap the notification.
 *
 * Nothing changes Google Maps without the driver's say-so.
 */
object MapsHandoff {
    const val MAPS_PACKAGE = "com.google.android.apps.maps"
    const val LISTEN_MS = 7_000L

    private val NO = setOf("no", "nope", "don't", "dont", "not", "cancel", "keep", "leave",
        "stop", "later", "wait")
    private val YES = setOf("yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please",
        "update", "send", "go", "do", "fine", "correct", "right")

    /** Is what was heard a yes? A no-word anywhere wins. Pure, so it is unit-tested. */
    fun isYes(heard: String?): Boolean {
        val words = (heard ?: return false).lowercase()
            .replace(Regex("[^a-z' ]"), " ").split(' ').filter { it.isNotBlank() }
        if (words.isEmpty() || words.any { it in NO }) return false
        return words.any { it in YES }
    }

    /**
     * The trip for Google Maps from [from] (the car's position now, so
     * Google Maps navigates rather than previews; else the trip's start):
     * each charging stop, then the destination, keeping off the roads the
     * trip keeps off.
     */
    fun link(r: TripResult, from: Pair<Double, Double>? = null): GoogleMaps.Handoff =
        GoogleMaps.tripLink(from ?: (r.origin.lat to r.origin.lon),
            r.plan.stops.map { it.charger.lat to it.charger.lon },
            r.destination.lat to r.destination.lon, r.avoid)

    /** The whole trip for Google Maps: each charging stop, then the destination. */
    fun intent(r: TripResult): Intent {
        val url = Uri.parse(link(r).url)
        return Intent(Intent.ACTION_VIEW, url).setPackage(MAPS_PACKAGE)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    /** What a tap on a notification does: open Google Maps with [r]. */
    fun pendingIntent(context: Context, r: TripResult): PendingIntent =
        PendingIntent.getActivity(context, 2, intent(r),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    /** Whether Android lets this app open Google Maps now, without a tap. */
    fun canOpenNow(context: Context): Boolean =
        Settings.canDrawOverlays(context) || inForeground()

    private fun inForeground(): Boolean = ActivityManager.RunningAppProcessInfo().also {
        ActivityManager.getMyMemoryState(it)
    }.importance == ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND

    /** Settings' "Display over other apps" page for this app. */
    fun permissionIntent(context: Context): Intent =
        Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:${context.packageName}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

    /**
     * Ask out loud whether to send [r] to Google Maps, and do it on a yes.
     * Only with spoken alerts on, and not over a conversation with the
     * assistant; the wake word lets go of the microphone meanwhile.
     */
    suspend fun offer(context: Context, r: TripResult) {
        if (!AiPrefs.load(context).speakAlerts) return
        if (VoiceAssistant.state.value != VoiceAssistant.State.IDLE) return
        withContext(Dispatchers.Main) { WakeWordService.pause() }
        try {
            Speaker.sayAndWait(context, "Say yes to send the new route to Google Maps.")
            VoiceAssistant.beep()
            val heard = runCatching { Listener.listenOnce(context, LISTEN_MS) }.getOrNull()
            when {
                heard == null -> Speaker.say(context,
                    "I didn't hear an answer. Tap the Route updated notification to send it.")
                !isYes(heard) -> Speaker.say(context, "OK. Google Maps keeps its route; " +
                    "the notification has the new one.")
                canOpenNow(context) -> {
                    val opened = withContext(Dispatchers.Main) {
                        runCatching { context.startActivity(intent(r)) }.isSuccess
                    }
                    Speaker.say(context, if (opened) "Sent to Google Maps."
                    else "Google Maps did not open. Tap the Route updated notification.")
                }
                else -> Speaker.say(context, "Android needs a tap for that: tap the Route " +
                    "updated notification. To send it by voice next time, allow Display over " +
                    "other apps in Route settings.")
            }
        } finally {
            withContext(Dispatchers.Main + NonCancellable) { WakeWordService.resume() }
        }
    }
}
