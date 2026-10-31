package com.odbscanner.route

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The trip's one route state: revisions, stale and duplicate guards, proposals, sync. */
class RouteStateTest {
    private val home = Place("Start", 51.75, -1.26)
    private val dest = Place("Leeds", 53.80, -1.55)

    private fun charger(id: String, name: String, lat: Double) =
        Charger(name, lat, -1.4, 150.0, "Op", id = id)

    private fun stop(c: Charger, arrive: Double) = Stop(km = 100.0, charger = c, arrivePct = arrive,
        departPct = 80.0, chargeMin = 20, kwh = 30.0, pricePerKwh = 0.6, priceSource = "", cost = 18.0,
        detourKm = 0.5)

    private fun trip(stops: List<Stop>, arrival: Double?, status: String = "with_stops") = TripResult(
        origin = home, destination = dest,
        plan = Plan(status, 270.0, 10800.0, 90.0, arrival, stops, emptyList(), null, 1200.0, 18.0,
            "balanced", emptyList()),
        car = CarEnergy(53.2, "test", 90.0, null, "test"), consumptionKwhPer100km = 17.0,
        line = emptyList())

    private val hubA = charger("101", "Hub A", 52.6)
    private val hubB = charger("202", "Hub B", 52.8)
    private val planA = trip(listOf(stop(hubA, 15.0)), 6.0)
    private val planB = trip(listOf(stop(hubB, 18.0)), 14.0)

    private fun started(provider: String = RouteRevisions.GOOGLE_MAPS) =
        RouteRevisions.start("trip-1", planA, provider, 1_000L)

    @Test
    fun aNewTripIsRevisionOneAndGoogleMapsHasNothingYet() {
        val s = started()
        assertEquals(1, s.revision)
        assertEquals(listOf("ocm:101", "ll:53.8000,-1.5500"), s.current.keys)
        assertEquals(SyncStatus.NOT_SENT, s.sync.status)
        assertTrue(s.needsHandoff)
        assertFalse(started(RouteRevisions.IN_APP).needsHandoff)   // in-app needs no hand-off
    }

    @Test
    fun sendingMakesItCurrent() {
        val s = RouteRevisions.markSent(started(), 2_000L)
        assertEquals(SyncStatus.CURRENT, s.sync.status)
        assertEquals(1, s.sync.sentRevision)
        assertFalse(s.needsHandoff)
    }

    @Test
    fun newFiguresForTheSameStopsAreNotANewRevision() {
        val sent = RouteRevisions.markSent(started(), 2_000L)
        val out = RouteRevisions.revise(sent, 1, trip(listOf(stop(hubA, 12.0)), 4.0), "drift", 3_000L)
        out as RouteRevisions.Outcome.Same
        assertEquals(1, out.state.revision)
        assertEquals(4.0, out.state.current.arrivalPct!!, 1e-9)
        assertFalse(out.state.needsHandoff)                      // no duplicate hand-off
    }

    @Test
    fun aDifferentStopIsANewRevisionAndGoogleMapsIsStale() {
        val sent = RouteRevisions.markSent(started(), 2_000L)
        val out = RouteRevisions.revise(sent, 1, planB, "using more than planned", 3_000L)
        out as RouteRevisions.Outcome.Revised
        assertEquals(2, out.state.revision)
        assertEquals("using more than planned", out.state.current.reason)
        assertEquals(SyncStatus.STALE, out.state.sync.status)
        assertTrue(out.state.needsHandoff)
        assertEquals("Charging at Hub B instead of Hub A; arrive with 14 % (was 6 %).", out.change)
    }

    @Test
    fun aReplanWorkedOutAgainstAnOlderRevisionIsRefused() {
        val two = (RouteRevisions.revise(started(), 1, planB, "x", 3_000L)
            as RouteRevisions.Outcome.Revised).state
        val late = RouteRevisions.revise(two, 1, planA, "slow re-plan", 4_000L)
        late as RouteRevisions.Outcome.Stale
        assertEquals(two, late.state)
    }

    @Test
    fun goingBackToTheStopsGoogleMapsHasIsCurrentAgain() {
        val sent = RouteRevisions.markSent(started(), 2_000L)
        val two = (RouteRevisions.revise(sent, 1, planB, "x", 3_000L) as RouteRevisions.Outcome.Revised).state
        val three = (RouteRevisions.revise(two, 2, planA, "y", 4_000L) as RouteRevisions.Outcome.Revised).state
        assertEquals(3, three.revision)
        assertEquals(SyncStatus.CURRENT, three.sync.status)
        assertFalse(three.needsHandoff)
    }

    @Test
    fun aProposalWaitsAndATurnedDownOneIsNotOfferedAgain() {
        val s = started()
        val p = RouteRevisions.propose(s, 1, planB, "using more than planned", 3_000L, urgent = false)
        assertNotNull(p)
        assertEquals(1, p!!.revision)                            // nothing applied yet
        assertTrue(p.proposal!!.change.startsWith("Charging at Hub B"))
        val no = RouteRevisions.decline(p)
        assertNull(no.proposal)
        assertNull(RouteRevisions.propose(no, 1, planB, "again", 4_000L, urgent = false))
        assertNotNull(RouteRevisions.propose(no, 1, planB, "critical", 5_000L, urgent = true))
        assertNull(RouteRevisions.propose(s, 1, planA, "same", 3_000L, urgent = false))   // no change
        assertNull(RouteRevisions.propose(s, 0, planB, "stale", 3_000L, urgent = false))  // stale
    }

    @Test
    fun anUnreachablePlanIsAChangeEvenWithTheSameStops() {
        val s = started()
        val bad = trip(listOf(stop(hubA, 15.0)), null, status = "unreachable")
        assertTrue(RouteRevisions.changesRoute(s, bad))
        val out = RouteRevisions.revise(s, 1, bad, "charge too low", 2_000L) as RouteRevisions.Outcome.Revised
        assertTrue(out.change, out.change.contains("out of reach"))
    }

    @Test
    fun historyIsBounded() {
        var s = started()
        repeat(30) { i ->
            val p = if (i % 2 == 0) planB else planA
            s = (RouteRevisions.revise(s, s.revision, p, "r$i", 10_000L + i)
                as RouteRevisions.Outcome.Revised).state
        }
        assertEquals(31, s.revision)
        assertEquals(RouteRevisions.HISTORY, s.history.size)
        assertEquals(31, s.current.number)
    }

    @Test
    fun theStateSurvivesSavingAndRestoring() {
        val sent = RouteRevisions.markSent(started(), 2_000L)
        val two = (RouteRevisions.revise(sent.copy(picks = Picks(setOf("ocm:9"), setOf("202"))),
            1, planB, "x", 3_000L) as RouteRevisions.Outcome.Revised).state
        val declined = RouteRevisions.decline(
            RouteRevisions.propose(two, 2, planA, "y", 4_000L, false)!!)
        val back = RouteRevisions.fromJson(org.json.JSONObject(RouteRevisions.toJson(declined).toString()))
        assertEquals(declined, back)
    }

    @Test
    fun aStopWithoutAnIdIsKnownByWhereItIs() {
        assertEquals("ocm:5", RouteRevisions.key("5", 1.0, 2.0))
        assertEquals("ll:51.1235,-0.1000", RouteRevisions.key("", 51.12346, -0.1))
    }

    @Test
    fun whatTheMonitorDoesWithARePlan() {
        val s = started()
        val sameStops = trip(listOf(stop(hubA, 10.0)), 3.0)
        assertEquals(RouteRevisions.Action.REFRESH, RouteRevisions.decide(s, 1, sameStops, false, false))
        assertEquals(RouteRevisions.Action.PROPOSE, RouteRevisions.decide(s, 1, planB, false, false))
        assertEquals(RouteRevisions.Action.APPLY, RouteRevisions.decide(s, 1, planB, true, false))
        assertEquals(RouteRevisions.Action.KEEP, RouteRevisions.decide(s, 0, planB, true, true))   // stale
        val declined = RouteRevisions.decline(RouteRevisions.propose(s, 1, planB, "x", 2_000L, false)!!)
        assertEquals(RouteRevisions.Action.KEEP, RouteRevisions.decide(declined, 1, planB, false, false))
        assertEquals(RouteRevisions.Action.PROPOSE, RouteRevisions.decide(declined, 1, planB, false, true))
        assertEquals(RouteRevisions.Action.APPLY, RouteRevisions.decide(declined, 1, planB, true, false))
    }

    @Test
    fun choosingAnotherChargerKeepsOffTheOneItReplaces() {
        val p = Picks().avoid("101")
        assertEquals(setOf("101"), p.avoidChargers)
        val swapped = Picks(useChargers = setOf("101")).insteadOf("101", "303")
        assertEquals(setOf("303"), swapped.useChargers)
        assertEquals(setOf("101"), swapped.avoidChargers)
        // Changing your mind: a charger avoided earlier can be chosen again.
        assertEquals(emptySet<String>(), p.insteadOf("", "101").avoidChargers)
        assertEquals(setOf("101"), p.insteadOf("", "101").useChargers)
    }
}

