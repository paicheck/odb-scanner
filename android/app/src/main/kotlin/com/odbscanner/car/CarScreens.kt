package com.odbscanner.car

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import androidx.car.app.CarContext
import androidx.car.app.CarToast
import androidx.car.app.Screen
import androidx.car.app.constraints.ConstraintManager
import androidx.car.app.model.Action
import androidx.car.app.model.ActionStrip
import androidx.car.app.model.ItemList
import androidx.car.app.model.ListTemplate
import androidx.car.app.model.MessageTemplate
import androidx.car.app.model.Row
import androidx.car.app.model.SearchTemplate
import androidx.car.app.model.Template
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.odbscanner.ai.Assistant
import com.odbscanner.ai.Speaker
import com.odbscanner.ai.VoiceAssistant
import com.odbscanner.ai.VoiceSource
import com.odbscanner.ai.VoiceState
import com.odbscanner.db.AppDatabase
import com.odbscanner.db.DtcEntity
import com.odbscanner.db.MeasurementEntity
import com.odbscanner.costLine
import com.odbscanner.fmtDuration
import com.odbscanner.headline
import com.odbscanner.route.NavSpeech
import com.odbscanner.route.NavText
import com.odbscanner.route.Navigator
import com.odbscanner.route.PhoneLocation
import com.odbscanner.route.Place
import com.odbscanner.route.RoutePrefs
import com.odbscanner.route.RouteRevisions
import com.odbscanner.route.RoutingException
import com.odbscanner.route.TripMonitor
import com.odbscanner.route.TripPlanner
import com.odbscanner.route.TripResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.Duration
import java.time.Instant
import java.util.Locale

/** Most rows a list may show on this car's screen (hosts cap it, often at 6). */
private fun CarContext.listLimit(): Int = try {
    getCarService(ConstraintManager::class.java)
        .getContentLimit(ConstraintManager.CONTENT_LIMIT_TYPE_LIST)
} catch (_: Exception) {
    6
}

/** "3 h ago" for a stored timestamp (Instant.toString()); the car is read in sessions. */
private fun age(ts: String?): String {
    val t = ts?.let { runCatching { Instant.parse(it) }.getOrNull() } ?: return "time unknown"
    val m = Duration.between(t, Instant.now()).toMinutes()
    return when {
        m < 1 -> "just now"
        m < 60 -> "$m min ago"
        m < 48 * 60 -> "${m / 60} h ago"
        else -> "${m / (24 * 60)} days ago"
    }
}

private fun message(title: String, text: String): Template =
    MessageTemplate.Builder(text).setTitle(title).setHeaderAction(Action.BACK).build()

private fun loading(title: String): Template =
    ListTemplate.Builder().setTitle(title).setHeaderAction(Action.BACK)
        .setLoading(true).build()

private fun navigate(car: CarContext, lat: Double, lon: Double) {
    try {
        car.startCarApp(Intent(CarContext.ACTION_NAVIGATE, Uri.parse("geo:$lat,$lon")))
    } catch (_: Exception) {
        CarToast.makeText(car, "No navigation app available", CarToast.LENGTH_LONG).show()
    }
}

// -- Home ------------------------------------------------------------------------
class HomeScreen(carContext: CarContext) : Screen(carContext) {
    init {
        lifecycleScope.launch {
            Navigator.state.map { it != null }.distinctUntilChanged().collect { invalidate() }
        }
    }

    override fun onGetTemplate(): Template {
        val list = ItemList.Builder()
        Navigator.state.value?.let { nav ->
            list.addItem(Row.Builder().setTitle("Navigating to ${nav.trip.destination.label}")
                .addText("Directions, lanes and traffic")
                .setOnClickListener { screenManager.push(NavScreen(carContext)) }
                .setBrowsable(true).build())
        }
        val items = list
            .addItem(Row.Builder().setTitle("Battery")
                .addText("Charge, energy, temperatures")
                .setOnClickListener { screenManager.push(BatteryScreen(carContext)) }
                .setBrowsable(true).build())
            .addItem(Row.Builder().setTitle("Route planner")
                .addText("Arrival charge and charging stops")
                .setOnClickListener { screenManager.push(RouteSearchScreen(carContext)) }
                .setBrowsable(true).build())
            .addItem(Row.Builder().setTitle("Fault codes")
                .addText("Stored DTCs from the last scan")
                .setOnClickListener { screenManager.push(FaultsScreen(carContext)) }
                .setBrowsable(true).build())
            .addItem(Row.Builder().setTitle("Assistant")
                .addText("Ask about the battery, the trip, chargers")
                .setOnClickListener { screenManager.push(AssistantScreen(carContext)) }
                .setBrowsable(true).build())
            .build()
        return ListTemplate.Builder().setSingleList(items).setTitle("ODB Scanner")
            .setHeaderAction(Action.APP_ICON).build()
    }
}

// -- Assistant -----------------------------------------------------------------------
/**
 * Speak to the assistant from the car: tap Speak, ask, hear the answer (also
 * shown). It listens through the phone's microphone and answers through the
 * car's speakers; the conversation is the one the phone shows.
 */
class AssistantScreen(carContext: CarContext) : Screen(carContext) {
    init {
        lifecycleScope.launch { VoiceAssistant.state.collect { invalidate() } }
        lifecycleScope.launch { Assistant.transcript.collect { invalidate() } }
    }

    private fun speak() {
        fun go() {
            // Over a long answer (or a slow one), Speak talks over it.
            if (VoiceAssistant.status.value.active) VoiceAssistant.bargeIn(carContext, VoiceSource.CAR)
            else VoiceAssistant.start(carContext, VoiceSource.CAR)
        }
        if (ContextCompat.checkSelfPermission(carContext, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        ) go()
        else carContext.requestPermissions(listOf(Manifest.permission.RECORD_AUDIO)) { granted, _ ->
            if (granted.isNotEmpty()) go()
        }
    }

    override fun onGetTemplate(): Template {
        if (!Assistant.ready(carContext)) {
            return message("Assistant",
                "Choose an AI service and add its key on the phone (Ask AI, Settings) first.")
        }
        // Fixed row titles: Android Auto then counts each update as a refresh of
        // this screen, not a new step (it allows only a few steps per task).
        val lines = Assistant.transcript.value
        val asked = lines.lastOrNull { it.fromDriver }?.text ?: "Nothing yet"
        val last = lines.lastOrNull { !it.fromDriver }?.text
        val status = VoiceAssistant.status.value
        val answer = when (status.state) {
            VoiceState.LISTENING -> "Listening…"
            VoiceState.TRANSCRIBING -> "Recognising…"
            VoiceState.THINKING -> "Thinking…"
            VoiceState.INTERRUPTED -> "Stopped"
            VoiceState.ERROR -> status.error?.message ?: "That didn't work"
            else -> last ?: "Tap Speak and ask about the battery, your trip, the next " +
                "charging stop or chargers nearby."
        }
        val list = ItemList.Builder()
            .addItem(Row.Builder().setTitle("You asked").addText(asked).build())
            .addItem(Row.Builder().setTitle("Answer").addText(answer).build())
            .build()
        val actions = ActionStrip.Builder().addAction(Action.Builder()
            .setTitle(if (status.active) "Interrupt" else "Speak")
            .setOnClickListener {
                val s = VoiceAssistant.status.value.state
                if (s != VoiceState.LISTENING && s != VoiceState.TRANSCRIBING) speak()
            }
            .build())
        if (status.active) {
            // Stop instead of Repeat while it runs (an action strip holds few).
            actions.addAction(Action.Builder().setTitle("Stop")
                .setOnClickListener { VoiceAssistant.stop() }.build())
        } else if (status.state == VoiceState.ERROR && VoiceAssistant.lastQuestion != null) {
            // After a failure: ask the same question again, no need to repeat it.
            actions.addAction(Action.Builder().setTitle("Retry")
                .setOnClickListener { VoiceAssistant.retry(carContext) }.build())
        } else if (last != null) {
            actions.addAction(Action.Builder().setTitle("Repeat")
                .setOnClickListener { Speaker.say(carContext, last) }.build())
        }
        return ListTemplate.Builder().setSingleList(list).setTitle("Assistant")
            .setHeaderAction(Action.BACK).setActionStrip(actions.build()).build()
    }
}

// -- Battery -----------------------------------------------------------------------
/**
 * The newest stored battery readings, each with its age: the phone reads the
 * car in sessions, so "now" is the last session. Refresh re-reads the database.
 */
class BatteryScreen(carContext: CarContext) : Screen(carContext) {
    private var rows: List<Pair<String, String>>? = null

    private fun load() {
        lifecycleScope.launch {
            rows = withContext(Dispatchers.IO) { batteryRows(carContext) }
            invalidate()
        }
    }

    init { load() }

    override fun onGetTemplate(): Template {
        val r = rows ?: return loading("Battery")
        if (r.isEmpty()) {
            return message("Battery",
                "No battery readings stored yet. Connect the adapter in the phone app.")
        }
        val list = ItemList.Builder()
        r.take(carContext.listLimit()).forEach { (title, text) ->
            list.addItem(Row.Builder().setTitle(title).addText(text).build())
        }
        return ListTemplate.Builder().setSingleList(list.build()).setTitle("Battery")
            .setHeaderAction(Action.BACK)
            .setActionStrip(ActionStrip.Builder().addAction(
                Action.Builder().setTitle("Refresh").setOnClickListener {
                    rows = null
                    invalidate()
                    load()
                }.build()).build())
            .build()
    }
}

private val BATTERY_KEYS = listOf(
    Triple("soc_abs", "State of charge (BMS)", "%.1f %%"),
    Triple("hv_energy_content", "Energy in the battery", "%.1f kWh"),
    Triple("hv_energy_max", "Usable when full", "%.1f kWh"),
    Triple("bat_temp_max", "Battery temperature max", "%.0f °C"),
    Triple("bat_temp_min", "Battery temperature min", "%.0f °C"),
    Triple("pack_voltage", "Pack voltage", "%.0f V"),
    Triple("aux_12v_voltage", "12 V battery", "%.2f V"),
)

private suspend fun batteryRows(context: android.content.Context): List<Pair<String, String>> {
    val db = AppDatabase.get(context)
    val vid = db.vehicleDao().getFirst()?.id ?: return emptyList()
    val dao = db.measurementDao()
    return BATTERY_KEYS.mapNotNull { (key, title, fmt) ->
        val m: MeasurementEntity = dao.latestValue(vid, key) ?: return@mapNotNull null
        val v = m.value!! / (if (key.startsWith("hv_energy")) 1000.0 else 1.0)
        title to "${String.format(Locale.ROOT, fmt, v)} · ${age(m.ts)}"
    }
}

// -- Fault codes ----------------------------------------------------------------------
class FaultsScreen(carContext: CarContext) : Screen(carContext) {
    private var dtcs: List<DtcEntity>? = null

    init {
        lifecycleScope.launch {
            dtcs = withContext(Dispatchers.IO) {
                val db = AppDatabase.get(carContext)
                db.vehicleDao().getFirst()?.id?.let { db.dtcDao().getAll(it) } ?: emptyList()
            }
            invalidate()
        }
    }

    override fun onGetTemplate(): Template {
        val all = dtcs ?: return loading("Fault codes")
        if (all.isEmpty()) {
            return message("Fault codes",
                "No fault codes stored. The phone reads them during each collection session.")
        }
        val limit = carContext.listLimit()
        val list = ItemList.Builder()
        all.take(limit).forEach { d ->
            list.addItem(Row.Builder().setTitle("${d.code} · ${d.status}")
                .addText("${d.ecu} · seen ${d.occurrenceCount}× · last ${age(d.lastSeen)}")
                .build())
        }
        val more = all.size - limit
        return ListTemplate.Builder().setSingleList(list.build())
            .setTitle(if (more > 0) "Fault codes (${all.size}, $more more on the phone)"
                      else "Fault codes (${all.size})")
            .setHeaderAction(Action.BACK).build()
    }
}

// -- Navigation --------------------------------------------------------------------------
/**
 * Turn-by-turn on the car screen, as text: the app is a charging app to
 * Android Auto, so it has no map template -- the spoken directions carry the
 * guidance, this screen backs them up. Fixed row titles: each update is a
 * refresh of this screen, not a new step.
 */
class NavScreen(carContext: CarContext) : Screen(carContext) {
    init {
        lifecycleScope.launch {
            Navigator.state.map { it?.let(::rows) }.distinctUntilChanged().collect { invalidate() }
        }
    }

    /** What the rows say: (title, text) each. Distances as shown, so updates are few. */
    private fun rows(s: Navigator.State): List<Pair<String, String>> {
        val g = s.guidance
        val next = g?.next
        val turn = when {
            s.phase == Navigator.Phase.AT_STOP || s.phase == Navigator.Phase.ARRIVED -> s.note
            next == null || g.offRoute -> s.note.ifBlank { "Finding the route" }
            else -> "${NavText.arrow(next)} ${NavText.distance(g.toNextKm)} · ${next.instruction}" +
                (g.then?.let { "  Then ${NavText.arrow(it)}" } ?: "")
        }
        val lanes = if (g != null && next != null && g.lanes.isNotEmpty()) {
            g.lanes.joinToString(" ") { NavText.lane(it, next.modifier) + (if (it.active) "" else "·") } +
                "  " + (NavSpeech.laneAdvice(g.lanes) ?: "Any lane")
        } else "No lane advice for the next turn"
        val ahead = g?.let { s.route?.trafficAhead(it.kmAlong, 8.0) }
        val traffic = when {
            !s.traffic -> "No live traffic (needs a Mapbox token)"
            g != null && ahead != null -> NavSpeech.traffic(ahead, g.kmAlong)
            else -> (s.route?.trafficDelayS ?: 0.0).let { d ->
                if (d >= 60) "Traffic adds ${Navigator.minutes(d)}" else "Traffic is light"
            }
        }
        val arrive = g?.let {
            val end = s.toEndS ?: it.remainingS
            val clock = java.time.LocalTime.now().plusSeconds(end.toLong())
                .format(java.time.format.DateTimeFormatter.ofPattern("HH:mm"))
            "$clock · ${fmtDuration(end)}" + (s.target?.stop?.let { st ->
                " · charge at ${st.charger.name} in ${NavText.distance(it.remainingKm)}" +
                    " (arrive about ${st.arrivePct.toInt()} %, charge to ${st.departPct.toInt()} %)"
            } ?: (" · ${NavText.distance(it.remainingKm)}" + (s.trip.plan.arrivalPct?.let { pct ->
                " · arrive about ${pct.toInt()} %" + (s.trip.plan.arrivalBand?.let { b ->
                    " (${b.lowPct.toInt()}–${b.highPct.toInt()})" } ?: "")
            } ?: "")))
        } ?: s.trip.destination.label
        return listOf("Next" to turn, "Lanes" to lanes, "Traffic" to traffic, "Arrive" to arrive)
    }

    override fun onGetTemplate(): Template {
        val s = Navigator.state.value
            ?: return message("Navigation", "Navigation has ended.")
        val list = ItemList.Builder()
        rows(s).take(carContext.listLimit()).forEach { (title, text) ->
            val row = Row.Builder().setTitle(title).addText(text)
            if (title == "Next") row.setOnClickListener { Navigator.repeat() }   // say it again
            list.addItem(row.build())
        }
        return ListTemplate.Builder().setSingleList(list.build())
            .setTitle("To ${s.trip.destination.label}")
            .setHeaderAction(Action.BACK)
            .setActionStrip(ActionStrip.Builder()
                .addAction(Action.Builder().setTitle(if (s.muted) "Voice on" else "Mute")
                    .setOnClickListener { Navigator.setMuted(!s.muted) }.build())
                .addAction(Action.Builder().setTitle("End").setOnClickListener {
                    TripMonitor.stop()
                    screenManager.pop()
                }.build())
                .build())
            .build()
    }
}

// -- Route planner -----------------------------------------------------------------------
/**
 * Destination search. Before typing, recent destinations are offered: typing
 * is limited while driving, so a repeat trip is one tap.
 */
class RouteSearchScreen(carContext: CarContext) : Screen(carContext) {
    private var results: List<Place> = RoutePrefs.recent(carContext)
    private var searching = false
    private var error: String? = null

    override fun onGetTemplate(): Template {
        val missing = RoutePrefs.load(carContext).missingKeys
        if (missing.isNotEmpty()) {
            return message("Route planner",
                "Add the free ${missing.joinToString(" and ")} keys in the phone app " +
                    "(Route → Settings) first.")
        }
        val list = ItemList.Builder()
        error?.let { list.setNoItemsMessage(it) }
            ?: list.setNoItemsMessage("Search for a destination")
        val active = TripMonitor.state.value
        if (active != null) {
            list.addItem(Row.Builder().setTitle("Trip to ${active.destination.label}")
                .addText(active.note)
                .setOnClickListener { screenManager.push(TripScreen(carContext, active.destination)) }
                .setBrowsable(true).build())
        }
        results.take(carContext.listLimit() - (if (active != null) 1 else 0)).forEach { p ->
            list.addItem(Row.Builder().setTitle(p.label)
                .setOnClickListener { screenManager.push(TripScreen(carContext, p)) }
                .setBrowsable(true).build())
        }
        return SearchTemplate.Builder(object : SearchTemplate.SearchCallback {
            override fun onSearchSubmitted(searchText: String) {
                if (searchText.isBlank()) return
                searching = true
                invalidate()
                lifecycleScope.launch {
                    try {
                        results = TripPlanner.search(carContext, searchText.trim())
                        error = if (results.isEmpty()) "No place found" else null
                    } catch (e: RoutingException) {
                        results = emptyList()
                        error = e.message
                    } finally {
                        searching = false
                        invalidate()
                    }
                }
            }
        }).setHeaderAction(Action.BACK).setSearchHint("Destination")
            .setShowKeyboardByDefault(false)
            .setLoading(searching)
            .apply { if (!searching) setItemList(list.build()) }
            .build()
    }
}

/**
 * The plan for one destination: headline, then each stop (tap to navigate).
 * Navigating starts the TripMonitor, which re-plans from the battery as the
 * trip goes; this screen follows its latest plan.
 */
class TripScreen(carContext: CarContext, private val dest: Place) : Screen(carContext) {
    private var result: TripResult? = null
    private var error: String? = null
    private var note: String? = null
    // The stop last handed to the car's navigation app (one place at a time):
    // when a new revision moves the next stop, the screen says so.
    private var navigatedTo: String? = null

    init {
        val active = TripMonitor.state.value
        if (active != null && active.destination == dest) result = active.result else plan()
        lifecycleScope.launch {
            TripMonitor.state.collect { t ->
                if (t != null && t.destination == dest) {
                    result = t.result
                    note = t.note
                    error = null
                } else {
                    note = null
                }
                invalidate()
            }
        }
    }

    private fun drive(r: TripResult, lat: Double, lon: Double) {
        navigatedTo = RouteRevisions.key("", lat, lon)
        fun go() {
            // In the app: the whole trip, stop by stop. Else the car's own
            // navigation app, to the tapped place.
            if (TripMonitor.drive(carContext, r)) screenManager.push(NavScreen(carContext))
            else navigate(carContext, lat, lon)
        }
        // Following the trip needs precise location; asked once, on the phone.
        if (PhoneLocation.precise(carContext)) go()
        else carContext.requestPermissions(listOf(Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION)) { _, _ -> go() }
    }

    private fun plan() {
        if (!PhoneLocation.permitted(carContext)) {
            carContext.requestPermissions(listOf(Manifest.permission.ACCESS_COARSE_LOCATION)) {
                granted, _ ->
                if (granted.isEmpty()) {
                    error = "Location access is needed to plan from here. Allow it on the phone."
                    invalidate()
                } else {
                    plan()
                }
            }
            return
        }
        lifecycleScope.launch {
            try {
                result = TripPlanner.plan(carContext, dest, null)
                RoutePrefs.remember(carContext, dest)
            } catch (e: RoutingException) {
                error = e.message
            } catch (e: Exception) {
                error = "Planning failed (${e.javaClass.simpleName})"
            }
            invalidate()
        }
    }

    override fun onGetTemplate(): Template {
        error?.let { return message("Route planner", it) }
        val r = result ?: return loading(dest.label)
        val p = r.plan
        val active = TripMonitor.state.value?.takeIf { it.destination == dest }
        // A suggested plan takes the screen until answered: two actions, no reading.
        active?.route?.proposal?.let { proposal ->
            return MessageTemplate.Builder(proposal.change)
                .setTitle(if (proposal.urgent) "Your plan no longer works" else "New plan suggested")
                .setHeaderAction(Action.BACK)
                .addAction(Action.Builder().setTitle("Switch").setOnClickListener {
                    lifecycleScope.launch {
                        val said = TripMonitor.accept(carContext)
                        CarToast.makeText(carContext, said, CarToast.LENGTH_LONG).show()
                    }
                }.build())
                .addAction(Action.Builder().setTitle("Keep my plan").setOnClickListener {
                    CarToast.makeText(carContext, TripMonitor.decline(), CarToast.LENGTH_SHORT).show()
                }.build())
                .build()
        }
        val list = ItemList.Builder()
        list.addItem(Row.Builder().setTitle(r.headline())
            .addText(String.format(Locale.ROOT, "%.0f km · ", p.distanceKm) +
                fmtDuration(p.driveS + p.chargeS) +
                if (p.chargeS > 0) " incl. ${fmtDuration(p.chargeS)} charging" else "")
            .apply { if (p.stops.isNotEmpty()) addText(r.costLine()) }
            .apply {
                active?.let { a -> addText("Plan revision ${a.route.revision}: ${a.route.current.reason}") }
            }
            .build())
        val next = p.stops.firstOrNull()
        val moved = navigatedTo != null && next != null &&
            navigatedTo != RouteRevisions.key("", next.charger.lat, next.charger.lon)
        p.stops.take(carContext.listLimit() - 2).forEachIndexed { i, s ->
            list.addItem(Row.Builder().setTitle("${i + 1}. ${s.charger.name}" +
                    if (i == 0 && moved) " (new next stop: tap to navigate)" else "")
                .addText(String.format(Locale.ROOT,
                    "%.0f km · %.0f kW · %.0f → %.0f %% · %d min · £%.2f",
                    s.km, s.charger.powerKw, s.arrivePct, s.departPct, s.chargeMin, s.cost))
                .apply {
                    if (s.arriveLowPct != null && s.arriveHighPct != null) addText(String.format(
                        Locale.ROOT, "Likely %.0f–%.0f %% on arrival", s.arriveLowPct, s.arriveHighPct))
                }
                .setOnClickListener { drive(r, s.charger.lat, s.charger.lon) }
                .build())
        }
        list.addItem(Row.Builder().setTitle(
                if (Navigator.state.value?.trip?.destination == dest) "Directions to ${dest.label}"
                else "Navigate to ${dest.label}")
            .apply { note?.let { addText(it) } }
            .setOnClickListener {
                if (Navigator.state.value?.trip?.destination == dest) {
                    screenManager.push(NavScreen(carContext))
                } else {
                    drive(r, dest.lat, dest.lon)
                }
            }.build())
        val template = ListTemplate.Builder().setSingleList(list.build()).setTitle(dest.label)
            .setHeaderAction(Action.BACK)
        if (TripMonitor.state.value?.destination == dest) {
            template.setActionStrip(ActionStrip.Builder()
                .addAction(Action.Builder().setTitle("Re-plan").setOnClickListener {
                    TripMonitor.replanNow(carContext)
                    CarToast.makeText(carContext, "Re-planning from here", CarToast.LENGTH_SHORT).show()
                }.build())
                .addAction(Action.Builder().setTitle("End trip").setOnClickListener {
                    TripMonitor.stop()
                }.build())
                .build())
        }
        return template.build()
    }
}
