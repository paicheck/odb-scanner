package com.odbscanner.route

import java.util.Locale
import kotlin.math.PI
import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.sin

/**
 * Small judgements the navigation screen shows at a glance. Pure, so they
 * are unit-tested.
 */
object NavStatus {
    const val FIX_STALE_MS = 10_000L      // no fix for this long: the position is a guess
    const val WEAK_ACCURACY_M = 50f       // worse than this: the position is rough

    /** Null when the position is good; else what to tell the driver. */
    fun gps(fixAtMs: Long, accuracyM: Float?, nowMs: Long): String? = when {
        fixAtMs <= 0L -> "Waiting for GPS"
        nowMs - fixAtMs > FIX_STALE_MS -> String.format(Locale.ROOT,
            "GPS signal lost: the position is from %d s ago", (nowMs - fixAtMs) / 1000)
        accuracyM != null && accuracyM > WEAK_ACCURACY_M -> String.format(Locale.ROOT,
            "GPS weak (±%.0f m)", accuracyM)
        else -> null
    }

    /**
     * Whether it is dark where the car is: the sun more than 2° below the
     * horizon (civil twilight starts at 0°, street lights at about −2°).
     * A standard low-precision solar position, good to well under a degree.
     */
    fun isNight(lat: Double, lon: Double, epochMs: Long): Boolean =
        sunElevationDeg(lat, lon, epochMs) < -2.0

    fun sunElevationDeg(lat: Double, lon: Double, epochMs: Long): Double {
        val d = epochMs / 86_400_000.0 - 10_957.5            // days since J2000.0
        val g = Math.toRadians((357.529 + 0.98560028 * d) % 360.0)      // mean anomaly
        val q = (280.459 + 0.98564736 * d) % 360.0                      // mean longitude
        val l = Math.toRadians(q + 1.915 * sin(g) + 0.020 * sin(2 * g)) // ecliptic longitude
        val e = Math.toRadians(23.439 - 0.00000036 * d)                 // obliquity
        val dec = asin(sin(e) * sin(l))
        val ra = Math.atan2(cos(e) * sin(l), cos(l))
        val gmst = (18.697374558 + 24.06570982441908 * d) % 24.0        // hours
        val hourAngle = Math.toRadians(gmst * 15.0 + lon) - ra
        val phi = Math.toRadians(lat)
        val elev = asin(sin(phi) * sin(dec) + cos(phi) * cos(dec) * cos(hourAngle))
        return elev * 180.0 / PI
    }

    /** (km driven, km to go) on a trip of [tripKm] with [toGoKm] left. */
    fun progress(tripKm: Double, toGoKm: Double): Pair<Double, Double> =
        (tripKm - toGoKm).coerceAtLeast(0.0) to toGoKm.coerceAtLeast(0.0)
}

/**
 * Which GPS fixes may move the guidance. A phone in a car gets fixes that
 * are late (cached, batched, replayed after a pause), vague (urban canyons,
 * tunnels' edges) or plainly wrong (a multipath jump to the next road). One
 * such fix used as if it were good can skip spoken cues, declare a re-route
 * or even an arrival. So each is judged by when it was *measured* (not when
 * it arrived), how accurate it says it is, and whether getting there from
 * the last good fix was possible:
 *
 *  * STALE   measured more than MAX_AGE_MS before it arrived: ignored, and
 *            the "GPS lost" note keeps counting from the last good one;
 *  * VAGUE   accuracy worse than VAGUE_M: shown, but the guidance waits;
 *  * ROUGH   worse than ROUGH_M: moves the guidance, but cannot count
 *            towards "off the route" (it may be the road, roughly);
 *  * JUMP    faster than MAX_KMH from the last good fix: held, until the
 *            next fix agrees with it (then the car really is there);
 *  * GOOD    the rest.
 *
 * Pure (times are passed in), so every case is a unit test.
 */
class GpsGate(
    private val maxAgeMs: Long = MAX_AGE_MS,
    private val vagueM: Float = VAGUE_M,
    private val roughM: Float = ROUGH_M,
    private val maxKmh: Double = MAX_KMH,
) {
    enum class Verdict { GOOD, ROUGH, VAGUE, JUMP, STALE }

    private data class Fix(val lat: Double, val lon: Double, val atMs: Long)

    private var last: Fix? = null
    private var held: Fix? = null

    /** Judge a fix measured at [measuredAtMs], arriving at [nowMs]. */
    fun judge(lat: Double, lon: Double, measuredAtMs: Long, accuracyM: Float?,
              nowMs: Long): Verdict {
        if (nowMs - measuredAtMs > maxAgeMs) return Verdict.STALE
        if (accuracyM != null && accuracyM > vagueM) return Verdict.VAGUE
        val fix = Fix(lat, lon, measuredAtMs)
        val prev = last
        if (prev != null && measuredAtMs <= prev.atMs) return Verdict.STALE   // out of order
        if (prev != null && !possible(prev, fix)) {
            val h = held
            held = fix
            // A second fix that agrees with the held one: the car is there.
            if (h == null || !possible(h, fix)) return Verdict.JUMP
        }
        held = null
        last = fix
        return if (accuracyM != null && accuracyM > roughM) Verdict.ROUGH else Verdict.GOOD
    }

    private fun possible(a: Fix, b: Fix): Boolean {
        val hours = max(1.0, (b.atMs - a.atMs).toDouble()) / 3_600_000.0
        return Planner.haversineKm(a.lat, a.lon, b.lat, b.lon) / hours <= maxKmh
    }

    companion object {
        const val MAX_AGE_MS = 10_000L
        const val VAGUE_M = 100f
        const val ROUGH_M = 60f             // Guidance.OFF_ROUTE_KM is 60 m
        const val MAX_KMH = 250.0
    }
}
