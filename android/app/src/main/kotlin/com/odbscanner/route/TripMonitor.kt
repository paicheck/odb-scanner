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
import com.odbscanner.ai.AiPrefs
import com.odbscanner.ai.Speaker
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
import java.util.UUID
import java.util.Locale

/** A trip being driven: its destination, current plan and what the monitor last saw. */
data class ActiveTrip(
    val destination: Place,
    val mode: String,
    val result: TripResult,
    val plannedAtMs: Long,
    val route: RouteState,                     // the one route state (RouteState.kt)
    val replans: Int = 0,
    val note: String = "Watching the battery against the plan",
    val chargedSincePlan: Boolean = false,
    val lastPct: Double? = null,
    val charging: Boolean = false,
    val announced: Set<String> = emptySet(),   // stops already announced aloud
    val proposed: TripResult? = null,          // the plan route.proposal describes, waiting
    val replanAtMs: Long = plannedAtMs,        // the latest re-plan worked out, applied or not
)

/**
 * Keeps an active trip's plan honest while driving. Started by Start
 * navigation (phone) or Navigate (car screen) -- which also start the
 * in-app Navigator, unless Route settings hand trips to Google Maps -- or by
 * the assistant planning a trip. Every minute it compares the
 * car's latest charge reading -- stored by the collector -- with what the
 * plan expected where the phone is now, and re-plans from here when they
 * drift apart (Replan has the rules). The plan lives in one RouteState with
 * revisions: a re-plan with the same stops just refreshes the figures; one
 * that changes where the car goes is a *proposal* the driver accepts or
 * keeps their plan -- by the notification's buttons, a spoken yes, the
 * planner or the car screen -- unless the driver asked for the re-plan. An
 * applied change that Google Maps does not have is offered to it (it cannot
 * be updated from outside, only handed the trip again).
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

    fun start(context: Context, result: TripResult, picks: Picks = Picks()) {
        val app = context.applicationContext
        val now = System.currentTimeMillis()
        val provider = if (RoutePrefs.load(app).navigateInApp) RouteRevisions.IN_APP
        else RouteRevisions.GOOGLE_MAPS
        // lastPct starts empty: "charging" means one READING above the last
        // one, not above a start charge the driver may have typed.
        _state.value = ActiveTrip(result.destination, result.plan.mode, result, now,
            route = RouteRevisions.start(UUID.randomUUID().toString(), result, provider, now, picks))
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

    /**
     * Drive [result]: watch it and navigate it in the app. False when Route
     * settings say Google Maps navigates instead -- the caller opens it.
     */
    fun drive(context: Context, result: TripResult, picks: Picks = Picks()): Boolean {
        if (_state.value?.result !== result) start(context, result, picks)
        if (!RoutePrefs.load(context).navigateInApp) return false
        Navigator.start(context, result)
        return true
    }

    fun stop() {
        job?.cancel()
        job = null
        _state.value = null
        Navigator.stop()
    }

    /** Re-plan now and wait for it: the trip as it stands afterwards (null: none). */
    suspend fun replan(context: Context): ActiveTrip? {
        if (_state.value == null) return null
        runCatching { check(context.applicationContext, force = true) }
        return _state.value
    }

    /**
     * The driver changed the trip's charger choices: keep them for the rest of
     * the trip and re-plan from here. They asked, so the new plan applies.
     */
    suspend fun changePicks(context: Context, picks: Picks, why: String): ActiveTrip? {
        if (_state.value == null) return null
        update { it.copy(route = it.route.copy(picks = picks)) }
        runCatching { check(context.applicationContext, force = true, why = why) }
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

    private suspend fun check(context: Context, force: Boolean = false,
                              why: String? = null) = lock.withLock {
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
        // Navigating, the Navigator says when the car has arrived (to the metre).
        if (!Navigator.active && plan.distanceKm - at.kmAlong < Replan.ARRIVED_KM &&
            at.offRouteKm < Replan.OFF_ROUTE_KM) {
            notify(context, "Arrived", "Trip to ${trip.destination.label} finished.")
            stop()
            return@withLock
        }
        announceStopAhead(context, trip, at.kmAlong)
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
            force -> why ?: "re-planned on request"
            trip.charging -> "charging finished at " + String.format(Locale.ROOT, "%.0f %%", actual)
            stopIdx != null -> null                 // at a planned charger: wait for it
            else -> Replan.reason(expected, actual, at.offRouteKm)
        }
        // The level (EnRoute: the dashboard's rules) goes in the note; it
        // recommends, and the re-plan below stays the only thing that changes
        // the route -- announced when it does.
        val judged = if (fresh) EnRoute.assess(plan, at.kmAlong, actual, (now - readMs!!) / 1000.0,
            RoutePrefs.load(context).reservePct, trip.chargedSincePlan || trip.charging) else null
        val level = judged?.takeIf { it.level != "normal" }
            ?.let { " · ${it.level.uppercase(Locale.ROOT)}: ${it.action}" } ?: ""
        if (judged?.level == "critical") {
            val key = "critical@${trip.plannedAtMs}"
            if (key !in trip.announced) {
                update { it.copy(announced = it.announced + key) }
                speak(context, judged.action)
            }
        }
        val checked = if (stopIdx != null) "At charging stop ${stopIdx + 1}: " +
            plan.stops[stopIdx].charger.name
        else String.format(Locale.ROOT, "Charge %.0f %%, plan %.0f %% here", actual, expected) + level
        if (reason == null) {
            update { it.copy(lastPct = seen, charging = false, note = checked) }
            return@withLock
        }
        if (!force && now - maxOf(trip.plannedAtMs, trip.replanAtMs) < Replan.MIN_INTERVAL_MS) {
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
                actual.coerceIn(1.0, 100.0), trip.mode, consumption, avoid = trip.result.avoid,
                picks = trip.route.picks)
        } catch (e: Exception) {
            update { it.copy(lastPct = seen, charging = false,
                note = "Could not re-plan (${e.message ?: e.javaClass.simpleName}); keeping the plan") }
            return@withLock
        }
        update { it.copy(replanAtMs = now, lastPct = seen, charging = false) }
        val basedOn = trip.route.revision
        val urgent = judged?.level == "critical" || fresher.plan.status == "unreachable"
        when (RouteRevisions.decide(trip.route, basedOn, fresher, force, urgent)) {
            RouteRevisions.Action.REFRESH -> {
                applyPlan(fresher, basedOn, reason, now)
                if (!force && actual < expected - Replan.DRIFT_PCT) {
                    speak(context, Replan.stillOkSpeech(actual, expected, fresher.plan.arrivalPct))
                }
                return@withLock
            }
            RouteRevisions.Action.APPLY -> {
                val out = applyPlan(fresher, basedOn, reason, now)
                if (out is RouteRevisions.Outcome.Revised) announceApplied(context, fresher, out.change)
                return@withLock
            }
            RouteRevisions.Action.KEEP -> {
                update { it.copy(note = "$checked · keeping your plan") }
                return@withLock
            }
            RouteRevisions.Action.PROPOSE -> Unit
        }
        val proposed = RouteRevisions.propose(trip.route, basedOn, fresher, reason, now, urgent)
            ?: return@withLock
        val p = proposed.proposal!!
        val asked = trip.route.proposal?.keys == p.keys      // already waiting: do not nag
        update { it.copy(route = proposed, proposed = fresher,
            note = "New plan suggested: ${p.change}") }
        if (!asked) {
            notifyProposal(context, p)
            scope.launch { runCatching { askToSwitch(context) } }
        }
    }

    /**
     * Apply [fresher], worked out against revision [basedOn]. Refused (Stale)
     * when another revision came first; the navigator follows only an applied
     * plan.
     */
    private fun applyPlan(fresher: TripResult, basedOn: Int, reason: String,
                          now: Long): RouteRevisions.Outcome {
        val trip = _state.value ?: return RouteRevisions.Outcome.Stale(
            RouteRevisions.start("", fresher, RouteRevisions.IN_APP, now), basedOn)
        val out = RouteRevisions.revise(trip.route, basedOn, fresher, reason, now)
        val next = when (out) {
            is RouteRevisions.Outcome.Stale -> return out
            is RouteRevisions.Outcome.Same -> out.state
            is RouteRevisions.Outcome.Revised -> out.state
        }
        update {
            it.copy(result = fresher, route = next, proposed = null, plannedAtMs = now,
                replans = it.replans + 1, chargedSincePlan = false, note = "Re-planned: $reason",
                announced = if (out is RouteRevisions.Outcome.Revised) emptySet() else it.announced)
        }
        if (Navigator.active) Navigator.planChanged(fresher)
        return out
    }

    /**
     * Say an applied change, and hand it to Google Maps if that is how the
     * trip is navigated and Google Maps does not have it: at once when the
     * driver has just said yes ([handoffNow]) and Android allows it, else by
     * the notification's tap (or a spoken yes).
     */
    private fun announceApplied(context: Context, fresher: TripResult, change: String,
                                handoffNow: Boolean = false) {
        val route = _state.value?.route ?: return
        val toMaps = route.needsHandoff && !Navigator.active && fresher.plan.status != "unreachable"
        if (toMaps && handoffNow && MapsHandoff.canOpenNow(context)) {
            if (MapsHandoff.open(context, fresher)) {
                speak(context, "Switched, and sent to Google Maps.")
                return
            }
        }
        notify(context, "Route updated", change +
            (if (toMaps) " Tap to send it to Google Maps." else ""),
            if (toMaps) MapsHandoff.pendingIntent(context) else null)
        speak(context, "Route updated. " + changeSpeech(fresher))
        if (toMaps && !handoffNow) scope.launch { runCatching { MapsHandoff.offer(context, fresher) } }
    }

    /** Switch to the waiting proposal; what to tell the driver. */
    suspend fun accept(context: Context, byVoice: Boolean = false): String = lock.withLock {
        val app = context.applicationContext
        val trip = _state.value ?: return@withLock "No trip is being watched."
        val p = trip.route.proposal ?: return@withLock "There is no new plan waiting."
        val fresher = trip.proposed ?: return@withLock "There is no new plan waiting."
        when (val out = applyPlan(fresher, p.basedOn, p.reason, System.currentTimeMillis())) {
            is RouteRevisions.Outcome.Stale -> {
                update { it.copy(proposed = null, route = it.route.copy(proposal = null)) }
                "That suggestion was overtaken by a newer plan; nothing changed."
            }
            is RouteRevisions.Outcome.Same -> "The plan already goes that way."
            is RouteRevisions.Outcome.Revised -> {
                announceApplied(app, fresher, out.change, handoffNow = byVoice)
                "Switched: ${out.change}"
            }
        }
    }

    /** Keep the current plan; the same suggestion is not offered again. */
    fun decline(): String {
        update { it.copy(route = RouteRevisions.decline(it.route), proposed = null,
            note = "Keeping your plan") }
        return "Keeping your plan."
    }

    /** Google Maps was handed the trip (revision [revision], or the current one). */
    fun markHandedOff(revision: Int? = null) = update { t ->
        if (revision != null && revision != t.route.revision) t
        else t.copy(route = RouteRevisions.markSent(t.route, System.currentTimeMillis()))
    }

    fun markHandoffFailed(why: String) = update { t ->
        t.copy(route = RouteRevisions.markFailed(t.route, System.currentTimeMillis(), why))
    }

    /** A notification button (TripActionReceiver). */
    fun handleAction(context: Context, action: String?, done: () -> Unit) {
        scope.launch {
            try {
                when (action) {
                    ACTION_ACCEPT -> accept(context.applicationContext)
                    ACTION_KEEP -> decline()
                }
            } finally {
                done()
            }
        }
    }

    /** Ask out loud whether to switch to the proposal; act on the answer. */
    private suspend fun askToSwitch(context: Context) {
        val p = _state.value?.route?.proposal ?: return
        val yes = MapsHandoff.askYes(context, (if (p.urgent) "Your plan no longer works. "
            else "New plan suggested. ") + p.change + " Say yes to switch.") ?: return
        if (_state.value?.route?.proposal != p) return        // answered another way meanwhile
        if (yes) Speaker.say(context, accept(context, byVoice = true))
        else Speaker.say(context, decline())
    }

    private fun notifyProposal(context: Context, p: Proposal) {
        fun action(name: String, code: Int) = PendingIntent.getBroadcast(context, code,
            Intent(context, TripActionReceiver::class.java).setAction(name),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        notify(context, if (p.urgent) "Your plan no longer works" else "New plan suggested",
            p.change + " (" + p.reason + ")", null,
            listOf("Switch" to action(ACTION_ACCEPT, 3), "Keep my plan" to action(ACTION_KEEP, 4)))
    }

    const val ACTION_ACCEPT = "com.odbscanner.trip.ACCEPT"
    const val ACTION_KEEP = "com.odbscanner.trip.KEEP"

    /** A notification whose tap opens Google Maps with [r] (for the assistant). */
    fun offerMapsTap(context: Context, r: TripResult) = notify(context.applicationContext,
        "Send to Google Maps", "Tap to open Google Maps with the current plan: " +
            "${r.plan.stops.size} charging stop(s), then ${r.destination.label}.",
        MapsHandoff.pendingIntent(context.applicationContext))

    /** Say the next charging stop once, a few minutes before it. */
    private fun announceStopAhead(context: Context, trip: ActiveTrip, kmAlong: Double) {
        val stops = trip.result.plan.stops
        val i = stops.indexOfFirst { it.km > kmAlong - 0.5 }
        if (i < 0) return
        val s = stops[i]
        val ahead = s.km - kmAlong
        val key = "${s.charger.name}@${s.km}"
        if (ahead > Replan.ANNOUNCE_STOP_KM || key in trip.announced) return
        update { it.copy(announced = it.announced + key) }
        val road = trip.result.steps.lastOrNull { it.km <= s.km }?.road ?: ""
        speak(context, Replan.stopAheadSpeech(i, s, ahead.coerceAtLeast(0.0), road))
    }

    /** Spoken alerts, when the driver has them on (Assistant settings). */
    private fun speak(context: Context, text: String) {
        if (AiPrefs.load(context).speakAlerts) Speaker.say(context, text)
    }

    private fun changeSpeech(r: TripResult): String {
        val p = r.plan
        val next = p.stops.firstOrNull()
        return when {
            p.status == "unreachable" -> "This charge no longer reaches the destination. " +
                "Check the planner when you can stop."
            next == null -> String.format(Locale.UK,
                "No charging needed now. You arrive with %.0f percent.", p.arrivalPct ?: 0.0)
            else -> String.format(Locale.UK, "Next charging stop: %s, %s. Arrive with %.0f percent.",
                next.charger.name, Replan.milesAhead(next.km).replaceFirst("In", "in"),
                next.arrivePct)
        }
    }

    private fun notify(context: Context, title: String, text: String,
                       tap: PendingIntent? = null,
                       actions: List<Pair<String, PendingIntent>> = emptyList()) {
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
            .setContentIntent(tap ?: open)
            .setAutoCancel(true)
            .apply {
                actions.forEach { (label, intent) -> addAction(0, label, intent) }
                if (tap != null) addAction(0, "Open the planner", open)
            }
            .build()
        runCatching { NotificationManagerCompat.from(context).notify(NOTIFICATION_ID, n) }
    }
}
