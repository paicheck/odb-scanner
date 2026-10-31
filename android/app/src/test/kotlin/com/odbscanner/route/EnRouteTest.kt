package com.odbscanner.route

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Parity with `analysis/enroute.py`: every case in tests/data/enroute_vectors.json
 * (written by tools/export_enroute_vectors.py from the Python) must be judged
 * the same way here.
 */
class EnRouteTest {
    private val vectors by lazy {
        JSONObject(File("../../tests/data/enroute_vectors.json").readText(Charsets.UTF_8))
    }

    private fun plan(o: JSONObject): Plan {
        val profile = o.getJSONArray("profile").let { a ->
            (0 until a.length()).map { a.getJSONArray(it).let { p -> p.getDouble(0) to p.getDouble(1) } }
        }
        fun charger(j: JSONObject) = Charger(j.getString("name"), 0.0, 0.0, 150.0, id = j.getString("id"))
        val stops = o.getJSONArray("stops").let { a ->
            (0 until a.length()).map { i ->
                val s = a.getJSONObject(i)
                val alts = s.getJSONArray("alternatives").let { b ->
                    (0 until b.length()).map { k ->
                        val x = b.getJSONObject(k)
                        Alternative(charger(x), x.getDouble("km"), 0.0, 0.0, false)
                    }
                }
                Stop(km = s.getDouble("km"), charger = charger(s), arrivePct = s.getDouble("arrive_pct"),
                    departPct = 0.0, chargeMin = 0, kwh = 0.0, pricePerKwh = 0.0, priceSource = "",
                    cost = 0.0, detourKm = 0.0, alternatives = alts)
            }
        }
        return Plan("with_stops", o.getDouble("distance_km"), 0.0, o.getDouble("start_pct"),
            o.optDouble("arrival_pct"), stops, profile, null, 0.0, 0.0, "balanced", emptyList())
    }

    private fun near(name: String, want: JSONObject, key: String, got: Double?, tol: Double) {
        if (want.isNull(key)) assertNull("$name $key", got)
        else assertEquals("$name $key", want.getDouble(key), got!!, tol)
    }

    @Test
    fun everyCaseMatchesPython() {
        val plans = vectors.getJSONObject("plans")
        val cases = vectors.getJSONArray("cases")
        assertTrue(cases.length() >= 10)
        val levels = mutableSetOf<String>()
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val name = c.getString("name")
            val got = EnRoute.assess(plan(plans.getJSONObject(c.getString("plan"))),
                c.getDouble("km_along"), c.getDouble("actual_pct"),
                c.getDouble("reading_age_s"), c.getDouble("reserve_pct"),
                c.getBoolean("charged_since_plan"))
            val want = c.getJSONObject("expect")
            assertEquals(name, want.getString("level"), got.level)
            assertEquals(name, want.getBoolean("replan"), got.replan)
            levels += got.level
            near(name, want, "expected_pct", got.expectedPct, 0.051)
            near(name, want, "deviation_pct", got.deviationPct, 0.051)
            near(name, want, "ratio", got.ratio, 0.0051)
            near(name, want, "target_km", got.targetKm, 0.051)
            near(name, want, "planned_target_pct", got.plannedTargetPct, 0.051)
            near(name, want, "projected_target_pct", got.projectedTargetPct, 0.051)
            near(name, want, "shortfall_pct", got.shortfallPct, 0.051)
            near(name, want, "reach_km", got.reachKm, 0.051)
            if (want.isNull("at_stop")) assertNull(name, got.atStop) else assertEquals(name, want.getInt("at_stop"), got.atStop)
            assertEquals(name, !want.isNull("stale_reading"), got.staleReading)
            assertEquals("$name alternatives", strings(want.getJSONArray("alternatives")),
                got.alternatives.map { it.name })
        }
        assertEquals(setOf("normal", "warning", "adjust", "critical"), levels)
    }

    @Test
    fun itNeverClaimsAChargerIsFree() {
        val plans = vectors.getJSONObject("plans")
        val got = EnRoute.assess(plan(plans.getJSONObject("hand")), 60.0, 40.0, 0.0, 10.0, false)
        assertEquals("critical", got.level)
        assertTrue(got.action, !got.action.contains("available", ignoreCase = true))
    }

    private fun strings(a: JSONArray) = (0 until a.length()).map { a.getString(it) }
}
