package com.odbscanner.route

import java.util.Locale
import kotlin.math.PI
import kotlin.math.asin
import kotlin.math.cos
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
