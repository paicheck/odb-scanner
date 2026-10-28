package com.odbscanner.route

import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.Locale

/**
 * Mapbox Directions (driving-traffic): routes in today's traffic, with the
 * same trip in typical traffic for comparison, heavy-traffic stretches, up to
 * two alternatives, and turn-by-turn guidance with lanes and ready-made
 * British-English announcements. Optional -- the route planner and the
 * guidance also work from OpenRouteService, without traffic or lanes.
 *
 * Needs a Mapbox access token (Route settings; the free tier is 100,000
 * route requests a month). The start, stops and destination go to Mapbox.
 * Blocking -- call from Dispatchers.IO.
 */
class MapboxRouting(private val token: String, private val timeoutMs: Int = 20_000) {

    /** Routes through [waypoints] (lat, lon); alternatives only between two points. */
    fun routes(waypoints: List<Pair<Double, Double>>, avoid: Avoid = Avoid(),
               alternatives: Boolean = true): List<NavRoute> {
        if (token.isBlank()) throw RoutingException("No Mapbox token: add one in Route settings.")
        return MapboxParse.routes(JSONObject(get(url(waypoints, avoid, alternatives, token))))
            .ifEmpty { throw RoutingException("Mapbox found no route") }
    }

    private fun get(url: String): String {
        val conn = URL(url).openConnection() as HttpURLConnection
        try {
            conn.connectTimeout = timeoutMs
            conn.readTimeout = timeoutMs
            conn.setRequestProperty("Accept", "application/json")
            val code = conn.responseCode
            val text = (if (code < 400) conn.inputStream else conn.errorStream)
                ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            val message = runCatching { JSONObject(text).optString("message") }.getOrNull() ?: ""
            when {
                code == 401 || code == 403 -> throw RoutingException(
                    "Mapbox refused the token (HTTP $code). Check it under Route settings.")
                code == 422 -> throw RoutingException("Mapbox could not route this: $message")
                code == 429 -> throw RoutingException("Mapbox rate limit reached; try again later.")
                code >= 400 -> throw RoutingException("Mapbox returned HTTP $code: ${message.take(200)}")
            }
            return text
        } catch (e: IOException) {
            throw RoutingException("Could not reach Mapbox: ${e.message}", e)
        } finally {
            conn.disconnect()
        }
    }

    companion object {
        const val BASE = "https://api.mapbox.com/directions/v5/mapbox/driving-traffic"

        fun url(waypoints: List<Pair<Double, Double>>, avoid: Avoid, alternatives: Boolean,
                token: String): String = buildString {
            append(BASE).append('/')
            append(waypoints.joinToString(";") { (lat, lon) ->
                String.format(Locale.ROOT, "%.6f,%.6f", lon, lat)
            })
            append("?geometries=geojson&overview=full&steps=true")
            append("&banner_instructions=true&voice_instructions=true")
            append("&voice_units=british_imperial&language=en")
            append("&annotations=congestion")
            if (alternatives && waypoints.size == 2) append("&alternatives=true")
            if (avoid.mapbox.isNotEmpty()) append("&exclude=").append(avoid.mapbox.joinToString(","))
            append("&access_token=").append(URLEncoder.encode(token, "UTF-8"))
        }
    }
}
