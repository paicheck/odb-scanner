package com.odbscanner.route

import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Turn-by-turn guidance: the route as manoeuvres with their lanes and spoken
 * cues, read from Mapbox (live traffic, lanes, ready-made announcements) or
 * from OpenRouteService (no traffic or lanes; the cues are made here), and a
 * guidance engine that follows the phone's position along it. Pure, so it is
 * unit-tested; Navigator runs it with live positions and speaks the cues.
 */

/** A lane at a junction: the directions it allows, and whether to be in it. */
data class Lane(val directions: List<String>, val active: Boolean)

/** One thing to say: where on the route (km from its start) and what. */
data class VoiceCue(val atKm: Double, val text: String)

/** One manoeuvre: where it is, what to show and say, and which lanes to use. */
data class Maneuver(
    val atKm: Double,
    val type: String,            // depart, turn, off ramp, fork, roundabout, arrive, ...
    val modifier: String,        // left, slight right, straight, ...
    val instruction: String,     // "Take exit 8 on the left towards the M40."
    val road: String,            // road after the manoeuvre
    val exit: String,            // motorway junction or roundabout exit number
    val lanes: List<Lane>,
    val cues: List<VoiceCue>,
    val atS: Double = -1.0,      // driving seconds from the route's start; < 0: unknown
)

/** A stretch of heavy or severe traffic along the route. */
data class CongestionRun(val fromKm: Double, val toKm: Double, val level: String)

/** A stretch of road with one posted limit, in the unit it is posted in. */
data class SpeedLimit(val fromKm: Double, val toKm: Double, val value: Int, val unit: String) {
    val kmh: Double get() = if (unit == "mph") value * 1.609344 else value.toDouble()
}

class NavRoute(
    val points: List<RoutePoint>,
    val maneuvers: List<Maneuver>,
    val durationS: Double,                 // with today's traffic, where known
    val typicalDurationS: Double?,         // the same trip in typical traffic (Mapbox)
    val congestion: List<CongestionRun>,
    val legEndsKm: List<Double>,           // charging stops, then the destination
    val summary: String,                   // main roads, e.g. "A40, M40"
    val source: String,
    val speedLimits: List<SpeedLimit> = emptyList(),   // Mapbox only; gaps are unknown
) {
    val cumKm: DoubleArray = Planner.cumulativeKm(points)
    val distanceKm: Double get() = cumKm.last()
    /** How much longer today's traffic makes it, in seconds (null: unknown). */
    val trafficDelayS: Double? get() = typicalDurationS?.let { max(0.0, durationS - it) }
    /** Kilometres of heavy or severe traffic on the route. */
    val heavyKm: Double get() = congestion.sumOf { it.toKm - it.fromKm }

    // (km, seconds) breakpoints: the start, each timed manoeuvre, the end.
    private val timeline: List<Pair<Double, Double>> = buildList {
        add(0.0 to 0.0)
        maneuvers.filter { it.atS >= 0 }.forEach { add(it.atKm to it.atS) }
        add(distanceKm to durationS)
    }.sortedBy { it.first }

    /** Driving seconds from the start to [km], by each stretch's own speed. */
    fun timeAt(km: Double): Double {
        val k = km.coerceIn(0.0, distanceKm)
        for (i in 1 until timeline.size) {
            val (k1, s1) = timeline[i]
            if (k <= k1) {
                val (k0, s0) = timeline[i - 1]
                return if (k1 > k0) s0 + (s1 - s0) * (k - k0) / (k1 - k0) else s1
            }
        }
        return durationS
    }

    /** The posted limit at [km], or null where it is not known. */
    fun speedLimitAt(km: Double): SpeedLimit? {
        var lo = 0
        var hi = speedLimits.size - 1
        while (lo <= hi) {
            val mid = (lo + hi) / 2
            val l = speedLimits[mid]
            when {
                km < l.fromKm -> hi = mid - 1
                km >= l.toKm -> lo = mid + 1
                else -> return l
            }
        }
        return null
    }

    /** The heavy-traffic stretch at or ahead of [km], within [withinKm]; null if none. */
    fun trafficAhead(km: Double, withinKm: Double): CongestionRun? =
        congestion.firstOrNull { it.toKm > km && it.fromKm - km <= withinKm }

    /** The (lat, lon) [km] along the line. */
    fun pointAt(km: Double): Pair<Double, Double> {
        val k = km.coerceIn(0.0, distanceKm)
        var i = java.util.Arrays.binarySearch(cumKm, k)
        if (i < 0) i = -i - 2                              // the segment it falls in
        i = i.coerceIn(0, points.size - 2)
        val seg = cumKm[i + 1] - cumKm[i]
        val t = if (seg > 0) (k - cumKm[i]) / seg else 0.0
        val a = points[i]
        val b = points[i + 1]
        return (a.lat + t * (b.lat - a.lat)) to (a.lon + t * (b.lon - a.lon))
    }

    /** Compass bearing of the road at [km], looking a little ahead: for a heading-up map. */
    fun bearingAt(km: Double): Double {
        val (lat1, lon1) = pointAt(km - 0.01)
        val (lat2, lon2) = pointAt(km + 0.04)
        val p1 = Math.toRadians(lat1)
        val p2 = Math.toRadians(lat2)
        val dl = Math.toRadians(lon2 - lon1)
        val y = kotlin.math.sin(dl) * cos(p2)
        val x = cos(p1) * kotlin.math.sin(p2) - kotlin.math.sin(p1) * cos(p2) * cos(dl)
        return (Math.toDegrees(kotlin.math.atan2(y, x)) + 360.0) % 360.0
    }

    /** The manoeuvres as plain steps: the assistant's "junctions ahead". */
    fun steps(): List<RouteStep> = maneuvers.mapIndexed { i, m ->
        val end = maneuvers.getOrNull(i + 1)?.atKm ?: distanceKm
        RouteStep(m.atKm, max(0.0, end - m.atKm), m.instruction, m.road, m.exit.toIntOrNull())
    }

    /**
     * Whether [other] keeps to this route's roads from [fromKm] on: every half
     * kilometre of it within [toleranceKm] of this line. A traffic check that
     * comes back on the same roads only freshens the times.
     */
    fun follows(other: NavRoute, fromKm: Double, toleranceKm: Double = 0.1): Boolean {
        val start = max(0, cumKm.indexOfFirst { it >= fromKm - 0.5 } - 1)
        var next = 0.0
        for (i in other.points.indices) {
            if (other.cumKm[i] < next && i != other.points.lastIndex) continue
            next = other.cumKm[i] + 0.5
            val p = other.points[i]
            if (nearestOnLine(points, p.lat, p.lon, start, points.size).second > toleranceKm) return false
        }
        return true
    }
}

/**
 * The nearest point to (lat, lon) on the line between points [from] and [to]:
 * (segment index, km away, fraction along the segment). Flat-earth maths --
 * good to a metre or two over one segment.
 */
fun nearestOnLine(pts: List<RoutePoint>, lat: Double, lon: Double, from: Int, to: Int):
    Triple<Int, Double, Double> {
    var best = Triple(max(0, min(from, pts.size - 2)), Double.MAX_VALUE, 0.0)
    val kx = 111.32 * cos(Math.toRadians(lat))
    val ky = 110.574
    for (i in max(0, from) until min(pts.size - 1, to)) {
        val ax = (pts[i].lon - lon) * kx; val ay = (pts[i].lat - lat) * ky
        val bx = (pts[i + 1].lon - lon) * kx; val by = (pts[i + 1].lat - lat) * ky
        val dx = bx - ax; val dy = by - ay
        val len2 = dx * dx + dy * dy
        val t = if (len2 > 0) ((-ax * dx - ay * dy) / len2).coerceIn(0.0, 1.0) else 0.0
        val px = ax + t * dx; val py = ay + t * dy
        val d = sqrt(px * px + py * py)
        if (d < best.second) best = Triple(i, d, t)
    }
    return best
}

/** How things are said: British distances, lane advice. */
object NavSpeech {
    private const val MILES_PER_KM = 0.621371
    private const val YARDS_PER_KM = 1093.61
    private val WORDS = listOf("", "one", "two", "three", "four", "five", "six")

    fun distance(km: Double): String {
        val miles = km * MILES_PER_KM
        return when {
            miles >= 9.75 -> "${Math.round(miles)} miles"
            miles >= 1.25 -> {                                    // to the half mile
                val halves = Math.round(miles * 2)
                if (halves % 2 == 0L) "${halves / 2} miles" else "${halves / 2} and a half miles"
            }
            miles >= 0.9 -> "1 mile"
            miles >= 0.65 -> "three quarters of a mile"
            miles >= 0.4 -> "half a mile"
            miles >= 0.2 -> "a quarter of a mile"
            else -> "${max(50L, Math.round(km * YARDS_PER_KM / 50.0) * 50)} yards"
        }
    }

    /** "Heavy traffic in 2 miles, for about 1 mile." */
    fun traffic(run: CongestionRun, km: Double): String {
        val what = if (run.level == "severe") "Very heavy traffic" else "Heavy traffic"
        val length = distance(run.toKm - max(km, run.fromKm))
        return if (run.fromKm - km < 0.2) "$what ahead for about $length."
        else "$what in ${distance(run.fromKm - km)}, for about $length."
    }

    /** "The speed limit is 30." -- miles an hour go unsaid on British roads. */
    fun speedLimit(limit: SpeedLimit): String =
        if (limit.unit == "mph") "The speed limit is ${limit.value}."
        else "The speed limit is ${limit.value} kilometres an hour."

    /** "In half a mile, turn left onto Station Road." */
    fun lead(km: Double, instruction: String): String =
        "In ${distance(km)}, ${instruction.replaceFirstChar { it.lowercase() }.trimEnd('.')}."

    /** "Use the left two lanes." -- null when every lane (or none) is right. */
    fun laneAdvice(lanes: List<Lane>): String? {
        val n = lanes.size
        val on = lanes.indices.filter { lanes[it].active }
        if (n < 2 || on.isEmpty() || on.size == n) return null
        val k = on.size
        val count = if (k == 1) "lane" else "${WORDS.getOrElse(k) { "$k" }} lanes"
        return when {
            on == (0 until k).toList() -> if (k == 1) "Use the left lane." else "Use the left $count."
            on == (n - k until n).toList() -> if (k == 1) "Use the right lane." else "Use the right $count."
            k == 1 -> "Use the ${ordinal(on[0] + 1)} lane from the left."
            else -> "Use the middle lanes."
        }
    }

    private fun ordinal(i: Int) = when (i) {
        1 -> "first"; 2 -> "second"; 3 -> "third"; 4 -> "fourth"; 5 -> "fifth"; else -> "${i}th"
    }
}

/** How things are shown: short distances, arrows for manoeuvres and lanes. */
object NavText {
    /** "250 yd", "0.3 mi", "12 mi". */
    fun distance(km: Double): String {
        val miles = km * 0.621371
        return when {
            miles < 0.1 -> "${max(10L, Math.round(km * 1093.61 / 10.0) * 10)} yd"
            miles < 10 -> String.format(Locale.ROOT, "%.1f mi", miles)
            else -> "${Math.round(miles)} mi"
        }
    }

    fun arrow(m: Maneuver): String = when {
        m.type == "arrive" -> "⚑"
        m.type.contains("roundabout") || m.type == "rotary" -> "↻"   // clockwise: driving on the left
        else -> direction(m.modifier)
    }

    fun direction(modifier: String): String = when (modifier) {
        "left" -> "↰"
        "right" -> "↱"
        "slight left" -> "↖"
        "slight right" -> "↗"
        "sharp left" -> "↙"
        "sharp right" -> "↘"
        "uturn" -> "↷"
        else -> "↑"
    }

    /** A lane's arrow: the way to go when it is a lane to be in, else its first. */
    fun lane(l: Lane, modifier: String): String =
        direction(if (l.active && modifier in l.directions) modifier
                  else l.directions.firstOrNull() ?: "straight")
}

/** Reading a Mapbox Directions (v5) reply: geojson geometry, steps, banners, voice. */
object MapboxParse {
    fun routes(data: JSONObject): List<NavRoute> {
        if (data.optString("code", "Ok") != "Ok") {
            throw RoutingException("Mapbox found no route: " +
                data.optString("message", data.optString("code")))
        }
        val routes = data.optJSONArray("routes") ?: return emptyList()
        return (0 until routes.length()).mapNotNull { routes.optJSONObject(it)?.let(::route) }
    }

    private fun route(r: JSONObject): NavRoute? {
        val coords = r.optJSONObject("geometry")?.optJSONArray("coordinates") ?: return null
        val pts = (0 until coords.length()).mapNotNull { i ->
            coords.optJSONArray(i)?.let { RoutePoint(it.getDouble(1), it.getDouble(0)) }
        }
        if (pts.size < 2) return null
        val cum = Planner.cumulativeKm(pts)

        val steps = mutableListOf<JSONObject>()
        val legEnds = mutableListOf<Double>()
        val congestion = mutableListOf<String>()
        val maxspeed = mutableListOf<Pair<Int, String>?>()
        val summaries = mutableListOf<String>()
        var stepKm = 0.0
        val legs = r.optJSONArray("legs") ?: JSONArray()
        for (l in 0 until legs.length()) {
            val leg = legs.optJSONObject(l) ?: continue
            leg.optJSONArray("steps")?.let { s ->
                for (i in 0 until s.length()) s.optJSONObject(i)?.let {
                    steps.add(it)
                    stepKm += it.optDouble("distance", 0.0) / 1000.0
                }
            }
            legEnds.add(stepKm)
            leg.optJSONObject("annotation")?.optJSONArray("congestion")?.let { c ->
                for (i in 0 until c.length()) congestion.add(c.optString(i, "unknown"))
            }
            // Per segment: {"speed": 30, "unit": "mph"}, {"unknown": true} or
            // {"none": true} (no limit). Only posted numbers are kept.
            leg.optJSONObject("annotation")?.optJSONArray("maxspeed")?.let { m ->
                for (i in 0 until m.length()) maxspeed.add(m.optJSONObject(i)?.let { o ->
                    if (o.has("speed") && !o.isNull("speed"))
                        o.optInt("speed") to o.optString("unit", "km/h") else null
                })
            }
            leg.optString("summary").takeIf { it.isNotBlank() }?.let(summaries::add)
        }
        // Steps measure the road; the geometry is what positions are matched
        // against. Put both on the geometry's scale.
        val scale = if (stepKm > 0) cum.last() / stepKm else 1.0
        val starts = DoubleArray(steps.size)
        val times = DoubleArray(steps.size)
        var acc = 0.0
        var t = 0.0
        for (j in steps.indices) {
            starts[j] = acc * scale
            times[j] = t
            acc += steps[j].optDouble("distance", 0.0) / 1000.0
            t += steps[j].optDouble("duration", 0.0)
        }
        val maneuvers = mutableListOf<Maneuver>()
        for (j in 1 until steps.size) {
            val s = steps[j]
            val man = s.optJSONObject("maneuver") ?: continue
            val type = man.optString("type")
            if (type == "depart") continue                       // a new leg starting
            val prev = steps[j - 1]                              // its cues and lanes
            val cues = prev.optJSONArray("voiceInstructions")?.let { v ->
                (0 until v.length()).mapNotNull { v.optJSONObject(it) }.map {
                    VoiceCue(max(0.0, starts[j] - it.optDouble("distanceAlongGeometry", 0.0) / 1000.0 * scale),
                        it.optString("announcement"))
                }.filter { it.text.isNotBlank() }
            } ?: emptyList()
            maneuvers.add(Maneuver(
                atKm = starts[j],
                type = type,
                modifier = man.optString("modifier"),
                instruction = man.optString("instruction"),
                road = text(s, "ref").ifBlank { text(s, "name") },
                exit = text(s, "exits").ifBlank { if (man.has("exit")) man.optInt("exit").toString() else "" },
                lanes = lanesOf(prev),
                cues = cues,
                atS = times[j],
            ))
        }
        return NavRoute(
            points = pts,
            maneuvers = maneuvers,
            durationS = r.optDouble("duration", 0.0),
            typicalDurationS = if (r.has("duration_typical") && !r.isNull("duration_typical"))
                r.optDouble("duration_typical") else null,
            congestion = runs(congestion, cum),
            legEndsKm = legEnds.map { it * scale },
            summary = summaries.joinToString("; "),
            source = "Mapbox (live traffic)",
            speedLimits = limits(maxspeed, cum),
        )
    }

    private fun text(o: JSONObject, key: String) = if (o.isNull(key)) "" else o.optString(key, "")

    /** Lane guidance for the coming manoeuvre: the last banner that has lanes. */
    private fun lanesOf(step: JSONObject): List<Lane> {
        val banners = step.optJSONArray("bannerInstructions") ?: return emptyList()
        for (b in banners.length() - 1 downTo 0) {
            val comps = banners.optJSONObject(b)?.optJSONObject("sub")?.optJSONArray("components")
                ?: continue
            val lanes = (0 until comps.length()).mapNotNull { comps.optJSONObject(it) }
                .filter { it.optString("type") == "lane" }
                .map { c ->
                    val d = c.optJSONArray("directions") ?: JSONArray()
                    Lane((0 until d.length()).map { d.optString(it) }, c.optBoolean("active", false))
                }
            if (lanes.isNotEmpty()) return lanes
        }
        return emptyList()
    }

    /** Where annotation segment [i] of [n] starts along the geometry: one segment
     *  per pair of points, spread proportionally if the counts differ. */
    private fun segmentKm(i: Int, n: Int, cum: DoubleArray): Double {
        val segs = cum.size - 1
        val pos = i.toDouble() * segs / n
        val k = min(segs - 1, pos.toInt())
        return cum[k] + (pos - k) * (cum[k + 1] - cum[k])
    }

    /** Stretches of one posted limit, from the per-segment maxspeed annotation. */
    fun limits(values: List<Pair<Int, String>?>, cum: DoubleArray): List<SpeedLimit> {
        if (values.isEmpty() || cum.size < 2) return emptyList()
        val out = mutableListOf<SpeedLimit>()
        var start = 0
        for (i in 1..values.size) {
            if (i < values.size && values[i] == values[start]) continue
            values[start]?.let { (v, unit) ->
                out.add(SpeedLimit(segmentKm(start, values.size, cum),
                    if (i >= values.size) cum.last() else segmentKm(i, values.size, cum), v, unit))
            }
            start = i
        }
        return out
    }

    /** Heavy or severe stretches, from per-segment congestion along the geometry. */
    fun runs(levels: List<String>, cum: DoubleArray): List<CongestionRun> {
        if (levels.isEmpty() || cum.size < 2) return emptyList()
        fun km(i: Int) = segmentKm(i, levels.size, cum)
        val out = mutableListOf<CongestionRun>()
        var start = -1
        var worst = ""
        for (i in 0..levels.size) {
            val heavy = i < levels.size && (levels[i] == "heavy" || levels[i] == "severe")
            if (heavy) {
                if (start < 0) { start = i; worst = levels[i] }
                if (levels[i] == "severe") worst = "severe"
            } else if (start >= 0) {
                out.add(CongestionRun(km(start), if (i >= levels.size) cum.last() else km(i), worst))
                start = -1
            }
        }
        return out
    }
}

/** OpenRouteService's turn-by-turn steps as manoeuvres, with cues made here. */
object OrsNav {
    // ORS instruction types: 10 arrive (goal), 11 depart.
    private const val ARRIVE = 10
    private const val DEPART = 11

    /** ORS's instruction type as Mapbox's (type, modifier), so both draw the same arrows. */
    fun kind(type: Int): Pair<String, String> = when (type) {
        0 -> "turn" to "left"
        1 -> "turn" to "right"
        2 -> "turn" to "sharp left"
        3 -> "turn" to "sharp right"
        4 -> "turn" to "slight left"
        5 -> "turn" to "slight right"
        6 -> "continue" to "straight"
        7 -> "roundabout" to ""
        8 -> "exit roundabout" to ""
        9 -> "turn" to "uturn"
        10 -> "arrive" to ""
        11 -> "depart" to ""
        12 -> "fork" to "slight left"           // keep left
        13 -> "fork" to "slight right"          // keep right
        else -> "turn" to ""
    }

    fun route(route: Route, legEndsKm: List<Double>): NavRoute {
        val steps = route.steps
        val maneuvers = steps.mapIndexedNotNull { i, st ->
            if (st.type == DEPART && i == 0) {
                return@mapIndexedNotNull Maneuver(0.0, "depart", "", st.instruction, st.road, "",
                    emptyList(), listOf(VoiceCue(0.0, st.instruction.trimEnd('.') + ".")))
            }
            if (st.type == DEPART) return@mapIndexedNotNull null     // later legs
            val prevKm = steps.getOrNull(i - 1)?.km ?: 0.0
            val cues = if (st.type == ARRIVE) {
                listOf(VoiceCue(max(prevKm, st.km - 0.3), NavSpeech.lead(0.3, st.instruction)),
                    VoiceCue(max(prevKm, st.km - 0.03), st.instruction.trimEnd('.') + "."))
            } else listOf(1.6, 0.4).filter { st.km - it > prevKm + 0.1 }
                .map { VoiceCue(st.km - it, NavSpeech.lead(it, st.instruction)) } +
                VoiceCue(max(prevKm, st.km - 0.06), st.instruction.trimEnd('.') + ".")
            val (type, modifier) = kind(st.type)
            Maneuver(st.km, type, modifier, st.instruction, st.road, st.exitNumber?.toString() ?: "",
                emptyList(), cues)
        }
        return NavRoute(route.points, maneuvers, route.durationS, null, emptyList(),
            legEndsKm, "", "OpenRouteService")
    }
}

/** Where the driver is on the route, what is next, and what to say now. */
data class GuidanceState(
    val kmAlong: Double,
    val offRouteKm: Double,
    val offRoute: Boolean,
    val next: Maneuver?,
    val toNextKm: Double,
    val then: Maneuver?,             // a second manoeuvre close after the next
    val lanes: List<Lane>,           // for the next manoeuvre, once it is near
    val say: String?,                // the cue due now, if any
    val remainingKm: Double,
    val remainingS: Double,
    val leg: Int,                    // which leg (0: to the first charging stop or the destination)
    val arrived: Boolean,
)

/**
 * Follows one route. [quietUntilKm]: cues due before this point are taken as
 * said -- for a route swapped in on the same roads, whose opening words the
 * driver has just heard from the old one.
 */
class Guidance(val route: NavRoute, quietUntilKm: Double = -1.0) {
    companion object {
        const val OFF_ROUTE_KM = 0.06          // further than this from the road...
        const val OFF_ROUTE_FIXES = 3          // ...for this many fixes in a row
        const val STALE_KM = 0.25              // a cue missed by more than this is skipped
        const val EARLY_KM = 0.02              // a cue falls due this much before its point
        const val LANES_FROM_KM = 2.0
        const val ARRIVED_KM = 0.05
    }

    private var hint = 0
    private var offCount = 0
    private val spoken = HashSet<String>()

    init {
        route.maneuvers.forEachIndexed { mi, m ->
            m.cues.forEachIndexed { ci, c -> if (c.atKm <= quietUntilKm) spoken += "$mi:$ci" }
        }
    }

    /**
     * Where the car is on the route now, and what to say. [countOffRoute]
     * false (a rough fix, GpsGate.Verdict.ROUGH): this fix may move the car
     * along but cannot count towards "off the route" -- it could be the road.
     */
    fun update(lat: Double, lon: Double, countOffRoute: Boolean = true): GuidanceState {
        val (km, off) = snap(lat, lon)
        offCount = when {
            off <= OFF_ROUTE_KM -> 0
            countOffRoute -> offCount + 1
            else -> offCount
        }
        val total = route.distanceKm
        val nextIdx = route.maneuvers.indexOfFirst { it.atKm > km + 0.015 }
        val next = route.maneuvers.getOrNull(nextIdx)
        val then = if (nextIdx >= 0) route.maneuvers.getOrNull(nextIdx + 1)
            ?.takeIf { it.atKm - route.maneuvers[nextIdx].atKm < 0.4 } else null
        var say: String? = null
        if (offCount == 0) {
            for ((mi, m) in route.maneuvers.withIndex()) {
                if (m.atKm < km - 0.05) continue
                if (m.cues.none { it.atKm <= km + EARLY_KM }) continue
                for ((ci, c) in m.cues.withIndex()) {
                    val key = "$mi:$ci"
                    if (key in spoken || c.atKm > km + EARLY_KM) continue
                    spoken += key
                    if (km - c.atKm > STALE_KM) continue        // long passed: stay quiet
                    var text = c.text
                    if (m.lanes.isNotEmpty() && m.atKm - km < 1.0 && !text.contains("lane", true)) {
                        NavSpeech.laneAdvice(m.lanes)?.let { text = "${text.trimEnd()} $it" }
                    }
                    say = text                                  // the latest due cue wins
                }
            }
        }
        val remaining = max(0.0, total - km)
        return GuidanceState(
            kmAlong = km,
            offRouteKm = off,
            offRoute = offCount >= OFF_ROUTE_FIXES,
            next = next,
            toNextKm = next?.let { max(0.0, it.atKm - km) } ?: remaining,
            then = then,
            lanes = next?.takeIf { it.atKm - km < LANES_FROM_KM }?.lanes ?: emptyList(),
            say = say,
            remainingKm = remaining,
            remainingS = max(0.0, route.durationS - route.timeAt(km)),
            leg = route.legEndsKm.count { it < km - ARRIVED_KM },
            arrived = remaining < ARRIVED_KM,
        )
    }

    /** (km along the route, km off it): nearest point on the line, searched near the last match. */
    fun snap(lat: Double, lon: Double): Pair<Double, Double> {
        val pts = route.points
        var (i, d, t) = nearestOnLine(pts, lat, lon, hint - 50, hint + 400)
        if (d > 0.2) {                                   // lost: look along the whole route
            val all = nearestOnLine(pts, lat, lon, 0, pts.size)
            if (all.second < d) { i = all.first; d = all.second; t = all.third }
        }
        hint = i
        val km = route.cumKm[i] + t * (route.cumKm[i + 1] - route.cumKm[i])
        return km to d
    }
}
