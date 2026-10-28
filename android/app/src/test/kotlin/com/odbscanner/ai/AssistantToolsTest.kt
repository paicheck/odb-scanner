package com.odbscanner.ai

import com.odbscanner.db.MeasurementEntity
import com.odbscanner.route.Planner
import com.odbscanner.route.RouteServices
import com.odbscanner.route.RoutePoint
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class AssistantToolsTest {

    @Test
    fun everyToolHasAValidSchema() {
        val names = AssistantTools.SPECS.map { it.name }
        assertEquals(names.toSet().size, names.size)
        for (t in AssistantTools.SPECS) {
            assertEquals(t.name, "object", t.parameters.getString("type"))
            val props = t.parameters.getJSONObject("properties")
            val req = t.parameters.getJSONArray("required")
            for (i in 0 until req.length()) assertTrue(t.name, props.has(req.getString(i)))
            assertTrue(t.name, t.description.length > 20)
        }
        // The assistant reads; the only actions are on trips -- nothing reaches the car.
        assertTrue(names.containsAll(listOf("get_battery", "get_fault_codes", "get_trip",
            "get_directions", "find_chargers", "plan_trip", "replan_trip", "start_navigation",
            "get_navigation", "reroute", "get_readings", "get_history")))
        assertFalse(names.any { it.contains("clear") || it.contains("write") || it.contains("send") })
    }

    @Test
    fun noVinLeaves() {
        assertEquals("""{"car":"[VIN]"}""", AssistantTools.redactVin("""{"car":"WVWZZZE1ZTEST0001"}"""))
        assertEquals("charge 80", AssistantTools.redactVin("charge 80"))
    }

    @Test
    fun distancesInKmAndMiles() {
        val d = AssistantTools.dist(100.0)
        assertEquals(100.0, d.getDouble("km"), 0.0)
        assertEquals(62.1, d.getDouble("miles"), 0.0)
    }

    @Test
    fun readingsCarryUnitsAndAge() {
        val now = Instant.parse("2026-10-27T12:00:00Z")
        assertEquals("12 min ago", AssistantTools.age("2026-10-27T11:48:00Z", now))
        assertEquals("unknown", AssistantTools.age("garbage", now))
        val energy = MeasurementEntity(ts = Instant.now().toString(), ecu = "bms", service = "22",
            pid = "2AB8", key = "hv_energy_content", value = 46050.0, textValue = null, unit = "Wh",
            provenance = "reported", decodingMethod = "builtin", docStatus = null, rawResponse = "",
            error = null)
        val r = AssistantTools.reading(energy)
        assertEquals(46.1, r.getDouble("value"), 0.0)
        assertEquals("kWh", r.getString("unit"))
    }

    @Test
    fun routeStepsArePlacedByKm() {
        val pts = (0..10).map { RoutePoint(50.0 + it / 111.195, 0.0) }
        val props = JSONObject("""{"segments":[{"steps":[
            {"distance":3000,"instruction":"Head north on A23","name":"A23","way_points":[0,3]},
            {"distance":7000,"instruction":"Take the 2nd exit onto M23","name":"M23",
             "exit_number":2,"way_points":[3,10]}]}]}""")
        val steps = RouteServices.parseSteps(props, Planner.cumulativeKm(pts))
        assertEquals(2, steps.size)
        assertEquals(3.0, steps[1].km, 0.05)
        assertEquals(2, steps[1].exitNumber)
        assertEquals("M23", steps[1].road)
        assertEquals(null, steps[0].exitNumber)
    }
}
