package com.odbscanner.route

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.odbscanner.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.time.Instant
import java.util.Locale

/** A trip being driven: its destination, current plan and what the monitor last saw. */
data class ActiveTrip(
    val destination: Place,
    val mode: String,
    val result: TripResult,
    val plannedAtMs: Long,
    val replans: Int = 0,
    val note: String = "Watching the battery against the plan",
    val chargedSincePlan: Boolean = false,
    val lastPct: Double? = null,
    val charging: Boolean = false,
)

/**
 * Keeps an active trip's plan honest while driving. Started by Start
 * navigation (phone) or Navigate (car screen); every minute it compares the
 * car's latest charge reading -- stored by the collector -- with what the
 * plan expected where the phone is now, and re-plans from here when they
 * drift apart (Replan has the rules). A changed plan is announced in a
 * notification; the phone's planner and the car screen show it at once.
 *
 * It reads only the phone's own database and position; nothing here talks to
 * the car. It runs while the app's process does -- in the car that is kept
 * alive by the collector service and Android Auto.
 */
object TripMonitor {
    private const val CHECK_MS = 60_000L
    private const val CHANNEL_ID = "trip_updates"
    private const val NOTIFICATION_ID = 4711

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val lock = Mutex()
    private var job: Job? = null
    private val _state = MutableStateFlow<ActiveTrip?>(null)
    val state: StateFlow<ActiveTrip?> = _state

    fun start(context: Context, result: TripResult) {
        val app = context.applicationContext
        // lastPct starts empty: "charging" means one READING above the last
        // one, not above a start charge the driver may have typed.
        _state.value = ActiveTrip(result.destination, result.plan.mode, result,
            System.currentTimeMillis())
        // The location-type foreground service lets the phone still read its
        // position while Google Maps is in front.
        if (PhoneLocation.permitted(app)) TripService.start(app)
        job?.cancel()
        job = scope.launch {
            while (isActive) {
                delay(CHECK_MS)
                runCatching { check(app) }
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
        _state.value = null
    }

    /** Re-plan now and wait for it: the trip as it stands afterwards (null: none). */
    suspend fun replan(context: Context): ActiveTrip? {
        if (_state.value == null) return null
        runCatching { check(context.applicationContext, force = true) }
        return _state.value
    }

    /** Re-plan now, from here, on the driver's tap. */
    fun replanNow(context: Context) {
        val app = context.applicationContext
        scope.launch { runCatching { check(app, force = true) } }
    }

    private fun update(f: (ActiveTrip) -> ActiveTrip) {
        _state.value?.let { _state.value = f(it) }
    }

    private suspend fun check(context: Context, force: Boolean = false) = lock.withLock {
        val trip = _state.value ?: return@withLock
        val now = System.currentTimeMillis()
        // Approximate location is ~2 km off and refreshed every ~10 min: not
        // enough to follow a trip, so without precise location only an
        // on-request re-plan runs (from the approximate position).
        if (!force && !PhoneLocation.precise(context)) {
            update { it.copy(note = "Turn on precise location to re-plan automatically") }
            return@withLock
        }
        val here = PhoneLocation.current(context,
            maxAgeMs = if (PhoneLocation.precise(context)) 3 * 60_000L else 12 * 60_000L) ?: run {
            update { it.copy(note = "No recent position from the phone") }
            return@withLock
        }
        val plan = trip.result.plan
        val at = Replan.progress(trip.result.line, plan.distanceKm, here.latitude, here.longitude)
        if (plan.distanceKm - at.kmAlong < Replan.ARRIVED_KM && at.offRouteKm < Replan.OFF_ROUTE_KM) {
            notify(context, "Arrived", "Trip to ${trip.destination.label} finished.")
            stop()
            return@withLock
        }
        val expected = Replan.expectedPct(plan.profile, at.kmAlong) ?: plan.startPct
        val car = CarEnergy.load(context)
        val readMs = car.startTs?.let { runCatching { Instant.parse(it).toEpochMilli() }.getOrNull() }
        val fresh = readMs != null && now - readMs <= Replan.READING_MAX_AGE_MS
        if (!fresh && !force) {
            update { it.copy(note = "Waiting for a fresh battery reading from the car") }
            return@withLock
        }
        // Without a fresh reading (forced), assume the plan is on track here.
        val actual = if (fresh) car.startPct else expected
        val seen = if (fresh) actual else trip.lastPct       // only readings are "seen"
        val rising = trip.lastPct != null && actual > trip.lastPct + Replan.RISING_PCT
        if (rising && !force) {
            update { it.copy(lastPct = seen, charging = true, chargedSincePlan = true,
                note = String.format(Locale.ROOT, "Charging: %.0f %%", actual)) }
            return@withLock
        }
        val stopIdx = Replan.atStop(plan.stops.map { it.km }, at.kmAlong)
        val reason = when {
            force -> "re-planned on request"
            trip.charging -> "charging finished at " + String.format(Locale.ROOT, "%.0f %%", actual)
            stopIdx != null -> null                 // at a planned charger: wait for it
            else -> Replan.reason(expected, actual, at.offRouteKm)
        }
        val checked = if (stopIdx != null) "At charging stop ${stopIdx + 1}: " +
            plan.stops[stopIdx].charger.name
        else String.format(Locale.ROOT, "Charge %.0f %%, plan %.0f %% here", actual, expected)
        if (reason == null) {
            update { it.copy(lastPct = seen, charging = false, note = checked) }
            return@withLock
        }
        if (!force && now - trip.plannedAtMs < Replan.MIN_INTERVAL_MS) {
            update { it.copy(lastPct = seen, note = checked) }   // keeps a pending "charged"
            return@withLock
        }
        val consumption = Replan.correctedConsumption(
            trip.result.consumptionKwhPer100km, plan.startPct, expected, actual,
            trip.chargedSincePlan || trip.charging, at.kmAlong, plan.stops.firstOrNull()?.km,
        ) ?: trip.result.consumptionKwhPer100km
        val fresher = try {
            TripPlanner.plan(context, trip.destination,
                Place("Your location", here.latitude, here.longitude),
                actual.coerceIn(1.0, 100.0), trip.mode, consumption)
        } catch (e: Exception) {
            update { it.copy(lastPct = seen, charging = false,
                note = "Could not re-plan (${e.message ?: e.javaClass.simpleName}); keeping the plan") }
            return@withLock
        }
        val before = plan.stops.map { it.charger.name }
        val after = fresher.plan.stops.map { it.charger.name }
        update {
            it.copy(result = fresher, plannedAtMs = now, replans = it.replans + 1,
                lastPct = seen, charging = false, chargedSincePlan = false,
                note = "Re-planned: $reason")
        }
        if (before != after || fresher.plan.status == "unreachable") {
            notify(context, "Route updated", changeText(fresher, reason))
        }
    }

    private fun changeText(r: TripResult, reason: String): String {
        val p = r.plan
        val next = p.stops.firstOrNull()
        return when {
            p.status == "unreachable" -> "Not reachable on this charge ($reason). Open the planner."
            next == null -> String.format(Locale.ROOT,
                "No charging needed now: arrive with %.0f %% (%s).", p.arrivalPct ?: 0.0, reason)
            else -> String.format(Locale.ROOT, "Next stop %s at %.0f km, arrive %.0f %% (%s).",
                next.charger.name, next.km, next.arrivePct, reason)
        }
    }

    private fun notify(context: Context, title: String, text: String) {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(
                context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return                                    // the planner still shows it
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.getSystemService(NotificationManager::class.java)?.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Trip updates", NotificationManager.IMPORTANCE_HIGH))
        }
        val open = PendingIntent.getActivity(context, 1, Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_map)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        runCatching { NotificationManagerCompat.from(context).notify(NOTIFICATION_ID, n) }
    }
}
