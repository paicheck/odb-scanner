package com.odbscanner.route

import java.util.Locale
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.ln
import kotlin.math.log2
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.tan

/**
 * Web-Mercator arithmetic for the trip map (OpenStreetMap tiles): which zoom
 * fits the whole trip in the box, which tiles cover it, and where a point
 * lands on screen. Plain JVM, so it is unit-tested.
 */
object MapMath {
    const val MAX_ZOOM = 17
    const val MIN_VIEW_ZOOM = 3.0
    const val MAX_VIEW_ZOOM = 18.5     // past MAX_ZOOM, zoom-17 tiles are drawn larger

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

    /**
     * Where a map looks and how close, for one the driver can pinch and drag:
     * a centre in world units (0 to 1 across the world at zoom 0) and a
     * continuous zoom. Tiles are fetched at the whole zoom below and drawn
     * scaled, so zooming is smooth.
     */
    data class Camera(val x: Double, val y: Double, val zoom: Double) {
        /** Pixels per world unit, with [baseTilePx] the drawn size of a tile at a whole zoom. */
        private fun scale(baseTilePx: Float) = baseTilePx * 2.0.pow(zoom)

        fun view(widthPx: Float, heightPx: Float, baseTilePx: Float): View {
            val z = floor(zoom).toInt().coerceIn(1, MAX_ZOOM)
            val n = (1 shl z).toDouble()
            return View(z, x * n, y * n, widthPx, heightPx,
                (baseTilePx * 2.0.pow(zoom - z)).toFloat())
        }

        /** Moved with a finger that moved [dx], [dy] pixels: the map follows it. */
        fun pan(dx: Float, dy: Float, baseTilePx: Float): Camera {
            val s = scale(baseTilePx)
            return copy(x = x - dx / s, y = (y - dy / s).coerceIn(0.0, 1.0))
        }

        /** Zoomed by [factor] about the screen point ([px], [py]), which stays where it is. */
        fun zoomAbout(factor: Float, px: Float, py: Float, widthPx: Float, heightPx: Float,
                      baseTilePx: Float, minZoom: Double = MIN_VIEW_ZOOM,
                      maxZoom: Double = MAX_VIEW_ZOOM): Camera {
            if (factor <= 0f) return this
            val z = (zoom + log2(factor.toDouble())).coerceIn(minZoom, maxZoom)
            val before = scale(baseTilePx)
            val after = baseTilePx * 2.0.pow(z)
            val ox = (px - widthPx / 2) / before
            val oy = (py - heightPx / 2) / before
            val rx = (px - widthPx / 2) / after
            val ry = (py - heightPx / 2) / after
            return Camera(x + ox - rx, (y + oy - ry).coerceIn(0.0, 1.0), z)
        }

        companion object {
            /** Looking at one place, [zoom] close. */
            fun at(lat: Double, lon: Double, zoom: Double) = Camera(worldX(lon, 0), worldY(lat, 0), zoom)
        }
    }

    /** The closest camera at which every (lat, lon) fits inside the box less [padPx]. */
    fun fitCamera(points: List<Pair<Double, Double>>, widthPx: Float, heightPx: Float,
                  baseTilePx: Float, padPx: Float): Camera {
        if (points.isEmpty()) return Camera(0.5, 0.5, MIN_VIEW_ZOOM)
        val xs = points.map { worldX(it.second, 0) }
        val ys = points.map { worldY(it.first, 0) }
        val dx = xs.max() - xs.min()
        val dy = ys.max() - ys.min()
        val room = min(
            if (dx > 0) (widthPx - 2 * padPx) / (dx * baseTilePx) else Double.MAX_VALUE,
            if (dy > 0) (heightPx - 2 * padPx) / (dy * baseTilePx) else Double.MAX_VALUE)
        val zoom = if (room == Double.MAX_VALUE) MAX_ZOOM - 2.0
        else log2(room).coerceIn(MIN_VIEW_ZOOM, MAX_ZOOM.toDouble())
        return Camera((xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2, zoom)
    }

    /** Every [n]th point and the last, so a long route draws quickly. */
    fun thinLine(points: List<RoutePoint>, max: Int = 500): List<Pair<Double, Double>> {
        val step = maxOf(1, (points.size + max - 1) / max)
        return points.filterIndexed { i, _ -> i % step == 0 || i == points.lastIndex }
            .map { it.lat to it.lon }
    }
}
