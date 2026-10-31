package com.odbscanner.route

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The forecast along a route, as tests/test_weather.py checks routing/weather.py.
 *  No test reaches the network: the GET is replaced. */
class WeatherTest {
    private val t0 = 1_800_000_000.0             // a fixed "now", on the hour

    /** A road due north from 50 N, one point per km. */
    private fun north(km: Int) = (0..km).map { RoutePoint(50.0 + it / 111.195, 0.0) }

    /** Answers like Open-Meteo for every place asked about: hourly from [start]. */
    private class Fake(val temp: Double = 8.0, val wind: Double = 25.0, val dir: Double = 0.0,
                       val start: Double = 1_800_000_000.0, val fail: Exception? = null) {
        val calls = mutableListOf<String>()
        fun get(url: String): String {
            calls += url
            fail?.let { throw it }
            val n = url.substringAfter("latitude=").substringBefore("&").split(",").size
            val hours = JSONArray().apply { (0 until 72).forEach { put(start + 3600.0 * it) } }
            fun same(v: Double) = JSONArray().apply { repeat(72) { put(v) } }
            val one = JSONObject().put("hourly", JSONObject().put("time", hours)
                .put("temperature_2m", same(temp)).put("wind_speed_10m", same(wind))
                .put("wind_direction_10m", same(dir)))
            return if (n > 1) JSONArray().apply { repeat(n) { put(one) } }.toString() else one.toString()
        }
    }

    @Test
    fun theRouteIsSampledWithTheTimeTheCarIsThere() {
        val got = Weather.samplePoints(north(200), 7200.0, t0)
        assertEquals(listOf(0L, 25, 50, 75, 100, 125, 150, 175), got.map { Math.round(it.km) })
        assertEquals(t0 + 3600, got[4].etaS, 1.0)                // halfway, an hour on
        assertTrue(Weather.samplePoints(north(1000), 36000.0, t0).size <= Weather.MAX_SAMPLES)
    }

    @Test
    fun positionsAreRoundedBeforeTheyAreSent() {
        val fake = Fake()
        val (conditions, note) = Weather.along(north(200), 7200.0, t0, fake::get, (t0 * 1000).toLong())
        val sent = fake.calls.single()
        for (key in listOf("latitude=", "longitude=")) {
            sent.substringAfter(key).substringBefore("&").split(",").forEach {
                assertEquals(it, 1, it.substringAfter(".").length)    // one decimal: ~11 km
            }
        }
        assertTrue(note.contains("rounded"))
        assertEquals(8.0, conditions!!.first().tempC!!, 1e-9)
        assertEquals(0.0, conditions.first().windFromDeg!!, 1e-9)
    }

    @Test
    fun oneRequestThenTheCache() {
        val fake = Fake()
        val road = north(150).map { it.copy(lon = 3.3) }           // its own cells
        val now = (t0 * 1000).toLong()
        Weather.along(road, 5400.0, t0, fake::get, now)
        Weather.along(road, 5400.0, t0, fake::get, now + 600_000)
        assertEquals(1, fake.calls.size)
        Weather.along(road, 5400.0, t0, fake::get, now + Weather.CACHE_MS + 1)
        assertEquals(2, fake.calls.size)
    }

    @Test
    fun noAnswerMeansNoForecastAndSaysSo() {
        val road = north(100).map { it.copy(lon = 7.7) }
        val down = Fake(fail = RoutingException("could not reach Open-Meteo: timeout"))
        val (none, note) = Weather.along(road, 3600.0, t0, down::get, (t0 * 1000).toLong())
        assertNull(none)
        assertTrue(note, note.startsWith("No weather forecast"))
        val later = Weather.along(north(100).map { it.copy(lon = 8.8) }, 3600.0,
            t0 + 10 * 86400, Fake()::get, (t0 * 1000).toLong())
        assertNull(later.first)                                    // beyond the forecast
    }

    @Test
    fun aSinglePlaceComesBackAsAnObject() {
        val got = Weather.parse(Fake().get(Weather.url(listOf(51.5 to -0.1))), 1)
        assertEquals(72, got.single().t.size)
    }

    @Test
    fun gridRoundingMatchesPython() {          // routing/weather.py _grid
        assertEquals(51.5, Weather.grid(51.4789), 1e-9)
        assertEquals(-0.1, Weather.grid(-0.1276), 1e-9)
        assertEquals(50.0, Weather.grid(50.04), 1e-9)
    }

    @Test
    fun aHeadwindNorthUsesMoreEnergyThanCalm() {
        val road = north(150)
        val car = CarModel(usableKwh = 53.2, consumptionKwhPerKm = 0.17)
        val calm = Planner.planTrip(road, 5400.0, 90.0, car, PlanSettings(), emptyList())
        val head = Planner.planTrip(road, 5400.0, 90.0, car, PlanSettings(), emptyList(),
            weather = listOf(Conditions(0.0, 10.0, 30.0, 0.0)))
        assertTrue(head.arrivalPct!! < calm.arrivalPct!!)
        assertTrue(head.windKwh!! > 0)
        assertNull(calm.windKwh)
    }
}
