package com.odbscanner.route

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Planning could not be done; the message says why, in plain words. */
class RoutingException(message: String, cause: Throwable? = null) : Exception(message, cause)

data class Place(val label: String, val lat: Double, val lon: Double)

data class Route(val points: List<RoutePoint>, val distanceKm: Double, val durationS: Double)

/**
 * OpenRouteService (place search, driving route with elevation) and
 * OpenChargeMap (CCS chargers): the same requests `routing/providers.py` makes
 * for the dashboard. Both need a free key, kept in [RoutePrefs]. The start,
 * destination and route go to these two services and nowhere else.
 *
 * HttpURLConnection, like CloudSync: no new networking dependency. Every
 * failure is a [RoutingException] with a message fit to show the driver.
 * Blocking -- call from Dispatchers.IO.
 */
class RouteServices(
    private val orsKey: String,
    private val ocmKey: String,
    private val minChargerKw: Double = 50.0,
    private val timeoutMs: Int = 20_000,
    countries: String = "",
) {
    private val countries = countryList(countries)

    fun geocode(text: String, near: Pair<Double, Double>? = null, size: Int = 5): List<Place> {
        requireOrs()
        val q = buildString {
            append("$ORS_BASE/geocode/search?api_key=").append(enc(orsKey))
            append("&text=").append(enc(text)).append("&size=").append(size)
            if (near != null) {
                append("&focus.point.lat=").append(near.first)
                append("&focus.point.lon=").append(near.second)
            }
            // A focus point barely moves the ranking ("Brighton" is Brighton, NY
            // even from England); only a country limit fixes that.
            if (countries.isNotEmpty()) append("&boundary.country=").append(enc(countries))
        }
        return parseGeocode(JSONObject(request("GET", q)), text, size)
    }

    fun route(start: Pair<Double, Double>, end: Pair<Double, Double>): Route {
        requireOrs()
        val body = JSONObject()
            .put("coordinates", JSONArray()
                .put(JSONArray().put(start.second).put(start.first))
                .put(JSONArray().put(end.second).put(end.first)))
            .put("elevation", true)
            .put("instructions", false)
        val reply = request("POST", "$ORS_BASE/v2/directions/driving-car/geojson",
            body.toString(), mapOf("Authorization" to orsKey))
        return parseRoute(JSONObject(reply))
    }

    /**
     * Every fast CCS charger within [radiusKm] of the route, in one request
     * (OCM's polyline search), with its listed price text.
     */
    fun chargersAlong(points: List<RoutePoint>, radiusKm: Double): List<Charger> {
        if (ocmKey.isBlank()) throw RoutingException(
            "No OpenChargeMap key. Get a free key at openchargemap.org and enter " +
                "it under Route settings.")
        val q = "$OCM_BASE?output=json&polyline=${enc(encodePolyline(thinLine(points)))}" +
            "&distance=$radiusKm&distanceunit=KM&maxresults=$OCM_MAX_RESULTS" +
            "&connectiontypeid=$CCS2&minpowerkw=$minChargerKw&verbose=false&key=${enc(ocmKey)}"
        return parseChargers(JSONArray(request("GET", q, headers = mapOf("X-API-Key" to ocmKey))))
    }

    private fun requireOrs() {
        if (orsKey.isBlank()) throw RoutingException(
            "No OpenRouteService key. Get a free key at openrouteservice.org and " +
                "enter it under Route settings.")
    }

    private fun request(
        method: String, url: String, body: String? = null,
        headers: Map<String, String> = emptyMap(),
    ): String {
        val host = URL(url).host
        val conn = (URL(url).openConnection() as HttpURLConnection)
        try {
            conn.requestMethod = method
            conn.connectTimeout = timeoutMs
            conn.readTimeout = timeoutMs
            // The route comes back as application/geo+json; accepting only
            // application/json gets HTTP 406 (ORS error 2007) from OpenRouteService.
            conn.setRequestProperty("Accept", "application/json, application/geo+json")
            headers.forEach { (k, v) -> conn.setRequestProperty(k, v) }
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val text = (if (code < 400) conn.inputStream else conn.errorStream)
                ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            when {
                code == 401 || code == 403 -> throw RoutingException(
                    "$host refused the API key (HTTP $code). Check it under Route settings.")
                code == 429 -> throw RoutingException("$host rate limit reached; try again later.")
                code >= 400 -> throw RoutingException("$host returned HTTP $code: ${text.take(200)}")
            }
            return text
        } catch (e: IOException) {
            throw RoutingException("Could not reach $host: ${e.message}", e)
        } finally {
            conn.disconnect()
        }
    }

    companion object {
        const val ORS_BASE = "https://api.openrouteservice.org"
        const val OCM_BASE = "https://api.openchargemap.io/v3/poi/"
        const val CCS2 = 33          // OpenChargeMap ConnectionTypeID: CCS (Type 2)
        const val OCM_MAX_RESULTS = 500
        const val POLYLINE_MAX_POINTS = 300
        // OCM UsageTypeID: private (restricted), privately owned (notice
        // required), private (staff, visitors or customers).
        val PRIVATE_USAGE = setOf(2, 3, 6)

        /** Every n-th point and the last, so the polyline fits in a URL. */
        fun thinLine(points: List<RoutePoint>, limit: Int = POLYLINE_MAX_POINTS): List<RoutePoint> {
            val step = maxOf(1, (points.size + limit - 1) / limit)
            val out = points.filterIndexed { i, _ -> i % step == 0 }.toMutableList()
            if (out.last() !== points.last()) out.add(points.last())
            return out
        }

        /** Google's encoded-polyline format, 5 decimals (as providers.py). */
        fun encodePolyline(points: List<RoutePoint>): String {
            val out = StringBuilder()
            var plat = 0L
            var plon = 0L
            for (p in points) {
                val lat = Planner.round(p.lat * 1e5).toLong()
                val lon = Planner.round(p.lon * 1e5).toLong()
                for (d in longArrayOf(lat - plat, lon - plon)) {
                    var v = if (d < 0) (d shl 1).inv() else d shl 1
                    while (v >= 0x20) {
                        out.append(((0x20 or (v and 0x1F).toInt()) + 63).toChar())
                        v = v shr 5
                    }
                    out.append((v + 63).toInt().toChar())
                }
                plat = lat
                plon = lon
            }
            return out.toString()
        }

        private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")

        /** "gb, fr" -> "GB,FR": the ISO codes place search is limited to ("" = anywhere). */
        fun countryList(raw: String): String =
            raw.replace(',', ' ').uppercase().split(' ')
                .filter { c -> c.length in 2..3 && c.all { it in 'A'..'Z' } }
                .joinToString(",")

        /** A string field, "" when absent or JSON null (Android's optString
         *  turns null into the text "null"). */
        private fun str(o: JSONObject, key: String): String =
            if (o.isNull(key)) "" else o.optString(key, "")

        fun parseGeocode(data: JSONObject, text: String, size: Int = 5): List<Place> {
            val feats = data.optJSONArray("features") ?: return emptyList()
            val out = mutableListOf<Place>()
            for (i in 0 until minOf(size, feats.length())) {
                val f = feats.optJSONObject(i) ?: continue
                val c = f.optJSONObject("geometry")?.optJSONArray("coordinates") ?: continue
                if (c.length() < 2) continue
                val label = f.optJSONObject("properties")?.let { str(it, "label") }
                    ?.ifBlank { null } ?: text
                out.add(Place(label, c.getDouble(1), c.getDouble(0)))
            }
            return out
        }

        fun parseRoute(data: JSONObject): Route {
            val feat = data.optJSONArray("features")?.optJSONObject(0)
                ?: throw RoutingException("OpenRouteService found no route")
            val coords = feat.optJSONObject("geometry")?.optJSONArray("coordinates")
            val summary = feat.optJSONObject("properties")?.optJSONObject("summary")
            if (coords == null || summary == null) {
                throw RoutingException("OpenRouteService found no route")
            }
            val pts = (0 until coords.length()).map { i ->
                val c = coords.getJSONArray(i)
                RoutePoint(c.getDouble(1), c.getDouble(0),
                    if (c.length() > 2 && !c.isNull(2)) c.getDouble(2) else null)
            }
            if (pts.size < 2) throw RoutingException("OpenRouteService returned an empty route")
            return Route(pts, summary.optDouble("distance", 0.0) / 1000.0,
                summary.optDouble("duration", 0.0))
        }

        /** Chargers this car can use: CCS, not Tesla-only, not private, not
         *  marked out of operation. */
        fun parseChargers(data: JSONArray): List<Charger> {
            val out = mutableListOf<Charger>()
            for (i in 0 until data.length()) {
                val poi = data.optJSONObject(i) ?: continue
                val operator = poi.optJSONObject("OperatorInfo")?.let { str(it, "Title") } ?: ""
                if ("tesla-only" in operator.lowercase()) continue
                if (!poi.isNull("UsageTypeID") && poi.optInt("UsageTypeID", 0) in PRIVATE_USAGE) continue
                val status = poi.optJSONObject("StatusType")
                if (status != null && !status.isNull("IsOperational") &&
                    !status.optBoolean("IsOperational", true)) continue
                val addr = poi.optJSONObject("AddressInfo") ?: continue
                if (!addr.has("Latitude") || !addr.has("Longitude")) continue
                val conns = poi.optJSONArray("Connections") ?: JSONArray()
                val powers = (0 until conns.length())
                    .mapNotNull { conns.optJSONObject(it) }
                    .filter { it.optInt("ConnectionTypeID") == CCS2 }
                    .map { c -> c.optDouble("PowerKW", 0.0).let { if (it.isNaN()) 0.0 else it } }
                if (powers.isEmpty()) continue
                val lat = addr.optDouble("Latitude")
                val lon = addr.optDouble("Longitude")
                if (lat.isNaN() || lon.isNaN()) continue
                val street = listOf(str(addr, "AddressLine1"), str(addr, "Town"))
                    .filter { it.isNotBlank() }.joinToString(", ")
                out.add(Charger(
                    name = str(addr, "Title").ifBlank { "Charger" },
                    lat = lat, lon = lon, powerKw = powers.max(),
                    operator = operator, address = street,
                    usageCost = str(poi, "UsageCost"),
                ))
            }
            return out
        }
    }
}
