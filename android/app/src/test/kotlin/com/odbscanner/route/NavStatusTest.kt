package com.odbscanner.route

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZonedDateTime

class NavStatusTest {
    @Test
    fun theDriverIsToldWhenThePositionCannotBeTrusted() {
        assertNull(NavStatus.gps(100_000L, 8f, 103_000L))
        assertEquals("GPS signal lost: the position is from 15 s ago",
            NavStatus.gps(100_000L, 8f, 115_000L))
        assertEquals("GPS weak (±120 m)", NavStatus.gps(100_000L, 120f, 101_000L))
        assertEquals("Waiting for GPS", NavStatus.gps(0L, null, 5_000L))
    }

    private fun at(iso: String) = ZonedDateTime.parse(iso).toInstant().toEpochMilli()

    @Test
    fun dayAndNightFollowTheSunWhereTheCarIs() {
        // Oxford: noon in June is day, midnight is night; so is 17:30 in late
        // December (sunset ~16:00) but not 17:30 in June.
        assertFalse(NavStatus.isNight(51.75, -1.26, at("2026-06-21T12:00:00Z")))
        assertTrue(NavStatus.isNight(51.75, -1.26, at("2026-06-21T23:30:00Z")))
        assertTrue(NavStatus.isNight(51.75, -1.26, at("2026-12-21T17:30:00Z")))
        assertFalse(NavStatus.isNight(51.75, -1.26, at("2026-06-21T17:30:00Z")))
        // Solar noon at the equivalent latitude: the sun near 90 - 51.75 + 23.44.
        val elev = NavStatus.sunElevationDeg(51.75, -1.26, at("2026-06-21T12:05:00Z"))
        assertEquals(61.7, elev, 1.0)
    }

    @Test
    fun progressNeverGoesNegative() {
        assertEquals(212.0 to 150.0, NavStatus.progress(362.0, 150.0))
        assertEquals(0.0 to 400.0, NavStatus.progress(362.0, 400.0))
    }
}
