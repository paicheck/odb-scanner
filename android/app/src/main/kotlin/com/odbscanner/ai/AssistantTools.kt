package com.odbscanner.ai

import android.content.Context
import com.odbscanner.db.AppDatabase
import com.odbscanner.db.MeasurementEntity
import com.odbscanner.route.ActiveTrip
import com.odbscanner.route.CarEnergy
import com.odbscanner.route.MapsHandoff
import com.odbscanner.route.NavSpeech
import com.odbscanner.route.Navigator
import com.odbscanner.route.PhoneLocation
import com.odbscanner.route.Picks
import com.odbscanner.route.Prices
import com.odbscanner.route.Replan
import com.odbscanner.route.RoutePrefs
import com.odbscanner.route.RouteRevisions
import com.odbscanner.route.RouteServices
import com.odbscanner.route.RoutingException
import com.odbscanner.route.Stop
import com.odbscanner.route.TripMonitor
import com.odbscanner.route.TripPlanner
import com.odbscanner.route.TripResult
import com.odbscanner.startTrip
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.time.Duration
import java.time.Instant

/**
 * What the assistant can look up or do. Everything reads the phone's own data
 * -- what the collector stored, the trip watch, the route planner, the
 * navigation -- and nothing reaches the car: there is no tool that sends
 * anything to it. The only actions are planning or re-planning a trip and
 * starting or re-routing navigation.
 *
 * Results are small JSON objects, distances in both km and miles. No VIN and
 * no raw position of the phone are ever in them ([redactVin] runs over every
 * result as a backstop).
 */
class AssistantTools(private val context: Context) : ToolRunner {

    override suspend fun run(call: ToolCall): String = redactVin(try {
        when (call.name) {
            "get_battery" -> battery()
            "get_fault_codes" -> faults()
            "get_trip" -> trip()
            "get_directions" -> directions(call.args.optInt("count", 5).coerceIn(1, 10))
            "find_chargers" -> chargers(call.args.optDouble("radius_km", 10.0).coerceIn(1.0, 50.0),
                call.args.optDouble("min_kw", 50.0).coerceIn(7.0, 350.0))
            "plan_trip" -> planTrip(call.args.optString("destination"),
                call.args.optString("mode").takeIf { it in com.odbscanner.route.MODES })
            "replan_trip" -> replan()
            "start_navigation" -> startNavigation()
            "get_navigation" -> navigation()
            "reroute" -> reroute(call.args)
            "get_readings" -> readings(call.args.optString("filter"))
            "get_history" -> history(call.args.optString("key"),
                call.args.optInt("count", 60).coerceIn(2, 500))
            "get_route_changes" -> routeChanges()
            "get_charging_history" -> chargingHistory(call.args.optInt("count", 5).coerceIn(1, 20))
            "answer_suggestion" -> answerSuggestion(call.args.optBoolean("switch"))
            "avoid_stop" -> avoidStop(call.args.optInt("stop", 1), call.args.optBoolean("confirm"))
            "use_alternative" -> useAlternative(call.args.optInt("stop", 1),
                call.args.optInt("alternative", 1), call.args.optBoolean("confirm"))
            "avoid_network" -> avoidNetwork(call.args.optString("network"),
                call.args.optBoolean("confirm"))
            else -> err("unknown tool ${call.name}")
        }
    } catch (e: RoutingException) {
        err(e.message ?: "routing failed")
    } catch (e: Exception) {
        err("${e.javaClass.simpleName}: ${e.message}")
    }.toString())

    // -- the car ------------------------------------------------------------------
    private suspend fun vehicleId(): Long? = withContext(Dispatchers.IO) {
        AppDatabase.get(context).vehicleDao().getFirst()?.id
    }

    private suspend fun battery(): JSONObject = withContext(Dispatchers.IO) {
        val vid = vehicleId() ?: return@withContext err("no readings stored yet")
        val dao = AppDatabase.get(context).measurementDao()
        val out = JSONObject()
        for ((key, label) in BATTERY_KEYS) {
            val m = dao.latestValue(vid, key) ?: continue
            out.put(label, reading(m))
        }
        // Cell voltages are stored per cell: summarise the newest of each.
        val cells = dao.latestPerKey(vid).filter { it.key.startsWith("cell_v_") && it.success && it.value != null }
        if (cells.isNotEmpty()) {
            val v = cells.map { it.value!! }
            val unitMv = cells.first().unit.equals("mV", ignoreCase = true)
            val toMv = if (unitMv) 1.0 else 1000.0
            out.put("cell_voltages", JSONObject()
                .put("cells", cells.size)
                .put("lowest_mv", round1(v.min() * toMv)).put("highest_mv", round1(v.max() * toMv))
                .put("spread_mv", round1((v.max() - v.min()) * toMv))
                .put("age", age(cells.maxOf { it.ts })))
        }
        if (out.length() == 0) err("no battery readings stored yet") else out
    }

    private suspend fun faults(): JSONObject = withContext(Dispatchers.IO) {
        val vid = vehicleId() ?: return@withContext err("no data stored yet")
        val dtcs = AppDatabase.get(context).dtcDao().getAll(vid)
        JSONObject().put("count", dtcs.size).put("codes", JSONArray().apply {
            dtcs.take(15).forEach { d ->
                put(JSONObject().put("code", d.code).put("status", d.status).put("module", d.ecu)
                    .put("times_seen", d.occurrenceCount).put("last_seen", age(d.lastSeen)))
            }
        }).put("note", "codes as last read by the phone; the app reads but never clears them")
    }

    private suspend fun readings(filter: String): JSONObject = withContext(Dispatchers.IO) {
        val vid = vehicleId() ?: return@withContext err("no readings stored yet")
        val f = filter.trim().lowercase()
        val rows = AppDatabase.get(context).measurementDao().latestPerKey(vid)
            .filter { it.success && (it.value != null || !it.textValue.isNullOrBlank()) }
            .filter { f.isEmpty() || f in it.key.lowercase() }
            .filterNot { it.key.startsWith("cell_v_") && f.isEmpty() }
        JSONObject().put("matching", rows.size).put("readings", JSONArray().apply {
            rows.take(40).forEach { put(JSONObject().put("key", it.key).put("reading", reading(it))) }
        }).apply { if (rows.size > 40) put("note", "first 40 shown; give a filter for others") }
    }

    private suspend fun history(key: String, count: Int): JSONObject = withContext(Dispatchers.IO) {
        if (key.isBlank()) return@withContext err("give a measurement key, e.g. soc_abs")
        val vid = vehicleId() ?: return@withContext err("no readings stored yet")
        val rows = AppDatabase.get(context).measurementDao().valueHistory(vid, key, count)
            .filter { it.success && it.value != null }.sortedBy { it.ts }
        if (rows.isEmpty()) return@withContext err("no stored values for $key")
        val v = rows.map { it.value!! }
        JSONObject().put("key", key).put("unit", rows.first().unit).put("values", rows.size)
            .put("oldest", JSONObject().put("value", v.first()).put("age", age(rows.first().ts)))
            .put("newest", JSONObject().put("value", v.last()).put("age", age(rows.last().ts)))
            .put("min", v.min()).put("max", v.max()).put("average", round2(v.average()))
    }

    // -- the trip -------------------------------------------------------------------
    private suspend fun where(r: TripResult): Pair<Double?, Double?> {
        val here = PhoneLocation.current(context) ?: return null to null
        val at = Replan.progress(r.line, r.plan.distanceKm, here.latitude, here.longitude)
        return at.kmAlong to at.offRouteKm
    }

    private suspend fun trip(): JSONObject {
        val t: ActiveTrip = TripMonitor.state.value
            ?: return err("no trip is active; plan one with plan_trip")
        val (km, off) = where(t.result)
        return summary(t.result, km).put("watch_status", t.note).put("replans", t.replans)
            .apply { off?.let { if (it > Replan.OFF_ROUTE_KM) put("off_route", dist(it)) } }
            .apply { t.assessment?.let { put("live_check", liveCheck(it)) } }
    }

    /**
     * The app's own check of the plan against the car's battery now
     * (EnRoute, as the trip watch runs it): the answer to "will I make it?".
     * The AI explains it; it never works out its own figures.
     */
    private suspend fun liveCheck(a: com.odbscanner.route.EnRoute.Assessment): JSONObject {
        val car = CarEnergy.load(context)
        return JSONObject()
            .put("level", a.level)
            .put("summary", a.action)
            .put("battery_now_pct", a.actualPct?.let { round1(it) } ?: JSONObject.NULL)
            .put("battery_reading_age", age(car.startTs))
            .put("plan_expected_here_pct", a.expectedPct?.let { round1(it) } ?: JSONObject.NULL)
            .put("planned_at_next_pct", a.plannedTargetPct?.let { round1(it) } ?: JSONObject.NULL)
            .put("projected_at_next_pct", a.projectedTargetPct?.let { round1(it) } ?: JSONObject.NULL)
            .put("using_times_the_plan", a.ratio?.let { round2(it) } ?: JSONObject.NULL)
            .put("reach_before_reserve", a.reachKm?.let { dist(it) } ?: JSONObject.NULL)
            .put("source", "the app's own en-route check (EnRoute), from the car's battery reading")
    }

    /** Recent charging sessions the phone recorded: when, AC or DC, from and to what charge. */
    private suspend fun chargingHistory(count: Int): JSONObject = withContext(Dispatchers.IO) {
        val vid = vehicleId() ?: return@withContext err("no readings stored yet")
        val sessions = AppDatabase.get(context).chargingSessionDao().getAll(vid)
            .filter { it.status == "completed" }.take(count)
        if (sessions.isEmpty()) return@withContext err("no completed charging session recorded")
        JSONObject().put("sessions", JSONArray().apply {
            sessions.forEach { s ->
                put(JSONObject().put("type", s.chargeType ?: "unknown")
                    .put("started", age(s.startedAt))
                    .put("minutes", minutesBetween(s.startedAt, s.endedAt) ?: JSONObject.NULL)
                    .put("from_pct", s.startSoc?.let { round1(it) } ?: JSONObject.NULL)
                    .put("to_pct", s.endSoc?.let { round1(it) } ?: JSONObject.NULL))
            }
        }).put("source", "charging sessions the phone recorded from the car (display charge)")
    }

    private fun minutesBetween(a: String?, b: String?): Long? = runCatching {
        Duration.between(Instant.parse(a), Instant.parse(b)).toMinutes()
    }.getOrNull()

    // -- the route's revisions and the driver's changes -------------------------------

    /** What changed and why: the plan's revisions, a waiting suggestion, Google Maps. */
    private fun routeChanges(): JSONObject {
        val t = TripMonitor.state.value ?: return err("no trip is active")
        val r = t.route
        val out = JSONObject().put("revision", r.revision)
            .put("history", JSONArray().apply {
                r.history.takeLast(5).forEach { v ->
                    put(JSONObject().put("revision", v.number).put("why", v.reason)
                        .put("minutes_ago", (System.currentTimeMillis() - v.atMs) / 60_000)
                        .put("charging_stops", JSONArray(v.waypoints.filter { it.isStop }.map { it.name }))
                        .put("arrival_charge_pct", v.arrivalPct?.let { round1(it) } ?: JSONObject.NULL)
                        .apply { v.evidence?.let { put("evidence", RouteRevisions.evidenceJson(it)) } })
                }
            })
        r.proposal?.let { p ->
            out.put("suggestion_waiting", JSONObject().put("change", p.change).put("why", p.reason)
                .put("urgent", p.urgent)
                .apply { p.evidence?.let { put("evidence", RouteRevisions.evidenceJson(it)) } }
                .put("next", "ask the driver; answer_suggestion with their answer"))
        }
        // Explain from these figures only: they are what the app measured.
        out.put("explain_from", "the evidence objects: readings, their age and caveats; " +
            "say a figure only if it is there")
        if (r.sync.provider == RouteRevisions.GOOGLE_MAPS) {
            out.put("google_maps", when {
                !r.needsHandoff -> "has the current plan (revision ${r.sync.sentRevision})"
                r.sync.sentRevision == null -> "not given the trip yet"
                else -> "has an older plan (revision ${r.sync.sentRevision}); it cannot be " +
                    "updated from here, only handed the trip again (start_navigation)"
            })
        }
        if (!r.picks.isEmpty) {
            out.put("drivers_choices", JSONObject().put("chargers_avoided", r.picks.avoidChargers.size)
                .put("chargers_chosen", r.picks.useChargers.size)
                .put("networks_avoided", JSONArray(r.picks.avoidNetworks.toList())))
        }
        return out
    }

    /** The driver's answer to a waiting suggestion: switch to it, or keep their plan. */
    private suspend fun answerSuggestion(switch: Boolean): JSONObject {
        val t = TripMonitor.state.value ?: return err("no trip is active")
        if (t.route.proposal == null) return err("no suggestion is waiting")
        return JSONObject().put("ok", if (switch) TripMonitor.accept(context, byVoice = true)
        else TripMonitor.decline())
    }

    /**
     * A change to the route needs the driver's yes: the first call says what
     * would change and must be put to the driver; only the same call again,
     * with confirm=true and within CONFIRM_MS, does it.
     */
    private suspend fun confirmed(key: String, confirm: Boolean, what: String,
                                  act: suspend () -> JSONObject): JSONObject {
        if (gate.pass(key, confirm, System.currentTimeMillis())) return act()
        return JSONObject().put("needs_confirmation", "$what Ask the driver; only if they say " +
            "yes, call again with confirm=true.")
    }

    private fun stopOf(t: ActiveTrip, n: Int): Stop? = t.result.plan.stops.getOrNull(n - 1)

    private suspend fun replanned(t: ActiveTrip, picks: Picks, why: String): JSONObject {
        val after = TripMonitor.changePicks(context, picks, why) ?: return err("no trip is active")
        val (km, _) = where(after.result)
        return summary(after.result, km).put("revision", after.route.revision).put("why", why)
            .put("google_maps", if (after.route.needsHandoff) "needs the new plan: offer " +
                "start_navigation" else "up to date or not used")
    }

    private suspend fun avoidStop(n: Int, confirm: Boolean): JSONObject {
        val t = TripMonitor.state.value ?: return err("no trip is active")
        val s = stopOf(t, n) ?: return err("there is no charging stop $n")
        if (s.charger.id.isBlank()) return err("that charger has no id to avoid it by")
        return confirmed("avoid_stop:${s.charger.id}", confirm,
            "This re-plans without ${s.charger.name} for the rest of the trip.") {
            replanned(t, t.route.picks.avoid(s.charger.id), "you asked to avoid ${s.charger.name}")
        }
    }

    private suspend fun useAlternative(n: Int, k: Int, confirm: Boolean): JSONObject {
        val t = TripMonitor.state.value ?: return err("no trip is active")
        val s = stopOf(t, n) ?: return err("there is no charging stop $n")
        val a = s.alternatives.getOrNull(k - 1) ?: return err("stop $n has no alternative $k")
        if (a.charger.id.isBlank()) return err("that charger has no id to choose it by")
        return confirmed("use:${s.charger.id}>${a.charger.id}", confirm,
            "This charges at ${a.charger.name} instead of ${s.charger.name}.") {
            replanned(t, t.route.picks.insteadOf(s.charger.id, a.charger.id),
                "you chose ${a.charger.name} instead of ${s.charger.name}")
        }
    }

    private suspend fun avoidNetwork(name: String, confirm: Boolean): JSONObject {
        val t = TripMonitor.state.value ?: return err("no trip is active")
        if (name.isBlank()) return err("say which network")
        return confirmed("network:${name.trim().lowercase()}", confirm,
            "This re-plans the rest of the trip without any ${name.trim()} charger.") {
            replanned(t, t.route.picks.avoidNetwork(name), "you asked to avoid ${name.trim()}")
        }
    }

    private suspend fun directions(count: Int): JSONObject {
        // Navigating: the route actually being driven, from where the car is on it.
        Navigator.state.value?.let { s ->
            val route = s.route
            val g = s.guidance
            if (route != null && g != null) {
                return JSONObject().put("from_here", JSONArray().apply {
                    route.maneuvers.filter { it.atKm > g.kmAlong }.take(count).forEach { m ->
                        put(JSONObject().put("in", dist(m.atKm - g.kmAlong))
                            .put("instruction", m.instruction).put("road", m.road)
                            .apply {
                                if (m.exit.isNotBlank()) put("exit", m.exit)
                                NavSpeech.laneAdvice(m.lanes)?.let { put("lanes", it) }
                            })
                    }
                }).put("to", s.target?.label ?: s.trip.destination.label)
            }
        }
        val t = TripMonitor.state.value ?: return err("no trip is active")
        val steps = t.result.steps
        if (steps.isEmpty()) return err("this plan has no turn-by-turn steps")
        val (km, _) = where(t.result)
        val from = km ?: 0.0
        return JSONObject().put("from_here", JSONArray().apply {
            steps.filter { it.km + it.lengthKm > from }.take(count).forEach { st ->
                put(JSONObject().put("in", dist((st.km - from).coerceAtLeast(0.0)))
                    .put("instruction", st.instruction).put("road", st.road)
                    .apply { st.exitNumber?.let { put("exit", it) } })
            }
        }).put("note", "the planned route's steps; navigation is not running")
    }

    private suspend fun planTrip(destination: String, mode: String?): JSONObject {
        if (destination.isBlank()) return err("say where to")
        val dest = TripPlanner.search(context, destination.trim()).firstOrNull()
            ?: return err("no place found for $destination")
        val r = TripPlanner.plan(context, dest, null, null, mode)
        TripMonitor.start(context, r)
        return summary(r, 0.0).apply {
            r.nav?.trafficDelayS?.let { put("traffic_delay_min", Math.round(it / 60)) }
            if (r.routes.size > 1) put("other_ways", JSONArray().apply {
                r.routes.drop(1).forEach { w ->
                    put(JSONObject().put("via", w.summary).put("driving_min", Math.round(w.durationS / 60))
                        .put("distance", dist(w.distanceKm)))
                }
            })
        }.put("next", "planned and being watched; call start_navigation when the driver wants " +
            "directions")
    }

    private suspend fun replan(): JSONObject {
        val t = TripMonitor.replan(context) ?: return err("no trip is active")
        return summary(t.result, where(t.result).first).put("watch_status", t.note)
    }

    private suspend fun startNavigation(): JSONObject {
        val t = TripMonitor.state.value ?: return err("no trip is planned")
        val inApp = withContext(Dispatchers.Main) { TripMonitor.drive(context, t.result) }
        if (inApp) {
            return JSONObject().put("ok", "navigation started in the app: it speaks each turn, " +
                "with lanes, via ${t.result.plan.stops.size} charging stops")
        }
        // Route settings hand trips to Google Maps. From the background Android
        // lets it open only with "display over other apps" allowed; otherwise
        // the notification's tap opens it (MapsHandoff).
        if (!MapsHandoff.canOpenNow(context)) {
            TripMonitor.offerMapsTap(context, t.result)
            return JSONObject().put("ok", "Android needs a tap to open Google Maps from the " +
                "background: tell the driver to tap the 'Send to Google Maps' notification")
        }
        withContext(Dispatchers.Main) { startTrip(context, t.result) }
        return JSONObject().put("ok", "Google Maps opened with ${t.result.plan.stops.size} " +
            "charging stops and the destination -- the current plan, including any re-plan")
    }

    private fun navigation(): JSONObject {
        val s = Navigator.state.value ?: return err("navigation is not running; start_navigation " +
            "starts it for a planned trip")
        val out = JSONObject().put("to", s.trip.destination.label).put("phase", s.phase.name.lowercase())
            .put("status", s.note).put("live_traffic", s.traffic)
        s.target?.let { t ->
            out.put("heading_for", t.label)
            t.stop?.let { out.put("heading_for_is_charging_stop", true) }
        }
        val g = s.guidance ?: return out
        g.next?.let { m ->
            out.put("next_turn", JSONObject().put("in", dist(g.toNextKm))
                .put("instruction", m.instruction).put("road", m.road)
                .apply {
                    if (m.exit.isNotBlank()) put("exit", m.exit)
                    NavSpeech.laneAdvice(m.lanes)?.let { put("lanes", it) }
                })
        }
        g.then?.let { out.put("then", it.instruction) }
        out.put("off_route", g.offRoute)
            .put("this_leg_remaining", dist(g.remainingKm))
            .put("this_leg_minutes", Math.round(g.remainingS / 60))
        s.toEndS?.let { out.put("minutes_to_destination_incl_charging", Math.round(it / 60)) }
        s.route?.let { r ->
            r.trafficDelayS?.let { out.put("traffic_delay_min", Math.round(it / 60)) }
            r.trafficAhead(g.kmAlong, 30.0)?.let { run ->
                out.put("heavy_traffic_ahead", JSONObject()
                    .put("starts_in", dist(maxOf(0.0, run.fromKm - g.kmAlong)))
                    .put("length", dist(run.toKm - maxOf(run.fromKm, g.kmAlong)))
                    .put("level", run.level))
            }
        }
        return out.put("avoiding", JSONArray().apply {
            if (s.avoid.motorways) put("motorways")
            if (s.avoid.tolls) put("tolls")
            if (s.avoid.ferries) put("ferries")
        })
    }

    private suspend fun reroute(args: JSONObject): JSONObject {
        val s = Navigator.state.value ?: return err("navigation is not running")
        if (args.optBoolean("faster", false)) {
            return JSONObject().put("result", Navigator.checkTraffic(announce = false))
        }
        fun flag(key: String, now: Boolean) = if (args.has(key)) args.optBoolean(key, now) else now
        val avoid = s.avoid.copy(
            motorways = flag("avoid_motorways", s.avoid.motorways),
            tolls = flag("avoid_tolls", s.avoid.tolls),
            ferries = flag("avoid_ferries", s.avoid.ferries))
        Navigator.reroute(avoid)
        return JSONObject().put("ok", "re-routing from here").put("avoiding", JSONArray().apply {
            if (avoid.motorways) put("motorways")
            if (avoid.tolls) put("tolls")
            if (avoid.ferries) put("ferries")
        })
    }

    private suspend fun chargers(radiusKm: Double, minKw: Double): JSONObject =
        withContext(Dispatchers.IO) {
            val here = PhoneLocation.current(context, maxAgeMs = 10 * 60_000L)
                ?: return@withContext err("no recent position from the phone")
            val s = RoutePrefs.load(context)
            val own = Prices.ownPrices(s.ownPrices)
            val found = RouteServices(s.orsKey, s.ocmKey, minChargerKw = minKw)
                .chargersNear(here.latitude, here.longitude, radiusKm)
                .map { c ->
                    val (price, fee, source) = Prices.priceFor(c.operator, c.usageCost, own,
                        s.defaultPricePerKwh)
                    Triple(c, com.odbscanner.route.Planner.haversineKm(here.latitude,
                        here.longitude, c.lat, c.lon), Triple(price, fee, source))
                }
                .sortedBy { it.second }
            JSONObject().put("found", found.size).put("chargers", JSONArray().apply {
                found.take(8).forEach { (c, d, p) ->
                    put(JSONObject().put("name", c.name).put("operator", c.operator)
                        .put("address", c.address).put("away", dist(d))
                        .put("power_kw", c.powerKw).put("price_per_kwh_gbp", p.first)
                        .put("session_fee_gbp", p.second).put("price_source", p.third))
                }
            })
        }

    // -- helpers ----------------------------------------------------------------------
    companion object {
        /** The trip for the assistant; [kmAlong]: where the phone is on it, null when not known. */
        internal fun summary(r: TripResult, kmAlong: Double?): JSONObject {
            val p = r.plan
            val ahead = p.stops.filter { kmAlong == null || it.km > kmAlong - 1.0 }
            // Without the phone's position the distances are from the start: said
            // so, never passed off as from here.
            val origin = if (kmAlong != null) "from_here" else "from_start"
            val out = JSONObject()
                .put("destination", r.destination.label)
                .put("mode", p.mode)
                .put("status", p.status)
                .put("total", dist(p.distanceKm))
                .put("arrival_charge_pct", p.arrivalPct?.let { round1(it) } ?: JSONObject.NULL)
                .put("arrival_likely_range_pct", p.arrivalBand?.let {
                    JSONArray().put(round1(it.lowPct)).put(round1(it.highPct)) } ?: JSONObject.NULL)
                .put("charging_cost_gbp", round2(p.cost))
                .put("driving_min", Math.round(p.driveS / 60))
                .put("charging_min", Math.round(p.chargeS / 60))
            if (kmAlong != null) out.put("remaining", dist((p.distanceKm - kmAlong).coerceAtLeast(0.0)))
            else out.put("position", "unknown: the phone has no recent fix, so the distances are " +
                "from the trip's start and the stops listed may include ones already passed")
            p.unreachableAtKm?.let { out.put("charge_runs_out_near", dist(it)) }
            out.put("stops_ahead", JSONArray().apply {
                ahead.forEach { s ->
                    put(JSONObject()
                        .put("charger", s.charger.name)
                        .put("operator", s.charger.operator)
                        .put("address", s.charger.address)
                        .put(origin, dist(s.km - (kmAlong ?: 0.0)))
                        .put("arrive_pct", s.arrivePct).put("leave_pct", s.departPct)
                        .put("charging_min", s.chargeMin).put("power_kw", s.charger.powerKw)
                        .put("cost_gbp", s.cost).put("price_per_kwh_gbp", s.pricePerKwh)
                        .put("price_source", s.priceSource)
                        .put("road_there", roadNear(r, s.km))
                        .put("stop", p.stops.indexOf(s) + 1)
                        .put("arrive_likely_range_pct", if (s.arriveLowPct != null && s.arriveHighPct != null)
                            JSONArray().put(s.arriveLowPct).put(s.arriveHighPct) else JSONObject.NULL)
                        .put("alternatives", JSONArray().apply {
                            s.alternatives.take(3).forEachIndexed { k, a ->
                                put(JSONObject().put("alternative", k + 1).put("charger", a.charger.name)
                                    .put("operator", a.charger.operator).put("power_kw", a.charger.powerKw)
                                    .put("from_stop", dist(a.km - s.km))
                                    .put("price_per_kwh_gbp", round2(a.pricePerKwh)))
                            }
                        }))
                }
            })
            return out
        }

        private fun roadNear(r: TripResult, km: Double): String =
            r.steps.lastOrNull { it.km <= km }?.let { st ->
                listOf(st.road, st.instruction).firstOrNull { it.isNotBlank() } ?: ""
            } ?: ""

        private const val MILES_PER_KM = 0.621371

        val BATTERY_KEYS = listOf(
            "soc_abs" to "charge_bms_pct",
            "soc_normal" to "charge_display_pct",
            "hv_energy_content" to "energy_now",
            "hv_energy_max" to "energy_when_full",
            "range_displayed" to "range_shown_by_car",
            "bat_temp_max" to "battery_temp_max",
            "bat_temp_min" to "battery_temp_min",
            "pack_voltage" to "pack_voltage",
            "aux_12v_voltage" to "battery_12v",
        )

        fun dist(km: Double): JSONObject = JSONObject()
            .put("km", round1(km)).put("miles", round1(km * MILES_PER_KM))

        fun round1(v: Double) = Math.round(v * 10.0) / 10.0
        fun round2(v: Double) = Math.round(v * 100.0) / 100.0

        fun err(message: String): JSONObject = JSONObject().put("error", message)

        /** "12 min ago" from a stored Instant; "unknown" when it cannot be read. */
        fun age(ts: String?, now: Instant = Instant.now()): String {
            val t = ts?.let { runCatching { Instant.parse(it) }.getOrNull() } ?: return "unknown"
            val m = Duration.between(t, now).toMinutes()
            return when {
                m < 1 -> "just now"
                m < 60 -> "$m min ago"
                m < 48 * 60 -> "${m / 60} h ago"
                else -> "${m / (24 * 60)} days ago"
            }
        }

        fun reading(m: MeasurementEntity): JSONObject {
            val v = m.value
            val (value, unit) = when {
                v == null -> (m.textValue ?: "") to m.unit
                m.key.startsWith("hv_energy") -> round1(v / 1000.0) to "kWh"
                else -> round2(v) to m.unit
            }
            return JSONObject().put("value", value).put("unit", unit).put("age", age(m.ts))
                // Where it came from: the car's own report, a calculation, a simulation.
                .put("source", m.provenance)
        }

        private val VIN = Regex("\\b[A-HJ-NPR-Z0-9]{17}\\b")

        /** Backstop: no VIN-shaped string ever leaves in a tool result. */
        fun redactVin(text: String): String = VIN.replace(text, "[VIN]")

        /**
         * The tools that act rather than look something up. A turn that ran
         * one is not handed to a backup AI when it fails (FallbackChatSession):
         * the backup could plan or start navigation a second time.
         */
        val CHANGES_STATE = setOf("plan_trip", "replan_trip", "start_navigation", "reroute",
            "answer_suggestion", "avoid_stop", "use_alternative", "avoid_network")

        /** How long a "this would change…" waits for the driver's yes. */
        const val CONFIRM_MS = 120_000L

        /** The one change put to the driver at a time. */
        private val gate = ConfirmGate(CONFIRM_MS)

        /** The tools as the AI sees them: names, what they do, JSON-schema arguments. */
        val SPECS: List<ToolSpec> = listOf(
            spec("get_battery", "Latest battery readings: charge (BMS and display), energy now " +
                "and when full, the car's displayed range, temperatures, pack and 12 V voltage, " +
                "cell voltage spread. Each with how old it is."),
            spec("get_fault_codes", "Stored diagnostic trouble codes the phone last read, with " +
                "module, status and when last seen."),
            spec("get_trip", "The trip being driven: destination, distance left, arrival charge, " +
                "cost, and each charging stop ahead with distance from here, charge on arrival " +
                "and leaving, minutes, price and the road it is on."),
            spec("get_directions", "The next turn-by-turn steps of the planned route from here: " +
                "distance, instruction and road -- for junctions and exits ahead.",
                "count" to JSONObject().put("type", "integer").put("description",
                    "How many steps, 1-10 (default 5)")),
            spec("find_chargers", "Fast chargers near the car's current position, nearest first, " +
                "with power, price per kWh and where the price came from.",
                "radius_km" to JSONObject().put("type", "number")
                    .put("description", "Search radius in km (default 10)"),
                "min_kw" to JSONObject().put("type", "number")
                    .put("description", "Minimum power in kW (default 50)")),
            spec("plan_trip", "Plan a trip from here to a destination with charging stops, " +
                "and start watching it. Does not open navigation.",
                "destination" to JSONObject().put("type", "string")
                    .put("description", "Town, postcode or address"),
                "mode" to JSONObject().put("type", "string")
                    .put("enum", JSONArray(listOf("fastest", "balanced", "cheapest")))
                    .put("description", "How to trade price against time (default: the saved choice)"),
                required = listOf("destination")),
            spec("replan_trip", "Re-plan the active trip from the current position and the " +
                "latest charge reading, and return the new plan."),
            spec("start_navigation", "Start turn-by-turn navigation of the planned trip in the " +
                "app (spoken turns and lanes), via its charging stops -- or, when Route settings " +
                "hand trips to Google Maps, send the current plan (after a re-plan too) to Google " +
                "Maps. Only when the driver asks to navigate, for directions, or to update or " +
                "send the route to Google Maps."),
            spec("get_charging_history", "Recent charging sessions the phone recorded: AC or " +
                "DC, how long ago, how long, from and to what charge.",
                "count" to JSONObject().put("type", "integer").put("description", "1 to 20")),
            spec("get_route_changes", "What has changed with the route and why: the plan's " +
                "latest revisions (reason, charging stops, arrival charge), a suggested plan " +
                "waiting for the driver, whether Google Maps has the current plan, and the " +
                "driver's charger choices."),
            spec("answer_suggestion", "Only with the driver's own answer to a suggested plan " +
                "(see get_route_changes): switch=true to take it (and hand it to Google Maps if " +
                "trips go there), false to keep their plan.",
                "switch" to JSONObject().put("type", "boolean"), required = listOf("switch")),
            spec("avoid_stop", "Re-plan without a planned charging stop, for the rest of the " +
                "trip. First call without confirm: it says what would change -- tell the driver " +
                "and call again with confirm=true only if they say yes.",
                "stop" to JSONObject().put("type", "integer").put("description",
                    "The stop's number in get_trip (1 = next)"),
                "confirm" to JSONObject().put("type", "boolean"), required = listOf("stop")),
            spec("use_alternative", "Charge at one of a stop's alternatives (get_trip lists " +
                "them) instead. Same confirm rule as avoid_stop.",
                "stop" to JSONObject().put("type", "integer"),
                "alternative" to JSONObject().put("type", "integer"),
                "confirm" to JSONObject().put("type", "boolean"),
                required = listOf("stop", "alternative")),
            spec("avoid_network", "Re-plan the rest of the trip without any charger of a " +
                "network (by name, e.g. 'Shell Recharge'). Same confirm rule as avoid_stop.",
                "network" to JSONObject().put("type", "string"),
                "confirm" to JSONObject().put("type", "boolean"), required = listOf("network")),
            spec("get_navigation", "While navigating: the next turn (distance, instruction, " +
                "road, exit, which lanes), the one after, time and distance left, arrival " +
                "including charging, what traffic adds, and heavy traffic ahead."),
            spec("reroute", "While navigating: re-route from here, optionally keeping off " +
                "motorways, tolls or ferries from now on; or, with faster=true, check live " +
                "traffic for a faster way and take it if it clearly is.",
                "avoid_motorways" to JSONObject().put("type", "boolean"),
                "avoid_tolls" to JSONObject().put("type", "boolean"),
                "avoid_ferries" to JSONObject().put("type", "boolean"),
                "faster" to JSONObject().put("type", "boolean").put("description",
                    "Only look for a faster way in today's traffic")),
            spec("get_readings", "Latest stored value of every measurement whose key contains " +
                "the filter (e.g. 'temp', 'soc', 'odometer'), with units and age.",
                "filter" to JSONObject().put("type", "string")
                    .put("description", "Part of the measurement key; empty for all")),
            spec("get_history", "How one measurement changed: oldest and newest value with " +
                "their age, minimum, maximum and average over the stored values.",
                "key" to JSONObject().put("type", "string")
                    .put("description", "Measurement key from get_readings, e.g. soc_abs"),
                "count" to JSONObject().put("type", "integer")
                    .put("description", "How many recent values (default 60)"),
                required = listOf("key")),
        )

        private fun spec(
            name: String, description: String, vararg props: Pair<String, JSONObject>,
            required: List<String> = emptyList(),
        ) = ToolSpec(name, description, JSONObject()
            .put("type", "object")
            .put("properties", JSONObject().apply { props.forEach { (k, v) -> put(k, v) } })
            .put("required", JSONArray(required)))

        /** The system prompt with the answer style: brief, normal or detailed. */
        fun prompt(style: String): String = SYSTEM_PROMPT + "\n\n" + when (style) {
            "brief" -> "Style: brief. The driver is driving: one or two short sentences, the " +
                "answer first. Offer detail only if asked."
            "detailed" -> "Style: detailed. The driver asked for detail: up to eight spoken " +
                "sentences, still plain words, no lists; explain how the app worked it out."
            else -> "Style: normal: one to three short sentences."
        }

        val SYSTEM_PROMPT = """
            You are the voice assistant in the driver's electric car, a Volkswagen ID.3, inside
            the ODB Scanner app on their phone. With the tools you can read the car's stored
            battery readings and fault codes, the trip plan with its charging stops and prices,
            the route's upcoming roads and junctions, chargers nearby, and every stored
            measurement and its history, and the navigation while it runs. You cannot control
            the car: everything is read-only, apart from planning or re-planning a trip and
            starting or re-routing navigation.

            While navigating, the app itself speaks every turn and traffic warning. Answer
            questions about the turns ahead, lanes, arrival time or traffic from get_navigation;
            use reroute when the driver asks to avoid roads or for a faster way.

            The driver is usually driving and hears your answer read aloud:
            - Answer in one to three short spoken sentences. No lists, markdown, symbols or emoji.
            - Lead with the answer. Round numbers sensibly. Say distances in miles and money in
              pounds (the tools give km and miles).
            - Say anything safety-relevant first and plainly: charge running low, a serious fault.
            - Use the tools for every fact about the car, the trip or chargers; never guess a
              reading. Mention a reading's age when it is over an hour old.
            - Never ask the driver to read or type anything.
            - If a tool says the data is not there, say so in a few words.
            - Never work out battery, range, arrival or charging figures yourself: the tools give
              the app's own calculations (get_trip's live_check answers "will I make it?").
              Explain them; if they are missing or old, say so rather than estimate.
        """.trimIndent()
    }
}

/**
 * A change the assistant wants to make waits for the driver's yes: a call
 * without confirm puts it to them; only the same change with confirm, within
 * [windowMs], goes through. A confirm for anything else -- or with nothing
 * asked -- does not. Pure, so it is unit-tested.
 */
class ConfirmGate(private val windowMs: Long) {
    private var waiting: Pair<String, Long>? = null

    @Synchronized
    fun pass(key: String, confirm: Boolean, nowMs: Long): Boolean {
        val w = waiting
        if (confirm && w != null && w.first == key && nowMs - w.second in 0..windowMs) {
            waiting = null
            return true
        }
        waiting = key to nowMs
        return false
    }
}
