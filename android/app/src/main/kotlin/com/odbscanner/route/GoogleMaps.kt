package com.odbscanner.route

import java.util.Locale

/**
 * Google Maps links for a planned trip, as the dashboard builds them
 * (routing/handoff.py, which has what Google documents and why), held to it
 * by tests/data/handoff_vectors.json.
 *
 * A Maps URL takes up to [MAX_WAYPOINTS] stops (three in a mobile browser);
 * a trip with more goes as far as the first stop that does not fit, as the
 * destination, and says so -- never silently fewer stops. With the car's
 * position as the origin and dir_action=navigate, Google Maps starts
 * navigating rather than showing a preview.
 */
object GoogleMaps {
    const val BASE = "https://www.google.com/maps/dir/?api=1"
    const val MAX_WAYPOINTS = 9
    const val MAX_WAYPOINTS_BROWSER = 3
    const val MAX_URL = 2048

    data class Handoff(val url: String, val stopsSent: Int, val partial: Boolean, val note: String)

    private fun ll(p: Pair<Double, Double>) =
        String.format(Locale.ROOT, "%.6f%%2C%.6f", p.first, p.second)

    private fun urlAvoid(a: Avoid?) = listOfNotNull("tolls".takeIf { a?.tolls == true },
        "highways".takeIf { a?.motorways == true }, "ferries".takeIf { a?.ferries == true })

    fun tripLink(origin: Pair<Double, Double>?, stops: List<Pair<Double, Double>>,
                 destination: Pair<Double, Double>, avoid: Avoid? = null,
                 maxWaypoints: Int = MAX_WAYPOINTS, navigate: Boolean = true): Handoff {
        var partial = stops.size > maxWaypoints
        var waypoints = if (partial) stops.take(maxWaypoints) else stops
        var end = if (partial) stops[maxWaypoints] else destination
        var url: String
        while (true) {
            val parts = mutableListOf(BASE, "travelmode=driving")
            if (origin != null) parts += "origin=" + ll(origin)
            parts += "destination=" + ll(end)
            if (waypoints.isNotEmpty()) parts += "waypoints=" + waypoints.joinToString("%7C") { ll(it) }
            val codes = urlAvoid(avoid)
            if (codes.isNotEmpty()) parts += "avoid=" + codes.joinToString("%2C")
            if (navigate) parts += "dir_action=navigate"
            url = parts.joinToString("&")
            if (url.length <= MAX_URL || waypoints.isEmpty()) break
            partial = true
            end = waypoints.last()
            waypoints = waypoints.dropLast(1)
        }
        val sent = waypoints.size + if (partial) 1 else 0
        val note = if (!partial) "" else "Google Maps takes $maxWaypoints stops from a link: " +
            "this goes as far as charging stop $sent; the rest is handed over from there."
        return Handoff(url, sent, partial, note)
    }

    /** The intent that navigates from where the phone is to one place (no waypoints). */
    fun nextStopIntent(target: Pair<Double, Double>, avoid: Avoid? = null): String {
        val codes = listOfNotNull("t".takeIf { avoid?.tolls == true },
            "h".takeIf { avoid?.motorways == true }, "f".takeIf { avoid?.ferries == true })
        return String.format(Locale.ROOT, "google.navigation:q=%.6f,%.6f&mode=d",
            target.first, target.second) + if (codes.isEmpty()) "" else "&avoid=" + codes.joinToString("")
    }
}
