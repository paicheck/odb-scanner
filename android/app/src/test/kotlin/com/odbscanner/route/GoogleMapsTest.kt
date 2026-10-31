package com.odbscanner.route

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Parity with routing/handoff.py: every case in tests/data/handoff_vectors.json. */
class GoogleMapsTest {
    private val vectors by lazy {
        JSONObject(File("../../tests/data/handoff_vectors.json").readText(Charsets.UTF_8))
    }

    private fun pt(a: JSONArray) = a.getDouble(0) to a.getDouble(1)

    private fun avoid(o: JSONObject?) = o?.let {
        Avoid(it.optBoolean("motorways"), it.optBoolean("tolls"), it.optBoolean("ferries"))
    }

    @Test
    fun everyLinkMatchesPython() {
        val cases = vectors.getJSONArray("trip_links")
        assertTrue(cases.length() >= 6)
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val stops = c.getJSONArray("stops").let { a -> (0 until a.length()).map { pt(a.getJSONArray(it)) } }
            val got = GoogleMaps.tripLink(
                if (c.isNull("origin")) null else pt(c.getJSONArray("origin")), stops,
                pt(c.getJSONArray("destination")), avoid(c.optJSONObject("avoid")),
                c.optInt("max_waypoints", GoogleMaps.MAX_WAYPOINTS), c.optBoolean("navigate", true))
            val want = c.getJSONObject("expect")
            val name = c.getString("name")
            assertEquals(name, want.getString("url"), got.url)
            assertEquals(name, want.getInt("stops_sent"), got.stopsSent)
            assertEquals(name, want.getBoolean("partial"), got.partial)
        }
    }

    @Test
    fun everyNavigationIntentMatchesPython() {
        val cases = vectors.getJSONArray("next_stop_intents")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            assertEquals(c.getString("expect"),
                GoogleMaps.nextStopIntent(pt(c.getJSONArray("target")), avoid(c.optJSONObject("avoid"))))
        }
    }
}
