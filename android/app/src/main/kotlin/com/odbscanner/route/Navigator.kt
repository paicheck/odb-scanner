package com.odbscanner.route

import android.annotation.SuppressLint
import android.content.Context
import android.location.Location
import android.location.LocationManager
import androidx.core.content.ContextCompat
import androidx.core.location.LocationListenerCompat
import androidx.core.location.LocationManagerCompat
import androidx.core.location.LocationRequestCompat
import com.odbscanner.ai.Speaker
import com.odbscanner.ai.VoiceAssistant
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.util.Locale
import kotlin.math.max
import kotlin.math.roundToLong

/**
 * Turn-by-turn navigation in the app, for the trip being driven: one leg at
 * a time -- to the next charging stop, then on -- with spoken turns and lane
 * advice, a new route when the driver leaves this one, and, with a Mapbox
 * token, a traffic check every five minutes that takes a clearly faster way
 * and warns of heavy traffic ahead.
 *
 * Position: the phone's GPS once a second while navigating; with the screen
 * off it keeps coming because the trip's TripService (location type) runs.
 * Route requests send the position and the next stop or destination to
 * Mapbox (or OpenRouteService), nothing else. Nothing here talks to the car.
 * Everything runs on the main thread.
 */
object Navigator {
    const val TRAFFIC_CHECK_MS = 5 * 60_000L
    const val REROUTE_MIN_MS = 15_000L
    const val RETRY_MS = 30_000L
    const val TRAFFIC_WARN_KM = 3.0
    const val FASTER_MIN_S = 300.0          // a different way must save 5 minutes...
    const val FASTER_FRACTION = 0.10        // ...and a tenth of the time left
    const val LEAVE_STOP_KM = 0.25          // this far from the charger: driving on
    const val ANNOUNCE_DELAY_S = 300.0      // say what traffic adds from this much
    const val STOP_OVERHEAD_S = 600.0       // parking, plugging in (as PlanSettings)

    enum class Phase { ROUTING, DRIVING, AT_STOP, ARRIVED }

    /** Where a leg goes: a planned charging stop, or the destination ([stop] null). */
    data class Target(val label: String, val lat: Double, val lon: Double, val stop: Stop?)

    data class State(
        val trip: TripResult,
        val targets: List<Target>,           // stops still to reach, then the destination
        val leg: Int,                        // the target this route goes to
        val phase: Phase,
        val route: NavRoute? = null,
        val guidance: GuidanceState? = null,
        val note: String = "",               // "Finding the route", "Re-routing", ...
        val muted: Boolean = false,
        val avoid: Avoid = Avoid(),
        val fix: Pair<Double, Double>? = null,  // the raw position, for the map when off route
        val speedKmh: Double = 0.0,
        val stopHere: Target? = null,        // the charger the car is at (AT_STOP)
        val traffic: Boolean = false,        // this route came with live traffic
    ) {
        val target: Target? get() = targets.getOrNull(leg)
        val lastLeg: Boolean get() = leg >= targets.lastIndex

        /** Seconds to the destination: this leg, then the plan's charging and driving. */
        val toEndS: Double? get() {
            val left = guidance?.remainingS ?: return null
            val stop = target?.stop ?: return left
            val p = trip.plan
            val later = targets.drop(leg).mapNotNull { it.stop }
            val drive = if (p.distanceKm > 0) p.driveS * max(0.0, p.distanceKm - stop.km) / p.distanceKm
            else 0.0
            return left + later.sumOf { it.chargeMin * 60.0 + STOP_OVERHEAD_S } + drive
        }
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val _state = MutableStateFlow<State?>(null)
    val state: StateFlow<State?> = _state

    /** The phone should show the navigation screen (set on Start, cleared on its Back). */
    val showOnPhone = MutableStateFlow(false)

    /** What navigation said lately, newest last: for Repeat (and the device test). */
    val said = MutableStateFlow<List<String>>(emptyList())

    private var app: Context? = null
    private var guidance: Guidance? = null
    private var listener: LocationListenerCompat? = null
    private var routing: Job? = null
    private var checker: Job? = null
    private var routedAtMs = 0L
    private var lastFix: Location? = null
    private val warned = mutableListOf<Pair<Double, Double>>()   // where announced jams start
    private val legsAnnounced = HashSet<Int>()     // "traffic adds N minutes", once a leg

    val active: Boolean get() = _state.value != null

    private fun update(f: (State) -> State) {
        _state.value?.let { _state.value = f(it) }
    }

    /** Navigate the trip from where the phone is. Replaces any navigation running. */
    fun start(context: Context, trip: TripResult) {
        val ctx = context.applicationContext
        val s = RoutePrefs.load(ctx)
        _state.value = State(trip, targetsOf(trip), 0, Phase.ROUTING, note = "Finding the route",
            muted = !s.voiceGuidance, avoid = trip.avoid)
        scope.launch {
            clear()
            app = ctx
            startUpdates(ctx)
            val stops = trip.plan.stops.size
            speak(when (stops) {
                0 -> "Starting navigation to ${trip.destination.label}."
                1 -> "Starting navigation to ${trip.destination.label}, with one charging stop, " +
                    "${trip.plan.stops[0].charger.name}."
                else -> "Starting navigation to ${trip.destination.label}, with $stops charging stops."
            })
            // Straight there, planned moments ago: the planned way is this leg's
            // route (Guidance re-routes if the car is not on it).
            val planned = trip.nav?.takeIf {
                stops == 0 && System.currentTimeMillis() - (TripMonitor.state.value?.plannedAtMs ?: 0) <
                    10 * 60_000L
            }
            if (planned != null) use(planned, "") else route("Finding the route")
            checker = scope.launch {
                while (isActive) {
                    delay(TRAFFIC_CHECK_MS)
                    runCatching { checkTraffic(announce = true) }
                }
            }
        }
    }

    fun stop() {
        scope.launch {
            clear()
            _state.value = null
            showOnPhone.value = false
        }
    }

    private fun clear() {
        stopUpdates()
        routing?.cancel()
        checker?.cancel()
        routing = null
        checker = null
        guidance = null
        lastFix = null
        warned.clear()
        legsAnnounced.clear()
        said.value = emptyList()
    }

    /** Say the current instruction again (a tap on it): the turn ahead, else the last words. */
    fun repeat() {
        scope.launch {
            val s = _state.value ?: return@launch
            val ctx = app ?: return@launch
            val g = s.guidance
            val next = g?.next
            val text = if (s.phase == Phase.DRIVING && next != null && !g.offRoute) {
                val lanes = NavSpeech.laneAdvice(g.lanes)?.let { " $it" } ?: ""
                NavSpeech.lead(g.toNextKm, next.instruction) + lanes
            } else said.value.lastOrNull() ?: s.note.takeIf { it.isNotBlank() } ?: return@launch
            Speaker.say(ctx, text, interrupt = true)                  // even when muted: asked for
        }
    }

    fun setMuted(muted: Boolean) {
        update { it.copy(muted = muted) }
        if (muted) Speaker.stop()
    }

    /** Re-route from here, optionally keeping off other roads from now on. */
    fun reroute(avoid: Avoid? = null) {
        scope.launch {
            if (avoid != null) update { it.copy(avoid = avoid) }
            if (_state.value?.phase in setOf(Phase.ROUTING, Phase.DRIVING)) {
                route("Re-routing on request", force = true)
            }
        }
    }

    /**
     * The trip watch re-planned (from here, so its stops are all ahead): on to
     * its first stop, re-routing only when that is not where this leg goes.
     */
    fun planChanged(trip: TripResult) {
        scope.launch {
            val s = _state.value ?: return@launch
            val targets = targetsOf(trip)
            val same = s.target?.let { near(it, targets[0]) } == true
            _state.value = s.copy(trip = trip, targets = targets, leg = 0)
            // At a charger, driving off routes the next leg; arrived: nothing to do.
            if (s.phase == Phase.DRIVING || s.phase == Phase.ROUTING) {
                if (!same) route("New plan: on to ${targets[0].label}", force = true)
            }
        }
    }

    private fun targetsOf(trip: TripResult): List<Target> =
        trip.plan.stops.map { Target(it.charger.name, it.charger.lat, it.charger.lon, it) } +
            Target(trip.destination.label, trip.destination.lat, trip.destination.lon, null)

    private fun near(a: Target, b: Target) =
        Planner.haversineKm(a.lat, a.lon, b.lat, b.lon) < 0.1

    // -- routes -------------------------------------------------------------------------

    private class Found(val routes: List<NavRoute>, val traffic: Boolean, val note: String)

    /** Ways from [from] to [to]: Mapbox in today's traffic, else (or if it fails) ORS. */
    private fun fetch(context: Context, from: Location, to: Target, avoid: Avoid,
                      alternatives: Boolean): Found {
        val s = RoutePrefs.load(context)
        val ends = listOf(from.latitude to from.longitude, to.lat to to.lon)
        var note = ""
        if (s.mapboxToken.isNotBlank()) {
            try {
                return Found(MapboxRouting(s.mapboxToken).routes(ends, avoid, alternatives), true, "")
            } catch (e: RoutingException) {
                if (s.orsKey.isBlank()) throw e
                note = "No live traffic: ${e.message}"
            }
        }
        val r = RouteServices(s.orsKey, s.ocmKey).routeVia(ends, avoid)
        return Found(listOf(OrsNav.route(r, r.legEndsKm)), false, note)
    }

    /** A route for this leg from the latest position; the old one guides meanwhile. */
    private fun route(note: String, force: Boolean = false) {
        if (routing?.isActive == true && !force) return
        routing?.cancel()
        routedAtMs = System.currentTimeMillis()
        update { it.copy(note = note) }
        routing = scope.launch {
            val ctx = app ?: return@launch
            val here = lastFix?.takeIf { System.currentTimeMillis() - it.time < 60_000L }
                ?: PhoneLocation.current(ctx, maxAgeMs = 60_000L)
            if (here == null) {
                update { it.copy(note = "Waiting for the GPS") }
                return@launch                               // the next fix asks again
            }
            val s = _state.value ?: return@launch
            val target = s.target ?: return@launch
            try {
                val found = withContext(Dispatchers.IO) { fetch(ctx, here, target, s.avoid, false) }
                // The plan moved on to somewhere else meanwhile (a re-plan that
                // keeps the same next stop still wants this route).
                if (_state.value?.target?.let { near(it, target) } != true) return@launch
                use(found.routes.first(), found.note, traffic = found.traffic)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e                                     // replaced by a newer request
            } catch (e: Exception) {
                update { it.copy(note = "No route yet (${e.message ?: e.javaClass.simpleName}); " +
                    "trying again") }
            }
        }
    }

    /** Follow [route] from now. [quietUntilKm]: same roads as before, nothing new to say. */
    private fun use(route: NavRoute, note: String, quietUntilKm: Double = -1.0,
                    traffic: Boolean = route.typicalDurationS != null) {
        guidance = Guidance(route, quietUntilKm)
        val s = _state.value ?: return
        _state.value = s.copy(route = route, phase = Phase.DRIVING, traffic = traffic, guidance = null,
            note = if (listener == null) "Turn-by-turn needs precise location (allow it for this app)"
            else note)
        val delayS = route.trafficDelayS ?: 0.0
        if (delayS >= ANNOUNCE_DELAY_S && legsAnnounced.add(s.leg)) {
            speak("Traffic adds about ${minutes(delayS)} to this ${if (s.lastLeg) "trip" else "leg"}.")
        }
        lastFix?.let { onFix(it) }
    }

    /**
     * With live traffic: is there a way now clearly faster than this one? Take it
     * (and say so). The same roads again only freshen the times and traffic.
     * The answer, in words, for the assistant.
     */
    suspend fun checkTraffic(announce: Boolean): String = withContext(Dispatchers.Main.immediate) {
        val s = _state.value ?: return@withContext "Not navigating."
        val ctx = app ?: return@withContext "Not navigating."
        if (RoutePrefs.load(ctx).mapboxToken.isBlank()) {
            return@withContext "No live traffic: add a Mapbox token in Route settings."
        }
        if (s.phase != Phase.DRIVING) return@withContext "Not driving a route just now."
        val here = lastFix ?: return@withContext "No position yet."
        val g = s.guidance ?: return@withContext "No position on the route yet."
        val route = s.route ?: return@withContext "No route yet."
        val target = s.target ?: return@withContext "No route yet."
        val found = withContext(Dispatchers.IO) { fetch(ctx, here, target, s.avoid, true) }
        val now = _state.value ?: return@withContext "Not navigating."
        if (now.route !== route || !found.traffic) return@withContext "The route changed meanwhile."
        val left = now.guidance?.remainingS ?: g.remainingS
        val kmAlong = now.guidance?.kmAlong ?: g.kmAlong
        val best = found.routes.minByOrNull { it.durationS } ?: return@withContext "No route found."
        val saves = left - best.durationS
        val same = found.routes.firstOrNull { route.follows(it, kmAlong) }
        when {
            saves >= max(FASTER_MIN_S, FASTER_FRACTION * left) && !route.follows(best, kmAlong) -> {
                val via = best.summary.takeIf { it.isNotBlank() }?.let { " via $it" } ?: ""
                val words = "A faster route$via saves about ${minutes(saves)}. Taking it."
                if (announce) speak(words)
                use(best, "Faster route: saves ${minutes(saves)}", traffic = true)
                words
            }
            same != null -> {
                use(same, "", quietUntilKm = 0.02, traffic = true)
                val delay = same.trafficDelayS ?: 0.0
                if (delay >= 60) "This is still the fastest way; traffic adds about ${minutes(delay)}."
                else "This is still the fastest way, and traffic is light."
            }
            else -> "This is still the fastest way."
        }
    }

    // -- position ---------------------------------------------------------------------------

    @SuppressLint("MissingPermission")
    private fun startUpdates(context: Context) {
        if (!PhoneLocation.precise(context)) {
            update { it.copy(note = "Turn-by-turn needs precise location (allow it for this app)") }
            return
        }
        val lm = context.getSystemService(LocationManager::class.java) ?: return
        val enabled = lm.getProviders(true)
        val provider = listOf(LocationManager.GPS_PROVIDER, "fused", LocationManager.NETWORK_PROVIDER)
            .firstOrNull { it in enabled } ?: run {
            update { it.copy(note = "Location is off on the phone") }
            return
        }
        val l = LocationListenerCompat { onFix(it) }
        try {
            LocationManagerCompat.requestLocationUpdates(lm, provider,
                LocationRequestCompat.Builder(1000L)
                    .setQuality(LocationRequestCompat.QUALITY_HIGH_ACCURACY)
                    .setMinUpdateIntervalMillis(500L).build(),
                ContextCompat.getMainExecutor(context), l)
            listener = l
        } catch (e: SecurityException) {
            update { it.copy(note = "No permission for location") }
        }
    }

    private fun stopUpdates() {
        val l = listener ?: return
        listener = null
        app?.getSystemService(LocationManager::class.java)?.let {
            LocationManagerCompat.removeUpdates(it, l)
        }
    }

    private fun onFix(loc: Location) {
        lastFix = loc
        val s = _state.value ?: return
        val now = System.currentTimeMillis()
        val speed = if (loc.hasSpeed()) loc.speed * 3.6 else s.speedKmh
        val fix = loc.latitude to loc.longitude
        when (s.phase) {
            Phase.ARRIVED -> return
            Phase.AT_STOP -> {
                _state.value = s.copy(fix = fix, speedKmh = speed)
                val at = s.stopHere
                if (at == null || Planner.haversineKm(loc.latitude, loc.longitude, at.lat, at.lon) >
                    LEAVE_STOP_KM) {
                    _state.value = _state.value?.copy(phase = Phase.ROUTING, route = null,
                        guidance = null, stopHere = null)
                    route("On to ${s.target?.label ?: "the destination"}", force = true)
                }
                return
            }
            else -> Unit
        }
        val g = guidance ?: run {
            _state.value = s.copy(fix = fix, speedKmh = speed)
            if (routing?.isActive != true && now - routedAtMs > RETRY_MS) route(s.note)
            return
        }
        val gs = g.update(loc.latitude, loc.longitude)
        var words = gs.say
        // Heavy traffic coming up: once for each jam, even after a fresher route.
        g.route.trafficAhead(gs.kmAlong, TRAFFIC_WARN_KM)?.let { run ->
            val (lat, lon) = g.route.pointAt(run.fromKm)
            if (warned.none { (a, b) -> Planner.haversineKm(a, b, lat, lon) < 1.5 }) {
                warned += lat to lon
                words = listOfNotNull(words, NavSpeech.traffic(run, gs.kmAlong)).joinToString(" ")
            }
        }
        val close = gs.next?.let { it.atKm - gs.kmAlong < 0.5 } == true
        words?.let { speak(it, urgent = close) }
        val note = when {
            gs.offRoute -> if (routing?.isActive == true) "Re-routing" else "Off the route"
            // Back on the road: what was said about leaving it is over.
            s.note == "Off the route" || s.note == "Re-routing" || s.note.startsWith("No route yet") -> ""
            else -> s.note
        }
        _state.value = s.copy(guidance = gs, fix = fix, speedKmh = speed, note = note)
        if (gs.offRoute && now - routedAtMs > REROUTE_MIN_MS) route("Re-routing")
        if (gs.arrived) arrive()
    }

    private fun arrive() {
        val s = _state.value ?: return
        val t = s.target ?: return
        guidance = null
        val stop = t.stop
        if (s.lastLeg || stop == null) {
            _state.value = s.copy(phase = Phase.ARRIVED, note = "Arrived at ${t.label}")
            scope.launch {
                delay(20_000L)                      // the screen says so for a moment
                if (_state.value?.phase == Phase.ARRIVED) TripMonitor.stop()   // ends this too
            }
            return
        }
        speak(String.format(Locale.UK, "This is charging stop %d, %s. Charge to %.0f percent, " +
            "about %d minutes.", s.trip.plan.stops.indexOf(stop) + 1, t.label, stop.departPct,
            stop.chargeMin))
        _state.value = s.copy(phase = Phase.AT_STOP, stopHere = t, leg = s.leg + 1, route = null,
            guidance = null, note = "Charging at ${t.label}")
    }

    // -- speech ---------------------------------------------------------------------------

    /** Say it, unless muted; never into the microphone while the driver is asking something. */
    private fun speak(text: String, urgent: Boolean = false) {
        val ctx = app ?: return
        if (_state.value?.muted != false) return
        said.value = (said.value + text).takeLast(10)
        if (VoiceAssistant.state.value == VoiceAssistant.State.LISTENING) {
            scope.launch {
                withTimeoutOrNull(15_000L) {
                    VoiceAssistant.state.first { it != VoiceAssistant.State.LISTENING }
                }
                if (_state.value?.muted == false) Speaker.say(ctx, text, interrupt = urgent)
            }
        } else {
            Speaker.say(ctx, text, interrupt = urgent)
        }
    }

    fun minutes(seconds: Double): String {
        val m = max(1L, (seconds / 60.0).roundToLong())
        return if (m == 1L) "1 minute" else "$m minutes"
    }
}
