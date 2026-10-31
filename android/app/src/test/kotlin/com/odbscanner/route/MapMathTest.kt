package com.odbscanner.route

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MapMathTest {

    @Test
    fun knownTileCoordinates() {
        // Greenwich on the equator is the middle of the world at every zoom.
        assertEquals(1.0, MapMath.worldX(0.0, 1), 1e-9)
        assertEquals(1.0, MapMath.worldY(0.0, 1), 1e-9)
        // London (51.5074, -0.1278) is tile 4093/2724 at zoom 13 (slippy-map
        // formula with asinh, computed separately).
        assertEquals(4093, MapMath.worldX(-0.1278, 13).toInt())
        assertEquals(2724, MapMath.worldY(51.5074, 13).toInt())
    }

    @Test
    fun fitKeepsEveryPointInsideThePaddedBox() {
        val trip = listOf(51.75 to -1.26, 53.48 to -2.24, 55.95 to -3.19)   // Oxford → Edinburgh
        val v = MapMath.fit(trip, 1000f, 700f, 256f, 40f)
        trip.forEach { (lat, lon) ->
            assertTrue(v.x(lon) in 40f..960f && v.y(lat) in 40f..660f)
        }
        // One zoom closer no longer fits.
        val closer = v.copy(zoom = v.zoom + 1, cx = v.cx * 2, cy = v.cy * 2)
        assertTrue(trip.any { (lat, lon) -> closer.x(lon) !in 40f..960f || closer.y(lat) !in 40f..660f })
    }

    @Test
    fun tilesCoverTheBoxAndWrapWhenFetched() {
        val v = MapMath.View(zoom = 2, cx = 0.2, cy = 2.0, widthPx = 512f, heightPx = 256f, tilePx = 256f)
        val tiles = v.tiles()
        assertEquals(setOf(-1, 0, 1), tiles.map { it.x }.toSet())
        assertEquals(setOf(1, 2), tiles.map { it.y }.toSet())
        assertEquals("2/3/1", tiles.first { it.x == -1 && it.y == 1 }.path)
        assertTrue(tiles.all { v.left(it) < 512f && v.left(it) + 256f > 0f })
    }

    @Test
    fun thinLineKeepsEndsAndCap() {
        val pts = (0 until 1234).map { RoutePoint(it.toDouble(), 0.0) }
        val thin = MapMath.thinLine(pts, 500)
        assertTrue(thin.size <= 501)
        assertEquals(0.0, thin.first().first, 0.0)
        assertEquals(1233.0, thin.last().first, 0.0)
    }

    // -- the camera a driver pinches and drags -----------------------------------

    private val trip = listOf(51.75 to -1.26, 53.48 to -2.24, 55.95 to -3.19)   // Oxford → Edinburgh

    @Test
    fun theFittedCameraShowsTheWholeTripAndNoMore() {
        val cam = MapMath.fitCamera(trip, 1000f, 700f, 256f, 40f)
        val v = cam.view(1000f, 700f, 256f)
        trip.forEach { (lat, lon) ->
            assertTrue(v.x(lon) in 39f..961f && v.y(lat) in 39f..661f)
        }
        // A touch closer and a point falls outside: it is the closest fit.
        val closer = cam.copy(zoom = cam.zoom + 0.1).view(1000f, 700f, 256f)
        assertTrue(trip.any { (lat, lon) -> closer.x(lon) !in 40f..960f || closer.y(lat) !in 40f..660f })
    }

    @Test
    fun zoomingKeepsThePlaceUnderTheFingers() {
        val cam = MapMath.fitCamera(trip, 1000f, 700f, 256f, 40f)
        val (lat, lon) = trip[1]
        val v = cam.view(1000f, 700f, 256f)
        val px = v.x(lon)
        val py = v.y(lat)
        for (factor in listOf(2f, 0.5f, 1.37f)) {
            val after = cam.zoomAbout(factor, px, py, 1000f, 700f, 256f).view(1000f, 700f, 256f)
            assertEquals(px, after.x(lon), 0.5f)
            assertEquals(py, after.y(lat), 0.5f)
        }
        val doubled = cam.zoomAbout(2f, px, py, 1000f, 700f, 256f)
        assertEquals(cam.zoom + 1.0, doubled.zoom, 1e-9)
    }

    @Test
    fun theMapFollowsADrag() {
        val cam = MapMath.Camera.at(52.0, -1.0, 12.4)
        val (lat, lon) = 52.0 to -1.0
        val before = cam.view(800f, 600f, 256f)
        val after = cam.pan(120f, -45f, 256f).view(800f, 600f, 256f)
        assertEquals(before.x(lon) + 120f, after.x(lon), 0.5f)
        assertEquals(before.y(lat) - 45f, after.y(lat), 0.5f)
    }

    @Test
    fun zoomStaysWithinWhatTheTilesCanShow() {
        val cam = MapMath.Camera.at(52.0, -1.0, 18.0)
        assertEquals(MapMath.MAX_VIEW_ZOOM, cam.zoomAbout(8f, 0f, 0f, 800f, 600f, 256f).zoom, 1e-9)
        assertEquals(MapMath.MIN_VIEW_ZOOM, cam.zoomAbout(1e-6f, 0f, 0f, 800f, 600f, 256f).zoom, 1e-9)
        // Fractional zoom: tiles one whole zoom below, drawn larger.
        val v = MapMath.Camera.at(52.0, -1.0, 13.5).view(800f, 600f, 256f)
        assertEquals(13, v.zoom)
        assertEquals(256f * Math.sqrt(2.0).toFloat(), v.tilePx, 0.01f)
        assertEquals(17, MapMath.Camera.at(52.0, -1.0, 18.5).view(800f, 600f, 256f).zoom)
    }

    @Test
    fun oneStopAloneIsShownCloseNotAtTheWholeWorld() {
        val cam = MapMath.fitCamera(listOf(52.0 to -1.0), 800f, 600f, 256f, 20f)
        assertEquals(MapMath.MAX_ZOOM - 2.0, cam.zoom, 1e-9)
    }
}
