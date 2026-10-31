package com.odbscanner.route

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** What survives the app being stopped mid-trip. */
class TripStoreTest {
    private val route = RouteState("trip-9", 3,
        listOf(Revision(3, 5_000L, "you chose Hub B instead of Hub A",
            listOf(Waypoint("ocm:202", "Hub B", 52.8, -1.4, true),
                Waypoint("ll:53.8000,-1.5500", "Leeds", 53.8, -1.55, false)),
            14.0, "with_stops", 270.0)),
        ProviderSync(RouteRevisions.GOOGLE_MAPS, 2, listOf("ocm:101", "ll:53.8000,-1.5500"), 4_000L,
            SyncStatus.STALE, "Google Maps still has revision 2"),
        Picks(setOf("101"), setOf("202")))
    private val saved = SavedTrip(Place("Leeds", 53.8, -1.55), "cheapest",
        Avoid(motorways = false, tolls = true, ferries = true), navigating = true, route = route,
        savedAtMs = 1_000_000L)

    @Test
    fun aSavedTripComesBackAsItWas() {
        assertEquals(saved, TripStore.decode(TripStore.encode(saved)))
    }

    @Test
    fun whatGoogleMapsWasGivenAndTheDriversPicksAreKept() {
        val back = TripStore.decode(TripStore.encode(saved))!!
        assertTrue(back.route.needsHandoff)                    // still out of date after restart
        assertEquals(setOf("202"), back.route.picks.useChargers)
    }

    @Test
    fun anOldTripIsOver() {
        assertTrue(TripStore.fresh(saved, saved.savedAtMs + TripStore.MAX_AGE_MS))
        assertFalse(TripStore.fresh(saved, saved.savedAtMs + TripStore.MAX_AGE_MS + 1))
        assertFalse(TripStore.fresh(saved, saved.savedAtMs - 1))   // a clock gone backwards
    }

    @Test
    fun nothingOrSomethingUnreadableIsNoTrip() {
        assertNull(TripStore.decode(null))
        assertNull(TripStore.decode("not json"))
        assertNull(TripStore.decode("{\"to\": {}}"))
    }

    @Test
    fun aRestoreSaysHowOldItsPositionIsAndWhatChanged() {
        val before = route.current
        val same = TripStore.restoreNote("Leeds", before, before.copy(number = 4), 30_000L, false)
        assertEquals("Watching your trip to Leeds again.", same)
        val moved = before.copy(number = 4, waypoints = listOf(
            Waypoint("ocm:303", "Hub C", 53.0, -1.4, true), before.waypoints.last()),
            arrivalPct = 11.0)
        val note = TripStore.restoreNote("Leeds", before, moved, 7 * 60_000L, true)
        assertTrue(note, note.startsWith("Watching your trip to Leeds again, planned from " +
            "where the phone was 7 min ago."))
        assertTrue(note, note.contains("The plan has changed since: "))
        assertTrue(note, note.contains("Hub C"))
        assertTrue(note, note.endsWith("Google Maps has an older plan: tap to send this one."))
    }

    @Test
    fun aRestoreKeepsTheStopsStillOnTheWayNotThoseBehind() {
        // Oxford to Leeds, stops near Coventry (52.4) and Sheffield (53.38).
        val rev = Revision(2, 0L, "planned", listOf(
            Waypoint("ocm:11", "Coventry", 52.40, -1.50, true),
            Waypoint("ocm:22", "Sheffield", 53.38, -1.47, true),
            Waypoint("ll:53.8000,-1.5500", "Leeds", 53.80, -1.55, false)), 12.0, "with_stops", 270.0)
        assertEquals(listOf("11", "22"), TripStore.stopsToKeep(rev, 51.75, -1.26))   // at Oxford
        assertEquals(listOf("22"), TripStore.stopsToKeep(rev, 52.90, -1.48))       // past Coventry
        assertEquals(emptyList<String>(), TripStore.stopsToKeep(rev, 53.38, -1.47)) // at Sheffield
    }
}
