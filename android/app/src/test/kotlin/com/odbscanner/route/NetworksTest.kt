package com.odbscanner.route

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The networks to pick from: counted from Open Charge Map's listings, most
 * locations first, with what is not a usable network left out; a network in
 * one list taken out of the other. Listings are synthetic; nothing is fetched.
 */
class NetworksTest {
    private val reference = JSONObject().put("Operators", JSONArray()
        .put(JSONObject().put("ID", 1).put("Title", "(Unknown Operator)"))
        .put(JSONObject().put("ID", 45).put("Title", "(Business Owner at Location)"))
        .put(JSONObject().put("ID", 3534).put("Title", "IONITY GmbH"))
        .put(JSONObject().put("ID", 3296).put("Title", "InstaVolt Ltd"))
        .put(JSONObject().put("ID", 32).put("Title", "BP Pulse (UK)"))
        .put(JSONObject().put("ID", 23).put("Title", "Tesla (Tesla-only charging)")))

    private fun poi(op: Int?, usage: Int? = 1, status: Int? = 50) = JSONObject().apply {
        put("OperatorID", op ?: JSONObject.NULL)
        put("UsageTypeID", usage ?: JSONObject.NULL)
        put("StatusTypeID", status ?: JSONObject.NULL)
    }

    @Test
    fun mostLocationsFirstAndOnlyRealUsableNetworks() {
        val gb = JSONArray().apply {
            repeat(3) { put(poi(3296)) }                    // InstaVolt x3
            repeat(2) { put(poi(3534)) }                    // IONITY x2
            put(poi(32)); put(poi(32, status = 100))        // BP x1 (one not operational)
            put(poi(1)); put(poi(45)); put(poi(null))       // no network
            put(poi(23))                                    // Tesla-only
            put(poi(3534, usage = 2))                       // private
            put(poi(3534, status = 150))                    // planned
        }
        val got = Networks.count(listOf(gb), Networks.names(reference))
        assertEquals(listOf(Network("InstaVolt Ltd", 3), Network("IONITY GmbH", 2),
            Network("BP Pulse (UK)", 1)), got)
    }

    @Test
    fun severalCountriesAddUp() {
        val gb = JSONArray().put(poi(3534))
        val fr = JSONArray().put(poi(3534)).put(poi(3296))
        val got = Networks.count(listOf(gb, fr), Networks.names(reference))
        assertEquals(Network("IONITY GmbH", 2), got.first())
    }

    @Test
    fun aNetworkCannotBeBothPreferredAndAvoided() {
        val (prefer, avoid) = Networks.choose("IONITY GmbH", "InstaVolt Ltd", "ionity gmbh, BP Pulse (UK)")
        assertEquals("InstaVolt Ltd, IONITY GmbH", prefer)
        assertEquals("BP Pulse (UK)", avoid)
        assertEquals("InstaVolt Ltd, IONITY GmbH", Networks.choose("ionity gmbh", prefer, avoid).first)
        assertEquals("IONITY GmbH", Networks.remove("instavolt ltd", prefer))
    }

    @Test
    fun theListIsKeptAMonthForTheSameCountriesAndPower() {
        val l = NetworkList(listOf(Network("IONITY GmbH", 2)), "GB", 50.0, 1_000L)
        assertEquals(l, Networks.decode(Networks.encode(l)))
        assertTrue(l.fresh(1_000L + 86_400_000L, "GB", 50.0))
        assertFalse(l.fresh(1_000L + 31 * 86_400_000L, "GB", 50.0))
        assertFalse(l.fresh(2_000L, "GB,FR", 50.0))
        assertFalse(l.fresh(2_000L, "GB", 100.0))
        assertNull(Networks.decode("{"))
    }
}
