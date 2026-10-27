package com.odbscanner.route

import java.util.Locale
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.ln
import kotlin.math.tan

/**
 * Web-Mercator arithmetic for the trip map (OpenStreetMap tiles): which zoom
 * fits the whole trip in the box, which tiles cover it, and where a point
 * lands on screen. Plain JVM, so it is unit-tested.
 */
object MapMath {
    const val MAX_ZOOM = 17

    /** Tile-space x of a longitude at zoom [z]: tile index plus fraction. */
    fun worldX(lon: Double, z: Int): Double = (lon + 180.0) / 360.0 * (1 shl z)

    fun worldY(lat: Double, z: Int): Double {
        val r = Math.toRadians(lat.coerceIn(-85.0511, 85.0511))
        return (1.0 - ln(tan(r) + 1.0 / cos(r)) / PI) / 2.0 * (1 shl z)
    }

    /** One tile; [x] may lie outside 0 until 2^z (wrapped only when fetched). */
    data class Tile(val z: Int, val x: Int, val y: Int) {
        val path: String get() = "$z/${Math.floorMod(x, 1 shl z)}/$y"
    }

    data class View(
        val zoom: Int,
        val cx: Double,            // tile-space centre
        val cy: Double,
        val widthPx: Float,
        val heightPx: Float,
        val tilePx: Float,         // drawn size of one tile
    ) {
        fun x(lon: Double) = ((worldX(lon, zoom) - cx) * tilePx + widthPx / 2).toFloat()
        fun y(lat: Double) = ((worldY(lat, zoom) - cy) * tilePx + heightPx / 2).toFloat()

        fun left(t: Tile) = ((t.x - cx) * tilePx + widthPx / 2).toFloat()
        fun top(t: Tile) = ((t.y - cy) * tilePx + heightPx / 2).toFloat()

        /** The tiles under the box, nearest the centre first. */
        fun tiles(): List<Tile> {
            val halfW = widthPx / 2 / tilePx
            val halfH = heightPx / 2 / tilePx
            val ys = floor(cy - halfH).toInt().coerceAtLeast(0)..
                floor(cy + halfH).toInt().coerceAtMost((1 shl zoom) - 1)
            val xs = floor(cx - halfW).toInt()..floor(cx + halfW).toInt()
            return ys.flatMap { y -> xs.map { x -> Tile(zoom, x, y) } }
                .sortedBy { (it.x + 0.5 - cx) * (it.x + 0.5 - cx) + (it.y + 0.5 - cy) * (it.y + 0.5 - cy) }
        }
    }

    /** The highest zoom at which every (lat, lon) fits inside the box less [padPx]. */
    fun fit(points: List<Pair<Double, Double>>, widthPx: Float, heightPx: Float,
            tilePx: Float, padPx: Float): View {
        val xs = points.map { worldX(it.second, 0) }
        val ys = points.map { worldY(it.first, 0) }
        val (x0, x1) = (xs.minOrNull() ?: 0.5) to (xs.maxOrNull() ?: 0.5)
        val (y0, y1) = (ys.minOrNull() ?: 0.5) to (ys.maxOrNull() ?: 0.5)
        val zoom = (MAX_ZOOM downTo 1).firstOrNull { z ->
            val s = (1 shl z) * tilePx
            (x1 - x0) * s <= widthPx - 2 * padPx && (y1 - y0) * s <= heightPx - 2 * padPx
        } ?: 1
        val n = (1 shl zoom).toDouble()
        return View(zoom, (x0 + x1) / 2 * n, (y0 + y1) / 2 * n, widthPx, heightPx, tilePx)
    }

    /** Every [n]th point and the last, so a long route draws quickly. */
    fun thinLine(points: List<RoutePoint>, max: Int = 500): List<Pair<Double, Double>> {
        val step = maxOf(1, (points.size + max - 1) / max)
        return points.filterIndexed { i, _ -> i % step == 0 || i == points.lastIndex }
            .map { it.lat to it.lon }
    }
}

/**
 * Google Maps directions for the whole trip: from [origin], each charger a
 * stop, then the destination. The origin must be given: without it the Maps
 * app takes the first stop as the destination and drops the rest. Google
 * Maps takes at most 9 stops from a link.
 */
fun googleMapsTripUrl(origin: Pair<Double, Double>, stops: List<Pair<Double, Double>>,
                      destination: Pair<Double, Double>): String {
    fun ll(p: Pair<Double, Double>) = String.format(Locale.ROOT, "%.6f,%.6f", p.first, p.second)
    val url = StringBuilder("https://www.google.com/maps/dir/?api=1&travelmode=driving")
        .append("&origin=").append(ll(origin))
        .append("&destination=").append(ll(destination))
    if (stops.isNotEmpty()) {
        url.append("&waypoints=").append(stops.take(MAX_LINK_STOPS).joinToString("%7C") { ll(it) })
    }
    return url.toString()
}

const val MAX_LINK_STOPS = 9
