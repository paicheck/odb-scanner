package com.odbscanner.route

import kotlin.math.abs
import kotlin.math.asin
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * EV trip planner: energy along a route, where to charge, and what it costs.
 *
 * A line-for-line port of `routing/planner.py`; both are held to
 * `tests/data/route_vectors.json` (PlannerTest here, test_route_planner.py
 * there), so the phone, the car screen and the dashboard give the same plan
 * for the same trip. Change the Python first, regenerate the vectors, then
 * follow here.
 *
 * Energy for a stretch = distance x flat consumption + climbing energy
 * (m g dh, through a drivetrain efficiency) - recovered descent energy. Speed,
 * temperature, wind and HVAC live inside the one consumption figure; the
 * detour to a charger costs time but its energy is ignored.
 *
 * Stops: every fast charger near the route is a candidate; dynamic
 * programming over (charger, leave-at level in 5 % steps) picks the feasible
 * plan with the lowest money + value-of-time x (charging + stop overhead +
 * detour). The mode sets the value of time. Pure: no I/O, no Android.
 */

const val G = 9.81
const val EARTH_RADIUS_KM = 6371.0088

/** ID.3 Pro 58 kWh DC charging power by state of charge: (SoC %, max kW). */
val ID3_58_CURVE: List<Pair<Double, Double>> = listOf(
    0.0 to 110.0, 30.0 to 110.0, 40.0 to 95.0, 50.0 to 80.0, 60.0 to 66.0,
    70.0 to 55.0, 80.0 to 43.0, 90.0 to 25.0, 100.0 to 7.0,
)

val MODES = listOf("fastest", "balanced", "cheapest")
const val FASTEST_TIME_VALUE = 1000.0      // per hour: time dominates, money breaks ties
const val CHEAPEST_TIME_VALUE = 3.0        // per hour: enough to refuse silly detours
const val DETOUR_ROAD_FACTOR = 1.4
const val DETOUR_SPEED_KMH = 40.0
const val NEAREST_STRIDE = 10
// Open Charge Map UsageTypeID: public, but only for members or after notice --
// not for a passing car without an account. Left out under publicOnly. (The
// private and staff-only types are dropped when chargers are fetched.)
val MEMBERS_ONLY_USAGE = setOf(4, 7)
val USAGE_LABELS = mapOf(1 to "public", 4 to "public, membership required",
    5 to "public, pay at location", 7 to "public, notice required")
// Search-only cost of NOT using a charger the driver chose: large enough that
// the plan uses it whenever it can be reached. Never part of a shown cost.
const val PIN_BONUS = 1e6
// A charger the driver chose counts up to this far from the route (an "add
// this stop" near a town off the way); others only within searchRadiusKm.
// The detour is still costed as time, both ways.
const val PIN_MAX_OFFSET_KM = 30.0
const val ALT_WINDOW_KM = 30.0             // alternatives: this far either side of a stop
const val ALT_MAX = 4
// Weather (routing/planner.py): air at 15 °C, and a 10 m forecast wind is about
// this share of itself at a car's height in open country.
const val AIR_DENSITY = 1.225
const val WIND_AT_ROAD = 0.7
// Without a measured spread, consumption is taken to be within this share of
// the planned rate either way, and the plan says it is an assumption.
const val ASSUMED_RATE_ERROR = 0.10

data class CarModel(
    val usableKwh: Double,
    val consumptionKwhPerKm: Double,
    val massKg: Double = 1950.0,
    val drivetrainEff: Double = 0.90,
    val regenEff: Double = 0.60,
    val chargeCurve: List<Pair<Double, Double>> = ID3_58_CURVE,
    val dragAreaM2: Double = 0.63,        // Cd x frontal area: ID.3 0.267 x 2.36 m²
)

/** The forecast from [km] along the route until the next one (routing/weather.py). */
data class Conditions(
    val km: Double,
    val tempC: Double? = null,
    val windKmh: Double? = null,       // at 10 m
    val windFromDeg: Double? = null,   // where it blows from, clockwise from north
)

/** How far real consumption may be from the plan, kWh/km: [low] (negative: less) to [high]. */
data class RateError(val low: Double, val high: Double, val basis: String)

data class ArrivalBand(val lowPct: Double, val highPct: Double, val basis: String,
                       val belowReserve: Boolean)

data class PlanSettings(
    val reservePct: Double = 10.0,
    val chargeToPct: Double = 80.0,
    val minChargerKw: Double = 50.0,
    val searchRadiusKm: Double = 5.0,
    val mode: String = "balanced",
    val timeValuePerHour: Double = 15.0,
    val defaultPricePerKwh: Double = 0.79,
    val stopOverheadMin: Double = 10.0,
    val levelStepPct: Double = 5.0,
    val minChargePct: Double = 10.0,
    val chargingEff: Double = 0.92,
    // Charger choice. Networks match the operator's name, ignoring case, by any
    // part ("ionity" matches "IONITY GmbH"). Chargers are Open Charge Map IDs.
    val avoidNetworks: List<String> = emptyList(),   // never used
    val preferNetworks: List<String> = emptyList(),  // cheaper by preferDiscount, when choosing
    val preferDiscount: Double = 0.25,
    val publicOnly: Boolean = false,                 // also leave out MEMBERS_ONLY_USAGE
    val avoidChargers: Set<String> = emptySet(),     // never used
    val useChargers: Set<String> = emptySet(),       // used whenever reachable
) {
    fun timeValue(): Double = when (mode) {
        "fastest" -> FASTEST_TIME_VALUE
        "cheapest" -> CHEAPEST_TIME_VALUE
        else -> timeValuePerHour
    }
}

data class Charger(
    val name: String,
    val lat: Double,
    val lon: Double,
    val powerKw: Double,
    val operator: String = "",
    val address: String = "",
    val pricePerKwh: Double? = null,     // null: settings.defaultPricePerKwh
    val fee: Double = 0.0,
    val priceSource: String = "",
    val usageCost: String = "",          // Open Charge Map's price text, as listed
    val id: String = "",                 // Open Charge Map ID, for avoid / use
    val usageType: Int? = null,          // Open Charge Map UsageTypeID
)

/** Another charger near a stop: what the driver can pick instead. */
data class Alternative(
    val charger: Charger,
    val km: Double,
    val detourKm: Double,
    val pricePerKwh: Double,
    val preferred: Boolean,
)

/** A route point: elevation in metres, or null when the provider gave none. */
data class RoutePoint(val lat: Double, val lon: Double, val ele: Double? = null)

data class Stop(
    val km: Double,
    val charger: Charger,
    val arrivePct: Double,
    val departPct: Double,
    val chargeMin: Long,
    val kwh: Double,
    val pricePerKwh: Double,
    val priceSource: String,
    val cost: Double,
    val detourKm: Double,
    val preferred: Boolean = false,
    val alternatives: List<Alternative> = emptyList(),
    val arriveLowPct: Double? = null,     // the leg's range (Planner.addBands)
    val arriveHighPct: Double? = null,
    val arriveLowBelowReserve: Boolean = false,
    val legKm: Double = 0.0,              // from the start or the last stop to here
    val legKwh: Double = 0.0,             // what the battery gave on that leg
) {
    val access: String get() = USAGE_LABELS[charger.usageType] ?: ""
}

data class Plan(
    val status: String,               // "direct" | "with_stops" | "unreachable"
    val distanceKm: Double,
    val driveS: Double,
    val startPct: Double,
    val arrivalPct: Double?,
    val stops: List<Stop>,
    val profile: List<Pair<Double, Double>>,   // (km, pct)
    val unreachableAtKm: Double?,
    val chargeS: Double,
    val cost: Double,
    val mode: String,
    val assumptions: List<String>,
    val windKwh: Double? = null,           // what the forecast wind cost (negative: saved)
    val arrivalBand: ArrivalBand? = null,
    val lastLegKm: Double? = null,         // the last stop (or start) to the end; null: unreachable
    val lastLegKwh: Double? = null,
)

object Planner {

    /** Python's round(): half to even, at [digits] decimals. */
    fun round(v: Double, digits: Int = 0): Double =
        java.math.BigDecimal(v).setScale(digits, java.math.RoundingMode.HALF_EVEN).toDouble()

    fun haversineKm(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
        val p1 = Math.toRadians(lat1)
        val p2 = Math.toRadians(lat2)
        val dp = p2 - p1
        val dl = Math.toRadians(lon2 - lon1)
        val a = sin(dp / 2) * sin(dp / 2) + cos(p1) * cos(p2) * sin(dl / 2) * sin(dl / 2)
        return 2 * EARTH_RADIUS_KM * asin(sqrt(min(1.0, a)))
    }

    fun cumulativeKm(points: List<RoutePoint>): DoubleArray {
        val out = DoubleArray(points.size)
        for (i in 1 until points.size) {
            val a = points[i - 1]
            val b = points[i]
            out[i] = out[i - 1] + haversineKm(a.lat, a.lon, b.lat, b.lon)
        }
        return out
    }

    /** Energy for one stretch of road; negative only through regeneration. */
    fun segmentKwh(distKm: Double, dhM: Double, car: CarModel): Double {
        val flat = distKm * car.consumptionKwhPerKm
        val potential = car.massKg * G * abs(dhM) / 3.6e6
        return if (dhM > 0) flat + potential / car.drivetrainEff
        else flat - potential * car.regenEff
    }

    /** Power available at a SoC: the car's curve, capped by the charger. */
    fun chargePowerKw(pct: Double, curve: List<Pair<Double, Double>>, chargerKw: Double): Double {
        val pts = curve.sortedWith(compareBy({ it.first }, { it.second }))
        var kw: Double
        if (pct <= pts.first().first) {
            kw = pts.first().second
        } else if (pct >= pts.last().first) {
            kw = pts.last().second
        } else {
            kw = pts.last().second
            for (i in 0 until pts.size - 1) {
                val (s0, k0) = pts[i]
                val (s1, k1) = pts[i + 1]
                if (s0 <= pct && pct <= s1) {
                    kw = k0 + (k1 - k0) * (pct - s0) / (s1 - s0)
                    break
                }
            }
        }
        return max(0.5, min(kw, chargerKw))
    }

    /** Time to charge between two SoCs, integrated in 1 % steps. */
    fun chargeSeconds(fromPct: Double, toPct: Double, car: CarModel, chargerKw: Double): Double {
        if (toPct <= fromPct) return 0.0
        val stepKwh = car.usableKwh / 100.0
        var seconds = 0.0
        var pct = fromPct
        while (pct < toPct - 1e-9) {
            val step = min(1.0, toPct - pct)
            val kw = chargePowerKw(pct + step / 2, car.chargeCurve, chargerKw)
            seconds += step * stepKwh / kw * 3600.0
            pct += step
        }
        return seconds
    }

    /** Seconds to charge from 0 % to each whole percent (read interpolated). */
    fun chargeTable(car: CarModel, chargerKw: Double): DoubleArray {
        val stepKwh = car.usableKwh / 100.0
        val t = DoubleArray(101)
        for (s in 0 until 100) {
            t[s + 1] = t[s] + stepKwh / chargePowerKw(s + 0.5, car.chargeCurve, chargerKw) * 3600.0
        }
        return t
    }

    fun tableSeconds(t: DoubleArray, pctIn: Double): Double {
        val pct = min(100.0, max(0.0, pctIn))
        val i = min(99, pct.toInt())
        return t[i] + (pct - i) * (t[i + 1] - t[i])
    }

    /** Leave-at levels: whole steps above the reserve and below the limit, then the limit. */
    fun leaveLevels(s: PlanSettings): List<Double> {
        val out = mutableListOf<Double>()
        var k = 1
        while (k * s.levelStepPct < s.chargeToPct - 1e-9) {
            val x = k * s.levelStepPct
            if (x > s.reservePct + 1e-9) out.add(x)
            k += 1
        }
        out.add(s.chargeToPct)
        return out
    }

    /** (index, km) of the route point nearest a place: coarse pass, then refine. */
    fun nearestPoint(points: List<RoutePoint>, lat: Double, lon: Double): Pair<Int, Double> {
        var bestK = 0
        var bestD = Double.POSITIVE_INFINITY
        for (k in points.indices step NEAREST_STRIDE) {
            val d = haversineKm(lat, lon, points[k].lat, points[k].lon)
            if (d < bestD) { bestK = k; bestD = d }
        }
        val lo = max(0, bestK - NEAREST_STRIDE)
        val hi = min(points.size - 1, bestK + NEAREST_STRIDE)
        for (k in lo..hi) {
            val d = haversineKm(lat, lon, points[k].lat, points[k].lon)
            if (d < bestD) { bestK = k; bestD = d }
        }
        return bestK to bestD
    }

    private class Candidate(
        val idx: Int,
        val offsetKm: Double,
        val charger: Charger,
        val price: Double,
        val table: DoubleArray,
        val levelCost: DoubleArray,
        val fixedCost: Double,
        val choicePrice: Double,          // price as the search sees it (preferences)
        val preferred: Boolean,
    )

    private class State(val cost: Double, val parent: Int, val parentLevel: Int)

    private class Reach(val node: Int, val dip: Double, val used: Double)

    /**
     * Plan a trip along [points]. [driveS] is the routing provider's driving
     * time for the whole route; [chargers] are the chargers near it (any
     * order; slow or distant ones are dropped here).
     */
    fun planTrip(
        points: List<RoutePoint>,
        driveS: Double,
        startPct: Double,
        car: CarModel,
        settings: PlanSettings,
        chargers: List<Charger>,
        weather: List<Conditions> = emptyList(),
        rateError: RateError? = null,
    ): Plan {
        require(points.size >= 2) { "a route needs at least two points" }
        val n = points.size
        val cum = cumulativeKm(points)
        val cap = car.usableKwh
        val reserve = cap * settings.reservePct / 100.0
        val tv = settings.timeValue()

        // The forecast by km. The phone plans with a flat figure, which has no
        // temperature to adjust, so only the wind counts (and the air density).
        val forecast = weather.sortedBy { it.km }
        val aeroKmh = if (driveS > 0) cum[n - 1] / (driveS / 3600.0) else null
        var windTotal = 0.0
        val seg = DoubleArray(n)
        val net = DoubleArray(n)
        for (i in 1 until n) {
            val a = points[i - 1]
            val b = points[i]
            val dh = if (a.ele != null && b.ele != null) b.ele - a.ele else 0.0
            val dist = cum[i] - cum[i - 1]
            seg[i] = segmentKwh(dist, dh, car)
            if (forecast.isNotEmpty()) {
                val here = forecast[max(0, forecast.indexOfLast { it.km <= cum[i - 1] })]
                val w = windKwh(dist, aeroKmh, headwindKmh(here, bearingDeg(a, b)), here.tempC, car)
                seg[i] += w
                windTotal += w
            }
            net[i] = net[i - 1] + seg[i]
        }
        val wind = if (forecast.isNotEmpty()) round(windTotal, 2) else null

        val levels = leaveLevels(settings)
        val levelKwh = levels.map { cap * it / 100.0 }
        val minAdd = cap * settings.minChargePct / 100.0
        val tables = HashMap<Double, DoubleArray>()
        val found = mutableListOf<Candidate>()
        for (c in chargers) {
            if (c.powerKw < settings.minChargerKw || !usable(c, settings)) continue
            val (idx, off) = nearestPoint(points, c.lat, c.lon)
            val pinned = c.id.isNotEmpty() && c.id in settings.useChargers
            if (off > (if (pinned) PIN_MAX_OFFSET_KM else settings.searchRadiusKm) ||
                idx == n - 1) continue
            val table = tables.getOrPut(c.powerKw) { chargeTable(car, c.powerKw) }
            val price = c.pricePerKwh ?: settings.defaultPricePerKwh
            // Preferences change what the search weighs, never a shown cost:
            // simulate() prices every stop at `price`.
            val preferred = networkIn(c.operator, settings.preferNetworks)
            val choice = if (preferred) price * (1.0 - settings.preferDiscount) else price
            val detourS = 2 * off * DETOUR_ROAD_FACTOR / DETOUR_SPEED_KMH * 3600.0
            var fixed = c.fee + tv * (settings.stopOverheadMin * 60.0 + detourS) / 3600.0
            if (pinned) fixed -= PIN_BONUS
            found.add(Candidate(
                idx, off, c, price, table,
                DoubleArray(levels.size) { li ->
                    choice * levelKwh[li] / settings.chargingEff +
                        tv * tableSeconds(table, levels[li]) / 3600.0
                },
                fixed, choice, preferred,
            ))
        }
        val cands = found.sortedBy { it.idx }       // stable, like Python's sort

        fun nodeIdx(j: Int) = if (j == 0) 0 else cands[j - 1].idx

        val big = Double.POSITIVE_INFINITY
        val best = Array(cands.size + 1) { Array(levels.size) { State(big, -1, -1) } }
        val startE = cap * startPct / 100.0
        var finalCost = big
        var finalArrive = 0.0
        var finalNode = -1
        var finalLevel = -1

        // (level, departure kWh, cost so far) for each reachable state of node j.
        fun departures(j: Int): List<Triple<Int, Double, Double>> =
            if (j == 0) listOf(Triple(-1, startE, 0.0))
            else levels.indices.filter { best[j][it].cost < big }
                .map { Triple(it, levelKwh[it], best[j][it].cost) }

        for (j in 0..cands.size) {
            val deps = departures(j)
            if (deps.isEmpty()) continue
            val top = deps.maxOf { it.second }
            val (reach, dest) = reach(j, ::nodeIdx, cands, net, top - reserve, n)
            for ((li, dep, cost) in deps) {
                if (dest != null && dest.dip <= dep - reserve + 1e-9) {
                    val arrive = min(cap, dep - dest.used)
                    if (finalNode < 0 || cost < finalCost - 1e-9 ||
                        (cost <= finalCost + 1e-9 && arrive > finalArrive + 1e-9)
                    ) {
                        finalCost = cost; finalArrive = arrive; finalNode = j; finalLevel = li
                    }
                }
                for (r in reach) {
                    if (r.dip > dep - reserve + 1e-9) break
                    val cand = cands[r.node - 1]
                    val arrive = min(cap, dep - r.used)
                    val base = cost + cand.fixedCost -
                        cand.choicePrice * arrive / settings.chargingEff -
                        tv * tableSeconds(cand.table, arrive / cap * 100.0) / 3600.0
                    val row = best[r.node]
                    for (l2 in levels.indices) {
                        if (levelKwh[l2] < arrive + minAdd - 1e-9) continue
                        val c2 = base + cand.levelCost[l2]
                        if (c2 < row[l2].cost - 1e-9) row[l2] = State(c2, j, li)
                    }
                }
            }
        }

        if (finalNode >= 0) {
            return addBands(simulate(points, cum, seg, cands, path(best, finalNode, finalLevel),
                levels, driveS, startPct, car, settings, null).copy(windKwh = wind),
                car, settings, rateError)
        }
        // Unreachable: follow the plan that gets furthest, and say where it fails.
        var farKm = -1.0
        var farCost = big
        var farNode = 0
        var farLevel = -1
        for (j in 0..cands.size) {
            val deps = departures(j)
            if (deps.isEmpty()) continue
            val (li, dep, cost) = deps.maxWith(
                compareBy<Triple<Int, Double, Double>>({ it.second }, { -it.third }))
            val km = cum[failIndex(nodeIdx(j), net, dep - reserve, n)]
            if (km > farKm + 1e-9 || (km >= farKm - 1e-9 && cost < farCost - 1e-9)) {
                farKm = km; farCost = cost; farNode = j; farLevel = li
            }
        }
        return addBands(simulate(points, cum, seg, cands, path(best, farNode, farLevel), levels,
            driveS, startPct, car, settings, reserve).copy(windKwh = wind), car, settings, rateError)
    }

    /** Later candidates reachable from node j while the dip stays in [budget]; then the destination. */
    private fun reach(
        j: Int, nodeIdx: (Int) -> Int, cands: List<Candidate>, net: DoubleArray,
        budget: Double, n: Int,
    ): Pair<List<Reach>, Reach?> {
        val a = nodeIdx(j)
        val out = mutableListOf<Reach>()
        var dip = 0.0
        var k = a
        var nxt = j + 1
        while (nxt <= cands.size) {
            val target = cands[nxt - 1].idx
            while (k < target) {
                k += 1
                dip = max(dip, net[k] - net[a])
            }
            if (dip > budget + 1e-9) break
            out.add(Reach(nxt, dip, net[target] - net[a]))
            nxt += 1
        }
        var dest: Reach? = null
        if (nxt > cands.size) {
            while (k < n - 1) {
                k += 1
                dip = max(dip, net[k] - net[a])
            }
            if (dip <= budget + 1e-9) dest = Reach(n - 1, dip, net[n - 1] - net[a])
        }
        return out to dest
    }

    private fun failIndex(a: Int, net: DoubleArray, budget: Double, n: Int): Int {
        for (k in a + 1 until n) if (net[k] - net[a] > budget + 1e-9) return k
        return n - 1
    }

    private fun path(best: Array<Array<State>>, jIn: Int, liIn: Int): List<Pair<Int, Int>> {
        val out = mutableListOf<Pair<Int, Int>>()
        var j = jIn
        var li = liIn
        while (j > 0) {
            out.add(j to li)
            val s = best[j][li]
            j = s.parent
            li = s.parentLevel
        }
        return out.reversed()
    }

    /** Network names from a comma-separated string: trimmed, no repeats, at
     *  most 20 of at most 60 characters (web/routes/route.py `_names`). */
    fun names(raw: String): List<String> {
        val out = mutableListOf<String>()
        for (item in raw.split(",")) {
            val name = item.trim().take(60)
            if (name.isNotEmpty() && out.none { it.equals(name, ignoreCase = true) }) out.add(name)
        }
        return out.take(20)
    }

    /** Whether [operator] belongs to one of the networks [names]: any of them
     *  found in it, ignoring case ("ionity" matches "IONITY GmbH"). */
    fun networkIn(operator: String, names: List<String>): Boolean {
        val op = operator.lowercase()
        return names.any { it.isNotBlank() && it.trim().lowercase() in op }
    }

    /** Whether the driver's choices allow charger [c] at all. */
    fun usable(c: Charger, settings: PlanSettings): Boolean {
        if (c.id.isNotEmpty() && c.id in settings.avoidChargers) return false
        if (networkIn(c.operator, settings.avoidNetworks)) return false
        return !(settings.publicOnly && c.usageType in MEMBERS_ONLY_USAGE)
    }

    /** Up to ALT_MAX other usable chargers within ALT_WINDOW_KM of a stop,
     *  nearest along the route first -- the same order as the Python planner. */
    private fun alternatives(cand: Candidate, cands: List<Candidate>, cum: DoubleArray):
        List<Alternative> {
        val km = cum[cand.idx]
        return cands.filter { d ->
            d !== cand && abs(cum[d.idx] - km) <= ALT_WINDOW_KM &&
                !(d.charger.id.isNotEmpty() && d.charger.id == cand.charger.id)
        }.sortedWith(compareBy<Candidate> { round(abs(cum[it.idx] - km), 3) }
            .thenBy { -it.charger.powerKw }
            .thenBy { it.charger.name })
            .take(ALT_MAX)
            .map { d ->
                Alternative(d.charger, round(cum[d.idx], 1), round(d.offsetKm, 1),
                    round(d.price, 4), d.preferred)
            }
    }

    private fun simulate(
        points: List<RoutePoint>, cum: DoubleArray, seg: DoubleArray, cands: List<Candidate>,
        path: List<Pair<Int, Int>>, levels: List<Double>, driveS: Double, startPct: Double,
        car: CarModel, settings: PlanSettings, failReserve: Double?,
    ): Plan {
        val n = points.size
        val cap = car.usableKwh
        fun pct(kwh: Double) = kwh / cap * 100.0

        var energy = cap * startPct / 100.0
        val profile = mutableListOf(0 to energy)
        val stops = mutableListOf<Stop>()
        var chargeTotal = 0.0
        var moneyTotal = 0.0
        // Each leg's energy is what the battery lost on it (regeneration counted).
        var legKwhFrom = energy
        var legKmFrom = 0.0
        var k = 0
        for ((j, li) in path) {
            val cand = cands[j - 1]
            while (k < cand.idx) {
                k += 1
                energy = min(cap, energy - seg[k])
                profile.add(k to energy)
            }
            val arrive = energy
            val depart = max(arrive, cap * levels[li] / 100.0)
            val secs = chargeSeconds(pct(arrive), pct(depart), car, cand.charger.powerKw)
            val added = depart - arrive
            val money = cand.charger.fee + cand.price * added / settings.chargingEff
            chargeTotal += secs
            moneyTotal += money
            stops.add(Stop(
                km = round(cum[cand.idx], 1), charger = cand.charger,
                arrivePct = round(pct(arrive), 1), departPct = round(pct(depart), 1),
                chargeMin = round(secs / 60.0).toLong(), kwh = round(added, 1),
                pricePerKwh = round(cand.price, 4),
                priceSource = cand.charger.priceSource.ifEmpty { "default price" },
                cost = round(money, 2), detourKm = round(cand.offsetKm, 1),
                preferred = cand.preferred,
                alternatives = alternatives(cand, cands, cum),
                legKm = round(cum[cand.idx] - legKmFrom, 1),
                legKwh = round(legKwhFrom - arrive, 2),
            ))
            legKwhFrom = depart
            legKmFrom = cum[cand.idx]
            profile.add(cand.idx to depart)                  // vertical step
            energy = depart
        }
        var unreachableAt: Double? = null
        while (k < n - 1) {
            val e = min(cap, energy - seg[k + 1])
            if (failReserve != null && e < failReserve) {
                unreachableAt = cum[k + 1]
                break
            }
            k += 1
            energy = e
            profile.add(k to energy)
        }
        if (failReserve != null && unreachableAt == null) unreachableAt = cum[n - 1]
        val status = if (failReserve != null) "unreachable"
        else if (stops.isNotEmpty()) "with_stops" else "direct"
        return Plan(
            status = status, distanceKm = cum.last(), driveS = driveS, startPct = startPct,
            arrivalPct = if (failReserve != null) null else pct(energy), stops = stops,
            profile = thin(profile.map { (k2, e) -> round(cum[k2], 2) to round(e / cap * 100.0, 1) }),
            unreachableAtKm = unreachableAt, chargeS = chargeTotal, cost = moneyTotal,
            mode = settings.mode, assumptions = assumptions(car, settings),
            lastLegKm = if (failReserve != null) null else round(cum[n - 1] - legKmFrom, 1),
            lastLegKwh = if (failReserve != null) null else round(legKwhFrom - energy, 2),
        )
    }

    /** Initial compass bearing from [a] to [b]. */
    fun bearingDeg(a: RoutePoint, b: RoutePoint): Double {
        val p1 = Math.toRadians(a.lat)
        val p2 = Math.toRadians(b.lat)
        val dl = Math.toRadians(b.lon - a.lon)
        val y = sin(dl) * cos(p2)
        val x = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl)
        return (Math.toDegrees(atan2(y, x)) % 360.0 + 360.0) % 360.0
    }

    /** The forecast wind against the direction of travel (negative: from behind), at 10 m. */
    fun headwindKmh(c: Conditions, bearing: Double): Double {
        val w = c.windKmh ?: return 0.0
        val from = c.windFromDeg ?: return 0.0
        return w * cos(Math.toRadians(from - bearing))
    }

    /**
     * The energy a wind adds over a stretch (negative: saves) against calm air.
     * Drag goes with the square of the speed through the air, so a headwind
     * costs more than the same tailwind saves; cold air is denser.
     */
    fun windKwh(distKm: Double, speedKmh: Double?, headwind: Double, tempC: Double?,
                car: CarModel): Double {
        if (headwind == 0.0 || speedKmh == null || speedKmh == 0.0) return 0.0
        val v = speedKmh / 3.6
        val va = v + headwind * WIND_AT_ROAD / 3.6
        val rho = if (tempC == null) AIR_DENSITY else 101325.0 / (287.05 * (tempC + 273.15))
        val force = 0.5 * rho * car.dragAreaM2 * (abs(va) * va - v * v)
        return force * distKm * 1000.0 / 3.6e6 / car.drivetrainEff
    }

    /**
     * Each arrival's range, if the car uses [err] low to high kWh/km more than
     * planned over the leg that leads there (routing/planner.py _add_bands).
     */
    private fun addBands(plan: Plan, car: CarModel, s: PlanSettings, rateError: RateError?): Plan {
        val l = java.util.Locale.ROOT
        val rate = car.consumptionKwhPerKm
        val err = rateError ?: RateError(-ASSUMED_RATE_ERROR * rate, ASSUMED_RATE_ERROR * rate,
            "assumed: consumption within %.0f %% of the plan either way (not measured on this car)"
                .format(l, ASSUMED_RATE_ERROR * 100))
        val cap = car.usableKwh
        fun band(pct: Double, legKm: Double) =
            round(max(0.0, pct - legKm * err.high / cap * 100.0), 1) to
                round(min(100.0, pct - legKm * err.low / cap * 100.0), 1)
        var prev = 0.0
        val stops = plan.stops.map { st ->
            val (lo, hi) = band(st.arrivePct, st.km - prev)
            prev = st.km
            st.copy(arriveLowPct = lo, arriveHighPct = hi, arriveLowBelowReserve = lo < s.reservePct)
        }
        val arrival = plan.arrivalPct?.let { pct ->
            val (lo, hi) = band(pct, plan.distanceKm - prev)
            ArrivalBand(lo, hi, err.basis, lo < s.reservePct)
        }
        val notes = mutableListOf("Arrival ranges: %s; that is %+.1f to %+.1f kWh/100 km against the plan over each leg."
            .format(l, err.basis, err.low * 100, err.high * 100))
        plan.windKwh?.let { w ->
            notes += ("Weather: the forecast for when you pass each point; the wind " +
                (if (w > 0.05) "costs about %.1f kWh over the trip".format(l, w)
                 else if (w < -0.05) "saves about %.1f kWh over the trip".format(l, -w)
                 else "makes no real difference") +
                " (a headwind costs more than the same tailwind saves). Temperature is not " +
                "counted: the flat consumption figure has none to adjust.")
        }
        return plan.copy(stops = stops, arrivalBand = arrival, assumptions = plan.assumptions + notes)
    }

    /** Keep the chart payload bounded; never drop the charging steps. */
    fun thin(profile: List<Pair<Double, Double>>, limit: Int = 400): List<Pair<Double, Double>> {
        if (profile.size <= limit) return profile
        val keep = sortedSetOf(0, profile.size - 1)
        for (k in 1 until profile.size) {
            if (profile[k].first == profile[k - 1].first) {
                keep.add(k - 1)
                keep.add(k)
            }
        }
        val stride = max(1, profile.size / limit)
        for (k in 0 until profile.size step stride) keep.add(k)
        return keep.map { profile[it] }
    }

    fun assumptions(car: CarModel, s: PlanSettings): List<String> {
        val l = java.util.Locale.ROOT
        val mode = when (s.mode) {
            "fastest" -> "Fastest: stops chosen for the least time charging and stopping; " +
                "price only breaks ties."
            "cheapest" -> "Cheapest: stops chosen for the lowest charging bill; time counts " +
                "at only £%.0f/hour, so a stop is not added to save pennies.".format(l, CHEAPEST_TIME_VALUE)
            else -> "Balanced: stops chosen for the lowest bill plus your time at £%.0f/hour."
                .format(l, s.timeValuePerHour)
        }
        return listOf(
            "Usable battery %.1f kWh.".format(l, car.usableKwh),
            ("Consumption %.1f kWh/100 km on the flat; speed, temperature, wind and " +
                "heating are inside this one figure.").format(l, car.consumptionKwhPerKm * 100),
            "Hills: %.0f kg, %.0f %% of descent energy recovered.".format(
                l, car.massKg, car.regenEff * 100),
            ("Arrive with at least %.0f %%; charge to at most %.0f %% at chargers of at " +
                "least %.0f kW within %.0f km of the route; the detour to a charger counts " +
                "as time, not energy.").format(l, s.reservePct, s.chargeToPct,
                s.minChargerKw, s.searchRadiusKm),
            mode + " Each stop also counts %.0f min for pulling in and plugging in, and adds at least %.0f %%."
                .format(l, s.stopOverheadMin, s.minChargePct),
            ("Prices: your own network prices first, then the price listed on Open Charge " +
                "Map (community-entered; can be out of date), else £%.2f/kWh. Billed energy " +
                "is the energy added divided by %.2f (charging losses).")
                .format(l, s.defaultPricePerKwh, s.chargingEff),
            "Charging time follows the ID.3 58 kWh DC curve, capped by the charger's " +
                "rated power; real sessions are often slower.",
        ) + choiceAssumptions(s)
    }

    private fun choiceAssumptions(s: PlanSettings): List<String> {
        val l = java.util.Locale.ROOT
        val out = mutableListOf<String>()
        if (s.avoidNetworks.isNotEmpty()) {
            out += "Never uses: ${s.avoidNetworks.joinToString(", ")}."
        }
        if (s.preferNetworks.isNotEmpty()) {
            out += ("Prefers ${s.preferNetworks.joinToString(", ")}: their energy counts " +
                "%.0f %% cheaper when choosing stops; the costs shown are the real prices.")
                .format(l, s.preferDiscount * 100)
        }
        if (s.publicOnly) {
            out += "Public access only: chargers that need a membership or notice are left out."
        }
        if (s.avoidChargers.isNotEmpty() || s.useChargers.isNotEmpty()) {
            out += "Your picks: ${s.useChargers.size} charger(s) used where reachable, " +
                "${s.avoidChargers.size} avoided."
        }
        return out
    }
}
