package com.odbscanner.service

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.odbscanner.MainActivity
import com.odbscanner.db.AppDatabase
import com.odbscanner.db.Repository
import com.odbscanner.spec.Spec
import com.odbscanner.spec.SpecLoader
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.min

/**
 * The "car phone" collector: a foreground service that keeps a live Bluetooth
 * SPP link open with the screen off, behind a persistent notification, and
 * auto-reconnects (with backoff) when the adapter drops or is out of range.
 *
 * All data goes through the normal [Repository], so the activity just reads the
 * database; progress is published on [CollectorStatusBus].
 */
class CollectorService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var repository: Repository
    private var spec: Spec? = null
    private var collectJob: Job? = null

    override fun onCreate() {
        super.onCreate()
        repository = Repository(AppDatabase.get(applicationContext))
        spec = try {
            SpecLoader.load(
                assets.open("spec.json").bufferedReader(Charsets.UTF_8).use { it.readText() },
            )
        } catch (e: Exception) {
            null
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> startCollecting()
            ACTION_STOP -> stopCollecting()
            ACTION_BOOT -> if (CollectorPrefs.autoStart(this)) startCollecting()
        }
        return START_STICKY
    }

    private fun startCollecting() {
        val s = spec
        if (s == null) {
            CollectorStatusBus.setMessage("Failed to load the diagnostic spec")
            stopSelf()
            return
        }
        if (collectJob?.isActive == true) return
        startForegroundCompat()
        CollectorStatusBus.setRunning(true)
        CollectorStatusBus.setMessage("Starting collection…")
        collectJob = scope.launch { collectLoop(s) }
    }

    private suspend fun collectLoop(spec: Spec) {
        var backoff = INITIAL_BACKOFF_MS
        while (true) {
            val device = findAdapter()
            if (device == null) {
                CollectorStatusBus.setMessage(
                    "Waiting for the paired adapter… (retry in ${backoff / 1000}s)",
                )
                delay(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_MS)
                continue
            }
            backoff = INITIAL_BACKOFF_MS
            try {
                runContinuousCollection(
                    spec = spec,
                    device = device,
                    repository = repository,
                    pollIntervalMs = POLL_INTERVAL_MS,
                    onVehicleId = { /* the service writes under the VIN's own row */ },
                    onStatus = { msg ->
                        CollectorStatusBus.setMessage(msg)
                        updateNotification(msg)
                    },
                )
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                CollectorStatusBus.setMessage("Disconnected: ${e.message} — reconnecting")
            }
            delay(backoff)
        }
    }

    /** The remembered adapter, if it is still paired. */
    @SuppressLint("MissingPermission")
    private fun findAdapter(): BluetoothDevice? {
        val address = CollectorPrefs.adapterAddress(this) ?: return null
        val adapter = BluetoothAdapter.getDefaultAdapter() ?: return null
        return adapter.bondedDevices?.firstOrNull { it.address == address }
    }

    private fun stopCollecting() {
        collectJob?.cancel()
        collectJob = null
        CollectorStatusBus.setRunning(false)
        CollectorStatusBus.setMessage("Stopped")
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun startForegroundCompat() {
        val notification = buildNotification("Starting…")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID, notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun updateNotification(message: String) {
        getSystemService(NotificationManager::class.java)
            ?.notify(NOTIFICATION_ID, buildNotification(message))
    }

    private fun buildNotification(message: String): Notification {
        ensureChannel()
        val contentIntent = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("ODB Scanner — collecting")
            .setContentText(message)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(contentIntent)
            .build()
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val channel = NotificationChannel(
            CHANNEL_ID, "Collection", NotificationManager.IMPORTANCE_LOW,
        )
        getSystemService(NotificationManager::class.java)?.createNotificationChannel(channel)
    }

    override fun onDestroy() {
        collectJob?.cancel()
        scope.cancel()
        CollectorStatusBus.setRunning(false)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        const val ACTION_START = "com.odbscanner.service.START"
        const val ACTION_STOP = "com.odbscanner.service.STOP"
        const val ACTION_BOOT = "com.odbscanner.service.BOOT"
        const val CHANNEL_ID = "collector"
        const val NOTIFICATION_ID = 1
        const val POLL_INTERVAL_MS = 5_000L
        const val INITIAL_BACKOFF_MS = 5_000L
        const val MAX_BACKOFF_MS = 60_000L

        fun start(context: Context) {
            ContextCompat.startForegroundService(
                context, Intent(context, CollectorService::class.java).setAction(ACTION_START),
            )
        }

        fun stop(context: Context) {
            context.startService(Intent(context, CollectorService::class.java).setAction(ACTION_STOP))
        }
    }
}
