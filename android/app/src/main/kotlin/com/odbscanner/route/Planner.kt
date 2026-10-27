package com.odbscanner.route

import kotlin.math.abs
import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * EV trip planner: energy along a route, and where to charge.
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
 * detour to a charger is ignored. Pure: no I/O, no Android.
 */

const val G = 9.81
const val EARTH_RADIUS_KM = 6371.0088

/** ID.3 Pro 58 kWh DC charging power by state of charge: (SoC %, max kW). */
val ID3_58_CURVE: List<Pair<Double, Double>> = listOf(
    0.0 to 110.0, 30.0 to 110.0, 40.0 to 95.0, 50.0 to 80.0, 60.0 to 66.0,
    70.0 to 55.0, 80.0 to 43.0, 90.0 to 25.0, 100.0 to 7.0,
)

data class CarModel(
    val usableKwh: Double,
    val consumptionKwhPerKm: Double,
    val massKg: Double = 1950.0,
    val drivetrainEff: Double = 0.90,
    val regenEff: Double = 0.60,
    val chargeCurve: List<Pair<Double, Double>> = ID3_58_CURVE,
)

data class PlanSettings(
    val reservePct: Double = 10.0,
    val chargeToPct: Double = 80.0,
    val minChargerKw: Double = 50.0,
    val searchStepKm: Double = 15.0,
    val searchBackKm: Double = 120.0,
    val searchRadiusKm: Double = 5.0,
    val maxStops: Int = 8,
)

data class Charger(
    val name: String,
    val lat: Double,
    val lon: Double,
    val powerKw: Double,
    val operator: String = "",
    val address: String = "",
)

/** A route point: elevation in metres, or null when the provider gave none. */
data class RoutePoint(val lat: Double, val lon: Double, val ele: Double? = null)

data class Stop(
    val km: Double,
    val charger: Charger,
    val arrivePct: Double,
    val departPct: Double,
    val chargeMin: Long,
)

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
    val assumptions: List<String>,
)

fun interface ChargerLookup {
    fun near(lat: Double, lon: Double, radiusKm: Double): List<Charger>
}

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

    /**
     * Plan a trip along [points]. [driveS] is the routing provider's driving
     * time for the whole route; [find] returns chargers near a point.
     */
    fun planTrip(
        points: List<RoutePoint>,
        driveS: Double,
        startPct: Double,
        car: CarModel,
        settings: PlanSettings,
        find: ChargerLookup,
    ): Plan {
        require(points.size >= 2) { "a route needs at least two points" }
        val cum = cumulativeKm(points)
        val totalKm = cum.last()
        val cap = car.usableKwh
        val reserveKwh = cap * settings.reservePct / 100.0

        fun seg(i: Int): Double {
            val a = points[i - 1]
            val b = points[i]
            val dh = if (a.ele != null && b.ele != null) b.ele - a.ele else 0.0
            return segmentKwh(cum[i] - cum[i - 1], dh, car)
        }

        fun pct(kwh: Double) = kwh / cap * 100.0

        // energy[k]: energy on arriving at point k (after charging, at a stop).
        val energy = DoubleArray(points.size)
        energy[0] = cap * startPct / 100.0
        var profile = mutableListOf(0 to energy[0])          // (point, kWh)
        val stops = mutableListOf<Stop>()
        var chargeTotal = 0.0
        var lastStopIdx = 0
        var i = 1
        while (i < points.size) {
            val e = min(cap, energy[i - 1] - seg(i))
            if (e >= reserveKwh) {
                energy[i] = e
                profile.add(i to e)
                i += 1
                continue
            }
            // Below the reserve at point i: find a charger behind it, searching
            // back from here in steps, never at or behind the previous stop.
            val found = if (stops.size < settings.maxStops)
                findStop(points, cum, i, cum[lastStopIdx], settings, find) else null
            if (found == null) {
                return Plan(
                    status = "unreachable", distanceKm = totalKm, driveS = driveS,
                    startPct = startPct, arrivalPct = null, stops = stops,
                    profile = thin(profileOf(profile, cum, cap)), unreachableAtKm = cum[i],
                    chargeS = chargeTotal, assumptions = assumptions(car, settings),
                )
            }
            val (idx, charger) = found
            val arrive = energy[idx]
            val depart = max(arrive, cap * settings.chargeToPct / 100.0)
            val secs = chargeSeconds(pct(arrive), pct(depart), car, charger.powerKw)
            chargeTotal += secs
            stops.add(
                Stop(
                    km = round(cum[idx], 1), charger = charger,
                    arrivePct = round(pct(arrive), 1), departPct = round(pct(depart), 1),
                    chargeMin = round(secs / 60.0).toLong(),
                ),
            )
            profile = profile.filter { it.first <= idx }.toMutableList()
            profile.add(idx to depart)                       // vertical step
            energy[idx] = depart
            lastStopIdx = idx
            i = idx + 1
        }
        return Plan(
            status = if (stops.isNotEmpty()) "with_stops" else "direct",
            distanceKm = totalKm, driveS = driveS, startPct = startPct,
            arrivalPct = pct(energy.last()), stops = stops,
            profile = profileOf(profile, cum, cap), unreachableAtKm = null,
            chargeS = chargeTotal, assumptions = assumptions(car, settings),
        )
    }

    private fun profileOf(
        points: List<Pair<Int, Double>>, cum: DoubleArray, cap: Double,
    ): List<Pair<Double, Double>> =
        thin(points.map { (k, e) -> round(cum[k], 2) to round(e / cap * 100.0, 1) })

    private fun indexAt(cum: DoubleArray, km: Double): Int {
        var best = 0
        for (k in cum.indices) if (cum[k] <= km + 1e-9) best = k
        return best
    }

    private fun findStop(
        points: List<RoutePoint>, cum: DoubleArray, failIdx: Int, lastStopKm: Double,
        settings: PlanSettings, find: ChargerLookup,
    ): Pair<Int, Charger>? {
        val target = cum[failIdx] - 1.0
        val floor = max(lastStopKm + 1.0, cum[failIdx] - settings.searchBackKm)
        var km = target
        while (km >= floor) {
            val idx = min(indexAt(cum, km), failIdx - 1)
            if (idx == 0 || cum[idx] <= lastStopKm) break
            val p = points[idx]
            val found = find.near(p.lat, p.lon, settings.searchRadiusKm)
                .filter { it.powerKw >= settings.minChargerKw }
            if (found.isNotEmpty()) {
                return idx to found.sortedWith(
                    compareBy({ -it.powerKw }, { haversineKm(p.lat, p.lon, it.lat, it.lon) }),
                ).first()
            }
            km -= settings.searchStepKm
        }
        return null
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

    fun assumptions(car: CarModel, s: PlanSettings): List<String> = listOf(
        "Usable battery %.1f kWh.".format(java.util.Locale.ROOT, car.usableKwh),
        ("Consumption %.1f kWh/100 km on the flat; speed, temperature, wind and " +
            "heating are inside this one figure.").format(
            java.util.Locale.ROOT, car.consumptionKwhPerKm * 100),
        "Hills: %.0f kg, %.0f %% of descent energy recovered.".format(
            java.util.Locale.ROOT, car.massKg, car.regenEff * 100),
        ("Arrive with at least %.0f %%; charge to %.0f %% at chargers of at least " +
            "%.0f kW within %.0f km of the route; the detour to the charger is not " +
            "counted.").format(java.util.Locale.ROOT, s.reservePct, s.chargeToPct,
            s.minChargerKw, s.searchRadiusKm),
        "Charging time follows the ID.3 58 kWh DC curve, capped by the charger's " +
            "rated power; real sessions are often slower.",
    )
}
