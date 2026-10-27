package com.odbscanner.route

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
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
import kotlinx.coroutines.launch

/**
 * The trip watch's foreground service (type location). While a trip is
 * driven Google Maps is in front and this app is in the background; Android
 * gives a background app no position unless it runs a location-type
 * foreground service. This is that service -- it holds no logic, shows
 * "Watching your trip" with the latest note, and stops when the TripMonitor
 * ends (arrival or End trip). The re-planning itself is TripMonitor's.
 */
class TripService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        ensureChannel()
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
            ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else 0
        try {
            ServiceCompat.startForeground(this, NOTIFICATION_ID,
                notification(TripMonitor.state.value), type)
        } catch (e: Exception) {          // no location permission, or not allowed now
            stopSelf()
            return START_NOT_STICKY
        }
        scope.launch {
            TripMonitor.state.collect { trip ->
                if (trip == null) {
                    ServiceCompat.stopForeground(this@TripService, ServiceCompat.STOP_FOREGROUND_REMOVE)
                    stopSelf()
                } else {
                    getSystemService(NotificationManager::class.java)
                        ?.notify(NOTIFICATION_ID, notification(trip))
                }
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun notification(trip: ActiveTrip?): Notification {
        val open = PendingIntent.getActivity(this, 2, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_map)
            .setContentTitle(trip?.let { "Watching your trip to ${it.destination.label}" }
                ?: "Watching your trip")
            .setContentText(trip?.note ?: "")
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
