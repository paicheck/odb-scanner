package com.odbscanner.route

import java.util.Locale
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * En route: is the plan still good, from the charge the car reports now?
 * The dashboard's rules (analysis/enroute.py), held to it case by case by
 * tests/data/enroute_vectors.json, with Replan's thresholds:
 *
 *  * expected: the plan's charge profile at this point;
 *  * ratio: what this drive has really used against what the plan expected
 *    by here -- before the first stop, with no charging since, once at least
 *    MIN_USED_PCT has gone; clamped like Replan.correctedConsumption;
 *  * projected: the charge at the next stop (or the destination) if the rest
 *    of the way costs `ratio` times the plan, so a higher rate widens the gap
 *    with distance.
 *
 * critical: below the reserve there; adjust: within ADJUST_MARGIN_PCT of it;
 * warning: DRIFT_PCT below plan but the stop still works; normal otherwise
 * (above plan by ABOVE_PCT suggests a re-plan to drop a stop). A plan aims
 * near the reserve, so critical and adjust also need a projected shortfall of
 * DRIFT_PCT against it. It recommends; it never changes a route, and never
 * says a charger is free.
 */
object EnRoute {
    const val ADJUST_MARGIN_PCT = 5.0
    const val RATIO_MIN = 0.75
    const val RATIO_MAX = 1.5

    data class Known(val name: String, val km: Double, val id: String)

    data class Assessment(
        val level: String,                  // normal | warning | adjust | critical
        val replan: Boolean,
        val action: String,
        val expectedPct: Double?,
        val deviationPct: Double? = null,
        val ratio: Double? = null,
        val targetKm: Double? = null,
        val plannedTargetPct: Double? = null,
        val projectedTargetPct: Double? = null,
        val shortfallPct: Double? = null,
        val reachKm: Double? = null,
        val atStop: Int? = null,
        val staleReading: Boolean = false,
        val alternatives: List<Known> = emptyList(),   // on this plan, within reach
    )

    /** The plan's charge arriving at [km]: before any charging step there. */
    private fun arriveProfile(profile: List<Pair<Double, Double>>, km: Double): Double? {
        if (profile.none { it.first <= km + 1e-9 }) return null
        return profile.firstOrNull { abs(it.first - km) < 1e-9 }?.second
            ?: Replan.expectedPct(profile, km)
    }

    fun assess(plan: Plan, kmAlong: Double, actualPct: Double, readingAgeS: Double?,
               reservePct: Double, chargedSincePlan: Boolean): Assessment {
        val l = Locale.ROOT
        val expected = Replan.expectedPct(plan.profile, kmAlong)
            ?: return Assessment("normal", false, "No plan profile to compare with.", null)
        if (readingAgeS == null || readingAgeS > Replan.READING_MAX_AGE_MS / 1000.0) {
            return Assessment("normal", false,
                "Waiting for a fresh battery reading; nothing judged.", expected,
                staleReading = true)
        }
        val atStop = plan.stops.indexOfFirst { abs(it.km - kmAlong) <= Replan.AT_STOP_KM }
        if (atStop >= 0) {
            return Assessment("normal", false, "At charging stop ${atStop + 1}: the plan " +
                "resumes once charging starts.", expected, atStop = atStop)
        }
        val firstStop = plan.stops.firstOrNull()?.km
        val usedPlan = plan.startPct - expected
        val usedReal = plan.startPct - actualPct
        var ratio = 1.0
        if (!chargedSincePlan && (firstStop == null || kmAlong < firstStop - 1.0) &&
            usedPlan >= Replan.MIN_USED_PCT && usedReal > 0) {
            ratio = min(RATIO_MAX, max(RATIO_MIN, usedReal / usedPlan))
        }
        val nxt = plan.stops.firstOrNull { it.km > kmAlong }
        val targetKm = nxt?.km ?: plan.distanceKm
        val target = arriveProfile(plan.profile, targetKm) ?: expected
        val projected = actualPct - (expected - target) * ratio
        val deviation = actualPct - expected
        val where = if (nxt != null)
            "charging stop ${plan.stops.indexOf(nxt) + 1} (${nxt.charger.name})" else "the destination"
        val perKm = if (targetKm > kmAlong && expected > target)
            (expected - target) / (targetKm - kmAlong) * ratio else null
        val reach = perKm?.let { (actualPct - reservePct) / it }
        val shortfall = target - projected
        fun done(level: String, replan: Boolean, action: String, alts: List<Known> = emptyList()) =
            Assessment(level, replan, action, expected, deviation, ratio, targetKm, target,
                projected, shortfall, reach?.let { max(0.0, it) }, null, false, alts)

        if (projected < reservePct && shortfall >= Replan.DRIFT_PCT) {
            val within = LinkedHashMap<String, Known>()
            if (reach != null) {
                for (s in plan.stops) {
                    val known = (if (s === nxt) emptyList() else
                        listOf(Known(s.charger.name, s.km, s.charger.id))) +
                        s.alternatives.map { Known(it.charger.name, it.km, it.charger.id) }
                    for (a in known) {
                        if (kmAlong < a.km && a.km <= kmAlong + reach) {
                            within.putIfAbsent(a.id.ifEmpty { a.name }, a)
                        }
                    }
                }
            }
            val alts = within.values.sortedBy { it.km }
            val known = if (alts.isNotEmpty()) "Chargers on this plan within reach are listed."
            else "No charger on this plan is within reach: re-plan to search for one."
            return done("critical", true, String.format(l,
                "At this rate the charge falls below the %.0f %% reserve before %s (%.0f %% " +
                    "there). Charge sooner: re-plan from here. %s", reservePct, where, projected,
                known), alts)
        }
        if (projected < reservePct + ADJUST_MARGIN_PCT && shortfall >= Replan.DRIFT_PCT) {
            return done("adjust", true, String.format(l,
                "%s is getting tight: about %.0f %% there against %.0f %% planned. Re-plan " +
                    "from here.", where.replaceFirstChar { it.uppercase() }, projected, target))
        }
        if (deviation <= -Replan.DRIFT_PCT || shortfall >= Replan.DRIFT_PCT) {
            return done("warning", false, String.format(l,
                "Charge %.0f %%, %.0f below plan; %s still works at about %.0f %%.",
                actualPct, -deviation, where, projected))
        }
        if (deviation >= Replan.ABOVE_PCT) {
            return done("normal", true, String.format(l,
                "Charge %.0f %%, %.0f above plan: a stop may no longer be needed. Re-plan to " +
                    "check.", actualPct, deviation))
        }
        return done("normal", false, "On plan.")
    }
}
