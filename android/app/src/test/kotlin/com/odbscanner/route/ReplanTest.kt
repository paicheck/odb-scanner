package com.odbscanner.route

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ReplanTest {
    // A road due north, one point per km; the plan stops at 100 km (20 -> 70 %).
    private val line = (0..200).map { (50.0 + it / 111.195) to 0.0 }
    private val profile = listOf(0.0 to 80.0, 100.0 to 20.0, 100.0 to 70.0, 200.0 to 15.0)

    @Test
    fun progressFindsTheNearestPointAndHowFarOff() {
        val p = Replan.progress(line, 200.0, 50.0 + 60.2 / 111.195, 0.0)
        assertEquals(60.0, p.kmAlong, 0.6)
        assertTrue(p.offRouteKm < 0.5)
        val off = Replan.progress(line, 200.0, 50.0 + 60 / 111.195, 0.1)     // ~7 km east
        assertTrue(off.offRouteKm > Replan.OFF_ROUTE_KM)
        // A thinned line is scaled to the plan's distance.
        assertEquals(120.0, Replan.progress(line, 400.0, 50.0 + 60 / 111.195, 0.0).kmAlong, 0.6)
    }

    @Test
    fun expectedChargeFollowsTheProfileAndItsSteps() {
        assertEquals(80.0, Replan.expectedPct(profile, 0.0)!!, 1e-9)
        assertEquals(50.0, Replan.expectedPct(profile, 50.0)!!, 1e-9)
        assertEquals(70.0, Replan.expectedPct(profile, 100.0)!!, 1e-9)    // stop: left
        assertEquals(42.5, Replan.expectedPct(profile, 150.0)!!, 1e-9)
        assertEquals(15.0, Replan.expectedPct(profile, 250.0)!!, 1e-9)
        assertNull(Replan.expectedPct(emptyList(), 10.0))
    }

    @Test
    fun reasonsToReplan() {
        assertNull(Replan.reason(50.0, 48.0, 1.0))                 // within the drift
        assertTrue(Replan.reason(50.0, 46.0, 1.0)!!.contains("expected 50 %"))
        assertTrue(Replan.reason(50.0, 56.0, 1.0)!!.contains("more than"))
        assertTrue(Replan.reason(50.0, 50.0, 6.0)!!.contains("off the planned route"))
    }

    @Test
    fun spokenAlertsReadNaturally() {
        assertEquals("In under a mile", Replan.milesAhead(1.2))
        assertEquals("In about 1 mile", Replan.milesAhead(1.7))
        assertEquals("In about 5 miles", Replan.milesAhead(8.0))
        val stop = Stop(280.0, Charger("Moto Lancaster Services", 54.0, -2.7, 150.0),
            18.2, 75.0, 29, 30.2, 0.79, "Open Charge Map", 25.93, 0.4)
        assertEquals("In about 4 miles, charging stop 1: Moto Lancaster Services, on the M6. " +
            "Charge from 18 to 75 percent, about 29 minutes, £25.93.",
            Replan.stopAheadSpeech(0, stop, 6.5, "M6"))
        assertEquals("Charge is 41 percent, 6 below plan. The plan still works: you arrive " +
            "with 12 percent.", Replan.stillOkSpeech(41.0, 47.0, 12.0))
    }

    @Test
    fun atAPlannedStopDriftIsIgnored() {
        assertEquals(0, Replan.atStop(listOf(100.0, 300.0), 98.5))
        assertEquals(1, Replan.atStop(listOf(100.0, 300.0), 302.0))
        assertNull(Replan.atStop(listOf(100.0, 300.0), 150.0))
        assertNull(Replan.atStop(emptyList(), 0.0))
    }

    @Test
    fun consumptionIsCorrectedOnlyWhenThereIsSomethingToGoOn() {
        // Planned 80 -> 50 % by here, really 80 -> 44 %: 20 % more than planned.
        assertEquals(20.4, Replan.correctedConsumption(17.0, 80.0, 50.0, 44.0,
            chargedSincePlan = false, kmAlong = 50.0, firstStopKm = 100.0)!!, 1e-9)
        // Clamped to +50 %.
        assertEquals(25.5, Replan.correctedConsumption(17.0, 80.0, 70.0, 40.0,
            false, 20.0, 100.0)!!, 1e-9)
        assertNull(Replan.correctedConsumption(17.0, 80.0, 50.0, 44.0, true, 50.0, 100.0))
        assertNull(Replan.correctedConsumption(17.0, 80.0, 50.0, 44.0, false, 120.0, 100.0))
        assertNull(Replan.correctedConsumption(17.0, 80.0, 79.0, 78.0, false, 2.0, 100.0))
    }
}
