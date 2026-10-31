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

    /** The trip for Google Maps from [from] (see [link]). */
    fun intent(r: TripResult, from: Pair<Double, Double>? = null): Intent {
        val url = Uri.parse(link(r, from).url)
        return Intent(Intent.ACTION_VIEW, url).setPackage(MAPS_PACKAGE)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    /**
     * What a notification's tap does: HandoffActivity hands Google Maps the
     * trip's *current* revision (never one a newer plan has replaced) and
     * records that it did.
     */
    fun pendingIntent(context: Context): PendingIntent =
        PendingIntent.getActivity(context, 2, Intent(context, HandoffActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    /**
     * Open Google Maps with [r] from where the phone is now, and record which
     * revision it was given; false when it would not open (not installed, or
     * Android refused). Without Google Maps the link opens in the browser.
     */
    fun open(context: Context, r: TripResult): Boolean {
        val here = PhoneLocation.lastKnown(context)?.let { it.latitude to it.longitude }
        val maps = intent(r, here)
        val opened = runCatching { context.startActivity(maps) }.recoverCatching {
            context.startActivity(Intent(Intent.ACTION_VIEW, maps.data)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }.isSuccess
        val trip = TripMonitor.state.value
        if (trip != null && RouteRevisions.waypoints(r.plan, r.destination).map { it.key } ==
            trip.route.current.keys) {
            if (opened) TripMonitor.markHandedOff(trip.route.revision)
            else TripMonitor.markHandoffFailed("Google Maps did not open")
        }
        return opened
    }

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
     * Ask [question] out loud and listen for a yes: true, false, or null when
     * nothing was heard -- or nothing asked: only with spoken alerts on, and
     * not over a conversation with the assistant. The wake word lets go of
     * the microphone meanwhile.
     */
    suspend fun askYes(context: Context, question: String): Boolean? {
        if (!AiPrefs.load(context).speakAlerts) return null
        if (VoiceAssistant.state.value != VoiceAssistant.State.IDLE) return null
        withContext(Dispatchers.Main) { WakeWordService.pause() }
        try {
            Speaker.sayAndWait(context, question)
            VoiceAssistant.beep()
            val heard = runCatching { Listener.listenOnce(context, LISTEN_MS) }.getOrNull()
                ?: return null
            return isYes(heard)
        } finally {
            withContext(Dispatchers.Main + NonCancellable) { WakeWordService.resume() }
        }
    }

    /** Ask out loud whether to send [r] to Google Maps, and do it on a yes. */
    suspend fun offer(context: Context, r: TripResult) {
        when (askYes(context, "Say yes to send the new route to Google Maps.")) {
            null -> Speaker.say(context, "Tap the Route updated notification to send it.")
            false -> Speaker.say(context, "OK. Google Maps keeps its route; " +
                "the notification has the new one.")
            true -> if (canOpenNow(context)) {
                val opened = withContext(Dispatchers.Main) { open(context, r) }
                Speaker.say(context, if (opened) "Sent to Google Maps."
                else "Google Maps did not open. Tap the Route updated notification.")
            } else {
                Speaker.say(context, "Android needs a tap for that: tap the Route updated " +
                    "notification. To send it by voice next time, allow Display over other " +
                    "apps in Route settings.")
            }
        }
    }
}
