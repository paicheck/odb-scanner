package com.odbscanner.route

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Parity with `routing/planner.py`: every case in tests/data/route_vectors.json
 * (written by tools/export_route_vectors.py from the Python planner) must plan
 * the same here -- same status, same stops at the same km, same charge levels.
 * Floating-point libraries may differ in the last bits, so continuous values
 * get a small tolerance; stop choice and counts must match exactly.
 */
class PlannerTest {

    private val vectors: JSONObject by lazy {
        // Unit tests run with the module (android/app) as working directory.
        JSONObject(File("../../tests/data/route_vectors.json").readText(Charsets.UTF_8))
    }

    private fun car(o: JSONObject) = CarModel(
        usableKwh = o.optDouble("usable_kwh", 53.2),
        consumptionKwhPerKm = o.optDouble("consumption_kwh_per_km", 0.17),
    )

    private fun settings(o: JSONObject) = PlanSettings(
        reservePct = o.optDouble("reserve_pct", 10.0),
        chargeToPct = o.optDouble("charge_to_pct", 80.0),
        minChargerKw = o.optDouble("min_charger_kw", 50.0),
        maxStops = o.optInt("max_stops", 8),
    )

    private fun points(a: JSONArray) = (0 until a.length()).map { i ->
        val p = a.getJSONArray(i)
        RoutePoint(p.getDouble(0), p.getDouble(1), if (p.isNull(2)) null else p.getDouble(2))
    }

    private fun chargers(a: JSONArray) = (0 until a.length()).map { i ->
        val c = a.getJSONObject(i)
        Charger(c.getString("name"), c.getDouble("lat"), c.getDouble("lon"),
            c.getDouble("power_kw"), c.optString("operator"), c.optString("address"))
    }

    @Test
    fun chargePowerMatchesPython() {
        val rows = vectors.getJSONArray("charge_power")
        for (i in 0 until rows.length()) {
            val r = rows.getJSONArray(i)
            assertEquals("row $i", r.getDouble(2),
                Planner.chargePowerKw(r.getDouble(0), ID3_58_CURVE, r.getDouble(1)), 1e-6)
        }
    }

    @Test
    fun chargeSecondsMatchPython() {
        val car = CarModel(53.2, 0.17)
        val rows = vectors.getJSONArray("charge_seconds")
        for (i in 0 until rows.length()) {
            val r = rows.getJSONArray(i)
            assertEquals("row $i", r.getDouble(3),
                Planner.chargeSeconds(r.getDouble(0), r.getDouble(1), car, r.getDouble(2)), 0.01)
        }
    }

    @Test
    fun everyCaseMatchesPython() {
        val cases = vectors.getJSONArray("cases")
        assertTrue(cases.length() >= 5)
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val name = c.getString("name")
            val all = chargers(c.getJSONArray("chargers"))
            val plan = Planner.planTrip(
                points(c.getJSONArray("points")), c.getDouble("drive_s"),
                c.getDouble("start_pct"), car(c.getJSONObject("car")),
                settings(c.getJSONObject("settings")),
            ) { lat, lon, r -> all.filter { Planner.haversineKm(lat, lon, it.lat, it.lon) <= r } }
            val want = c.getJSONObject("expect")

            assertEquals(name, want.getString("status"), plan.status)
            assertEquals(name, want.getDouble("distance_km"), Planner.round(plan.distanceKm, 1), 0.051)
            if (want.isNull("arrival_pct")) assertNull(name, plan.arrivalPct)
            else assertEquals(name, want.getDouble("arrival_pct"), plan.arrivalPct!!, 0.051)
            if (want.isNull("unreachable_at_km")) assertNull(name, plan.unreachableAtKm)
            else assertEquals(name, want.getDouble("unreachable_at_km"), plan.unreachableAtKm!!, 0.051)
            assertEquals(name, want.getDouble("charge_s"), plan.chargeS, 1.0)

            val stops = want.getJSONArray("stops")
            assertEquals(name, stops.length(), plan.stops.size)
            for (k in 0 until stops.length()) {
                val s = stops.getJSONObject(k)
                val got = plan.stops[k]
                assertEquals("$name stop $k", s.getString("name"), got.charger.name)
                assertEquals("$name stop $k", s.getDouble("km"), got.km, 0.051)
                assertEquals("$name stop $k", s.getDouble("arrive_pct"), got.arrivePct, 0.051)
                assertEquals("$name stop $k", s.getDouble("depart_pct"), got.departPct, 0.051)
                assertEquals("$name stop $k", s.getLong("charge_min"), got.chargeMin)
            }
            assertEquals(name, want.getInt("profile_points"), plan.profile.size)
            val last = want.getJSONArray("profile_last")
            assertEquals(name, last.getDouble(0), plan.profile.last().first, 0.011)
            assertEquals(name, last.getDouble(1), plan.profile.last().second, 0.051)
        }
    }

    @Test
    fun pythonRoundingIsHalfEven() {
        assertEquals(0.0, Planner.round(0.5), 0.0)
        assertEquals(2.0, Planner.round(1.5), 0.0)
        // Python's round(2.675, 2) is 2.67: the double is just below 2.675.
        assertEquals(2.67, Planner.round(2.675, 2), 0.0)
    }

    @Test
    fun servicesParseLikeProvidersPy() {
        val places = RouteServices.parseGeocode(JSONObject(
            """{"features":[{"geometry":{"coordinates":[-0.17,51.24]},
                "properties":{"label":"Redhill"}},{"geometry":{},"properties":{}}]}"""), "x")
        assertEquals(listOf(Place("Redhill", 51.24, -0.17)), places)

        val route = RouteServices.parseRoute(JSONObject(
            """{"features":[{"geometry":{"coordinates":[[-0.17,51.24,80.0],[-0.1,51.5]]},
                "properties":{"summary":{"distance":32000,"duration":2400}}}]}"""))
        assertEquals(RoutePoint(51.24, -0.17, 80.0), route.points[0])
        assertNull(route.points[1].ele)
        assertEquals(32.0, route.distanceKm, 1e-9)

        val chargers = RouteServices.parseChargers(JSONArray(
            """[{"AddressInfo":{"Title":"Services","Latitude":51.1,"Longitude":-0.2,
                 "AddressLine1":"M23","Town":"Crawley"},"OperatorInfo":{"Title":"Ionity"},
                 "Connections":[{"ConnectionTypeID":33,"PowerKW":350},
                                {"ConnectionTypeID":2,"PowerKW":400}]},
                {"AddressInfo":{"Title":"Type 2 only","Latitude":51.1,"Longitude":-0.2},
                 "Connections":[{"ConnectionTypeID":25,"PowerKW":22}]},
                {"AddressInfo":{"Title":"Broken"},"Connections":[{"ConnectionTypeID":33}]},
                {"AddressInfo":{"Title":null,"Latitude":51.0,"Longitude":0.0,"Town":null},
                 "OperatorInfo":null,"Connections":[{"ConnectionTypeID":33,"PowerKW":null}]}]"""))
        assertEquals(
            listOf(
                Charger("Services", 51.1, -0.2, 350.0, "Ionity", "M23, Crawley"),
                Charger("Charger", 51.0, 0.0, 0.0, "", ""),
            ),
            chargers,
        )
    }

    @Test(expected = RoutingException::class)
    fun noRouteIsAPlainError() {
        RouteServices.parseRoute(JSONObject("""{"features":[]}"""))
    }

    @Test
    fun countryListMatchesTheDashboard() {     // tests/test_route_planner.py::test_country_list
        mapOf("gb" to "GB", " gb, fr " to "GB,FR", "GB FR" to "GB,FR", "GBR" to "GBR",
            "" to "", "UK!, x, england" to "").forEach { (raw, want) ->
            assertEquals(raw, want, RouteServices.countryList(raw))
        }
    }
}
