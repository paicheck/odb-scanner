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
}
