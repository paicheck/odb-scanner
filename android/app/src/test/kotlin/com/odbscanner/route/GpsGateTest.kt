package com.odbscanner.route

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Which GPS fixes may move the guidance (GpsGate): late, vague and jumping
 * fixes, fed in as a car would get them. Times are passed in, so each case
 * is exact.
 */
class GpsGateTest {
    private fun north(km: Double) = 51.0 + km / 111.195      // along a road due north

    /** A drive at 100 km/h: a fix a second, ~28 m apart. */
    private fun drive(g: GpsGate, fromS: Int, toS: Int, startKm: Double = 0.0,
                      accuracy: Float? = 5f): List<GpsGate.Verdict> =
        (fromS until toS).map { s ->
            val at = s * 1000L
            g.judge(north(startKm + s * 100.0 / 3600.0), -1.0, at, accuracy, at + 200)
        }

    @Test
    fun goodFixesAreUsedAndRoughOrVagueOnesAreSaidSo() {
        val g = GpsGate()
        assertEquals(List(10) { GpsGate.Verdict.GOOD }, drive(g, 0, 10))
        assertEquals(GpsGate.Verdict.ROUGH, g.judge(north(0.3), -1.0, 11_000, 75f, 11_100))
        assertEquals(GpsGate.Verdict.VAGUE, g.judge(north(0.33), -1.0, 12_000, 150f, 12_100))
        assertEquals(GpsGate.Verdict.GOOD, g.judge(north(0.36), -1.0, 13_000, null, 13_100))
    }

    @Test
    fun aFixThatArrivesLateOrOutOfOrderIsStale() {
        val g = GpsGate()
        drive(g, 0, 5)
        // Measured 15 s before it arrived: a cached or replayed fix.
        assertEquals(GpsGate.Verdict.STALE, g.judge(north(0.2), -1.0, 5_000, 5f, 20_000))
        // Measured before the last good one: never moves the car backwards.
        assertEquals(GpsGate.Verdict.STALE, g.judge(north(0.05), -1.0, 3_500, 5f, 6_000))
    }

    @Test
    fun oneJumpIsHeldAndTheDriveCarriesOn() {
        val g = GpsGate()
        drive(g, 0, 10)
        // A multipath fix 3 km away a second later: 10,800 km/h.
        assertEquals(GpsGate.Verdict.JUMP, g.judge(north(3.3), -1.0, 10_000, 8f, 10_100))
        // The next fixes carry on along the road: the jump is forgotten.
        assertEquals(List(5) { GpsGate.Verdict.GOOD }, drive(g, 11, 16))
    }

    @Test
    fun aJumpTheNextFixAgreesWithIsWhereTheCarIs() {
        val g = GpsGate()
        drive(g, 0, 10)
        assertEquals(GpsGate.Verdict.JUMP, g.judge(north(5.0), -1.0, 10_000, 8f, 10_100))
        assertEquals(GpsGate.Verdict.GOOD, g.judge(north(5.03), -1.0, 11_000, 8f, 11_100))
        assertEquals(GpsGate.Verdict.GOOD, g.judge(north(5.06), -1.0, 12_000, 8f, 12_100))
    }

    @Test
    fun farAfterATunnelIsPossibleNotAJump() {
        val g = GpsGate()
        drive(g, 0, 10)
        // Three minutes without a fix, then 5 km on: 100 km/h.
        assertEquals(GpsGate.Verdict.GOOD, g.judge(north(5.3), -1.0, 190_000, 10f, 190_100))
    }

    @Test
    fun roughFixesCannotTakeTheCarOffTheRoute() {
        // The guidance counts three fixes 140 m off as "off the route"; three
        // rough ones (they may be the road) do not count, three good ones do.
        val road = -1.0
        val pts = (0..100).map { k -> RoutePoint(north(k * 0.1), road) }
        val g = Guidance(NavRoute(pts, emptyList(), 600.0, null, emptyList(), listOf(10.0),
            "test road", "test"))
        g.update(north(1.0), road)
        val off = road + 0.002
        repeat(3) { g.update(north(1.1 + it * 0.1), off, countOffRoute = false) }
        assertEquals(false, g.update(north(1.5), off, countOffRoute = false).offRoute)
        repeat(2) { g.update(north(1.6 + it * 0.1), off) }
        assertEquals(true, g.update(north(1.9), off).offRoute)
    }
}
