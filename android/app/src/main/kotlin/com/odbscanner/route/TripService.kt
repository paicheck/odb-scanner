package com.odbscanner.route

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.odbscanner.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch

/**
 * The trip's foreground service (type location). While a trip is driven the
 * screen may be off, or Google Maps in front; Android gives a background app
 * no position unless it runs a location-type foreground service. This is
 * that service -- it holds no logic, shows the next turn while navigating
 * (else "Watching your trip" with the latest note), and stops when the
 * TripMonitor ends (arrival or End trip). The re-planning is TripMonitor's,
 * the guidance Navigator's.
 */
class TripService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        ensureChannel()
        val location = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
            ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else 0
        // The microphone too, when it is allowed: a re-plan asks "send it to
        // Google Maps?" while Google Maps is in front (MapsHandoff), and a
        // background app hears nothing without it. Android shows the
        // microphone in use only while it listens.
        val mic = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && ContextCompat.checkSelfPermission(
                this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED)
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
        val started = listOf(location or mic, location).distinct().any { type ->
            runCatching {
                ServiceCompat.startForeground(this, NOTIFICATION_ID,
                    notification(TripMonitor.state.value, null), type)
            }.isSuccess
        }
        if (!started) {                   // no location permission, or not allowed now
            stopSelf()
            return START_NOT_STICKY
        }
        scope.launch {
            // The next turn changes the text a few times a minute, not every fix.
            val turn = Navigator.state.map { it?.let(::turnLine) }.distinctUntilChanged()
            combine(TripMonitor.state, turn) { trip, nav -> trip to nav }.collect { (trip, nav) ->
                if (trip == null) {
                    ServiceCompat.stopForeground(this@TripService, ServiceCompat.STOP_FOREGROUND_REMOVE)
                    stopSelf()
                } else {
                    getSystemService(NotificationManager::class.java)
                        ?.notify(NOTIFICATION_ID, notification(trip, nav))
                }
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    /** "↰ 0.3 mi · Take exit 8 on the left towards the M40" -- or what the Navigator is doing. */
    private fun turnLine(s: Navigator.State): String {
        val g = s.guidance
        val next = g?.next
        return when {
            s.phase == Navigator.Phase.DRIVING && next != null && !g.offRoute ->
                "${NavText.arrow(next)} ${NavText.distance(g.toNextKm)} · ${next.instruction}"
            else -> s.note.ifBlank { "Navigating to ${s.target?.label ?: s.trip.destination.label}" }
        }
    }

    private fun notification(trip: ActiveTrip?, turn: String?): Notification {
        val open = PendingIntent.getActivity(this, 2, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_map)
            .setContentTitle(trip?.let {
                if (turn != null) "Navigating to ${it.destination.label}"
                else "Watching your trip to ${it.destination.label}"
            } ?: "Watching your trip")
            .setContentText(turn ?: trip?.note ?: "")
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(open)
            .build()
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        getSystemService(NotificationManager::class.java)?.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Trip watch", NotificationManager.IMPORTANCE_LOW))
    }

    companion object {
        private const val CHANNEL_ID = "trip_watch"
        private const val NOTIFICATION_ID = 4712

        /** Start the watch; false when Android does not allow it now. */
        fun start(context: Context): Boolean = try {
            ContextCompat.startForegroundService(context, Intent(context, TripService::class.java))
            true
        } catch (e: Exception) {          // e.g. ForegroundServiceStartNotAllowedException
            false
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, TripService::class.java))
        }
    }
}
