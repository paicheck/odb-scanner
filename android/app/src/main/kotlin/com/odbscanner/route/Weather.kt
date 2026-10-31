package com.odbscanner.route

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * The forecast along a planned route, from Open-Meteo (free, no key), as the
 * dashboard does it (routing/weather.py): the route sampled every SAMPLE_KM
 * (at most MAX_SAMPLES), the hourly forecast for those places in one GET, and
 * each sample given the hour the car is expected there (leaving now, at the
 * routing provider's driving time). The planner turns the wind into energy.
 *
 * Positions are rounded to GRID_DEG (about 11 km) before they are sent, and a
 * forecast is kept CACHE_MS per rounded position. Any failure gives no
 * forecast and a note that says so; the plan then counts no wind. Nothing here
 * talks to the car.
 */
object Weather {
    const val BASE = "https://api.open-meteo.com/v1/forecast"
    const val GRID_DEG = 0.1
    const val SAMPLE_KM = 25.0
    const val MAX_SAMPLES = 24
    const val CACHE_MS = 30 * 60_000L
    const val MAX_HOUR_GAP_S = 5400.0     // a forecast hour further than this from the ETA is none
    private const val TIMEOUT_MS = 10_000

    data class Sample(val km: Double, val lat: Double, val lon: Double, val etaS: Double)
    data class Series(val t: List<Double>, val temp: List<Double?>, val wind: List<Double?>,
                      val dir: List<Double?>)

    private val cache = HashMap<Pair<Double, Double>, Pair<Long, Series>>()

    /** A coordinate rounded to the grid that is sent (Python's round: half to even). */
    fun grid(x: Double): Double = Planner.round(Planner.round(x / GRID_DEG) * GRID_DEG, 1)

    fun samplePoints(points: List<RoutePoint>, driveS: Double, departS: Double): List<Sample> {
        val cum = Planner.cumulativeKm(points)
        val total = cum.last()
        val step = max(SAMPLE_KM, total / MAX_SAMPLES)
        val out = mutableListOf<Sample>()
        var km = 0.0
        while (km < total - 1e-3 || out.isEmpty()) {
            var i = cum.indexOfFirst { it >= km }                 // bisect_left
            if (i < 0) i = points.size - 1
            i = min(points.size - 1, i)
            val eta = departS + if (total > 0) driveS * km / total else 0.0
            out += Sample(km, points[i].lat, points[i].lon, eta)
            km += step
        }
        return out
    }

    fun url(cells: List<Pair<Double, Double>>): String {
        fun one(x: Double) = String.format(Locale.ROOT, "%.1f", x)
        return BASE + "?latitude=" + cells.joinToString(",") { one(it.first) } +
            "&longitude=" + cells.joinToString(",") { one(it.second) } +
            "&hourly=temperature_2m,wind_speed_10m,wind_direction_10m" +
            "&timeformat=unixtime&timezone=GMT&forecast_days=3"
    }

    /** One forecast per place asked for: a list for several, an object for one. */
    fun parse(text: String, places: Int): List<Series> {
        val trimmed = text.trim()
        val arr = if (trimmed.startsWith("[")) JSONArray(trimmed)
        else JSONArray().put(JSONObject(trimmed))
        if (arr.length() != places) {
            throw RoutingException("Open-Meteo gave ${arr.length()} forecasts for $places places")
        }
        return (0 until arr.length()).map { i ->
            val h = arr.getJSONObject(i).optJSONObject("hourly") ?: JSONObject()
            fun nums(name: String): List<Double?> = h.optJSONArray(name)?.let { a ->
                (0 until a.length()).map { k -> if (a.isNull(k)) null else a.optDouble(k).takeIf { !it.isNaN() } }
            } ?: emptyList()
            Series(nums("time").map { it ?: Double.NaN }, nums("temperature_2m"),
                nums("wind_speed_10m"), nums("wind_direction_10m"))
        }
    }

    /** (temperature, wind, direction) for the hour nearest [whenS], or nulls. */
    fun at(s: Series, whenS: Double): Triple<Double?, Double?, Double?> {
        if (s.t.isEmpty()) return Triple(null, null, null)
        var i = s.t.indexOfFirst { it >= whenS }
        if (i < 0) i = s.t.size
        val j = listOf(i - 1, i).filter { it in s.t.indices }.minByOrNull { abs(s.t[it] - whenS) }
            ?: return Triple(null, null, null)
        if (abs(s.t[j] - whenS) > MAX_HOUR_GAP_S) return Triple(null, null, null)
        return Triple(s.temp.getOrNull(j), s.wind.getOrNull(j), s.dir.getOrNull(j))
    }

    /**
     * The forecast along the route for the planner, and a sentence for the
     * screen. [fetch] is the GET (replaced in tests); [nowMs] the clock.
     */
    fun along(
        points: List<RoutePoint>, driveS: Double, departS: Double? = null,
        fetch: (String) -> String = ::get, nowMs: Long = System.currentTimeMillis(),
    ): Pair<List<Conditions>?, String> = synchronized(cache) {
        val nowS = nowMs / 1000.0
        val samples = samplePoints(points, driveS, departS ?: nowS)
        val cells = samples.map { grid(it.lat) to grid(it.lon) }
        val want = cells.distinct().filter { c ->
            cache[c]?.let { nowMs - it.first > CACHE_MS } ?: true
        }
        if (want.isNotEmpty()) {
            val got = try {
                parse(fetch(url(want)), want.size)
            } catch (e: RoutingException) {
                return Pair(null, "No weather forecast (${e.message}); the wind is not counted.")
            } catch (e: org.json.JSONException) {
                return Pair(null, "No weather forecast (Open-Meteo did not answer usefully); " +
                    "the wind is not counted.")
            }
            if (cache.size > 2000) cache.clear()
            want.zip(got).forEach { (c, series) -> cache[c] = nowMs to series }
        }
        val out = samples.zip(cells).mapNotNull { (s, c) ->
            val (temp, wind, dir) = at(cache.getValue(c).second, s.etaS)
            if (temp == null && wind == null) null else Conditions(s.km, temp, wind, dir)
        }
        if (out.isEmpty()) {
            return Pair(null, "No weather forecast for the time of this trip; the wind is not counted.")
        }
        Pair(out, "Weather: Open-Meteo's forecast at ${out.size} points along the way for when " +
            "you pass them (positions sent rounded to $GRID_DEG°, about 11 km).")
    }

    private fun get(url: String): String {
        val conn = URL(url).openConnection() as HttpURLConnection
        try {
            conn.connectTimeout = TIMEOUT_MS
            conn.readTimeout = TIMEOUT_MS
            conn.setRequestProperty("Accept", "application/json")
            val code = conn.responseCode
            val text = (if (code < 400) conn.inputStream else conn.errorStream)
                ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            if (code >= 400) throw RoutingException("Open-Meteo returned HTTP $code")
            return text
        } catch (e: IOException) {
            throw RoutingException("could not reach Open-Meteo: ${e.message}", e)
        } finally {
            conn.disconnect()
        }
    }
}
