package com.odbscanner.route

import java.util.Locale
import kotlin.math.max
import kotlin.math.min

/**
 * When a trip in progress needs a new plan, decided from the car's latest
 * charge reading and the phone's position. Pure, so it is unit-tested; the
 * TripMonitor runs it every minute while a trip is active.
 *
 * Re-plan when:
 *  * the charge is DRIFT_PCT below what the plan expected at this point
 *    (using more than planned -- the next stop may now be out of reach), or
 *    ABOVE_PCT above it (using less, or charged more: a stop may go);
 *  * the phone is OFF_ROUTE_KM from the planned road (a different way);
 *  * charging has just finished (the charge rose, then stopped rising).
 * Never while the charge is still rising (charging in progress), and not
 * more often than MIN_INTERVAL_MS.
 *
 * Before the first planned stop, with no charging since the plan was made,
 * the consumption figure is corrected by how the real drop compares with the
 * planned one -- so the new plan uses what this drive is really costing.
 */
object Replan {
    const val DRIFT_PCT = 3.0
    const val ABOVE_PCT = 5.0
    const val OFF_ROUTE_KM = 4.0          // the phone's coarse position is ~2 km
    const val MIN_INTERVAL_MS = 5 * 60_000L
    const val READING_MAX_AGE_MS = 15 * 60_000L
    const val ARRIVED_KM = 1.5
    const val MIN_USED_PCT = 3.0          // less drop than this is too little to measure
    const val RISING_PCT = 0.5            // between checks: charging in progress

    data class Progress(val kmAlong: Double, val offRouteKm: Double)

    /** Where [lat]/[lon] is along the drawn route [line], in plan km ([planKm] long). */
    fun progress(line: List<Pair<Double, Double>>, planKm: Double, lat: Double, lon: Double): Progress {
        if (line.isEmpty()) return Progress(0.0, Double.POSITIVE_INFINITY)
        var along = 0.0
        var bestAlong = 0.0
        var bestD = Double.POSITIVE_INFINITY
        for (i in line.indices) {
            if (i > 0) along += Planner.haversineKm(line[i - 1].first, line[i - 1].second,
                line[i].first, line[i].second)
            val d = Planner.haversineKm(lat, lon, line[i].first, line[i].second)
            if (d < bestD) { bestD = d; bestAlong = along }
        }
        // The drawn line is thinned, so it is a little shorter than the road.
        val scale = if (along > 0) planKm / along else 1.0
        return Progress(bestAlong * scale, bestD)
    }

    /** The charge the plan expected at [km]; a stop at exactly [km] counts as left. */
    fun expectedPct(profile: List<Pair<Double, Double>>, km: Double): Double? {
        if (profile.isEmpty()) return null
        var i = -1
        for (k in profile.indices) if (profile[k].first <= km) i = k
        if (i < 0) return profile.first().second
        if (i == profile.lastIndex) return profile.last().second
        val (k0, p0) = profile[i]
        val (k1, p1) = profile[i + 1]
        if (k1 <= k0) return p0
        return p0 + (p1 - p0) * (km - k0) / (k1 - k0)
    }

    const val AT_STOP_KM = 3.0

    /**
     * The planned stop the car is at, if any. There the plan's charge jumps
     * to the leave-at level before the car has charged, so drift means
     * nothing until charging starts.
     */
    fun atStop(stopKms: List<Double>, km: Double): Int? =
        stopKms.indexOfFirst { kotlin.math.abs(it - km) <= AT_STOP_KM }.takeIf { it >= 0 }

    /** Why the trip needs a new plan, or null. */
    fun reason(expected: Double, actual: Double, offRouteKm: Double): String? = when {
        offRouteKm > OFF_ROUTE_KM -> String.format(Locale.ROOT,
            "%.0f km off the planned route", offRouteKm)
        actual < expected - DRIFT_PCT -> String.format(Locale.ROOT,
            "charge %.0f %%, the plan expected %.0f %%", actual, expected)
        actual > expected + ABOVE_PCT -> String.format(Locale.ROOT,
            "charge %.0f %%, more than the %.0f %% planned", actual, expected)
        else -> null
    }

    /**
     * Consumption (kWh/100 km) corrected by this drive so far, or null when
     * there is not enough to go on: charged since the plan, already past the
     * first planned stop, or too small a drop to measure.
     */
    fun correctedConsumption(
        base: Double, planStartPct: Double, expected: Double, actual: Double,
        chargedSincePlan: Boolean, kmAlong: Double, firstStopKm: Double?,
    ): Double? {
        if (chargedSincePlan) return null
        if (firstStopKm != null && kmAlong >= firstStopKm - 1.0) return null
        val usedPlan = planStartPct - expected
        val usedReal = planStartPct - actual
        if (usedPlan < MIN_USED_PCT || usedReal <= 0) return null
        val corrected = base * usedReal / usedPlan
        return min(max(corrected, max(8.0, base * 0.75)), min(40.0, base * 1.5))
    }
}
