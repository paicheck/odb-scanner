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

    @Test
    fun googleTripLinkHasStopsThenDestination() {
        val url = googleMapsTripUrl(51.75 to -1.26, listOf(52.1 to -1.5, 54.0 to -2.75),
            55.95 to -3.19)
        assertEquals("https://www.google.com/maps/dir/?api=1&travelmode=driving" +
            "&origin=51.750000,-1.260000&destination=55.950000,-3.190000" +
            "&waypoints=52.100000,-1.500000%7C54.000000,-2.750000", url)
        assertTrue(!googleMapsTripUrl(0.0 to 0.0, emptyList(), 1.0 to 2.0).contains("waypoints"))
        val many = googleMapsTripUrl(0.0 to 0.0, (1..12).map { it.toDouble() to 0.0 }, 0.0 to 0.0)
        assertEquals(MAX_LINK_STOPS, many.substringAfter("waypoints=").split("%7C").size)
    }
}
