package com.odbscanner.route

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

/** A charging network and how many fast CCS charging locations it has. */
data class Network(val name: String, val locations: Int)

/** The networks, most locations first, and where the count came from. */
data class NetworkList(
    val networks: List<Network>,
    val countries: String,         // "GB", "GB,FR"
    val minKw: Double,
    val fetchedAtMs: Long,
) {
    fun fresh(nowMs: Long, countries: String, minKw: Double): Boolean =
        this.countries == countries && this.minKw == minKw && nowMs - fetchedAtMs in 0..MAX_AGE_MS

    companion object {
        const val MAX_AGE_MS = 30 * 86_400_000L
    }
}

/**
 * The charging networks to choose from for Prefer and Never use (Route
 * settings), ordered by how many fast CCS charging locations each has in
 * your countries -- counted from Open Charge Map's own listings, with the
 * same filters the planner uses (CCS, at least the minimum power; not
 * private, not out of operation, not Tesla-only). Not a ranking from
 * anywhere else: only what Open Charge Map lists.
 *
 * Fetched once and kept for 30 days (two requests per country: the
 * locations, compact, and the operators' names); [load] with refresh asks
 * again. The names are Open Charge Map's, which the planner matches on.
 */
object Networks {
    private const val FILE = "networks"
    private const val KEY = "list"

    // Open Charge Map: OperatorID 1 is "(Unknown Operator)", 45 "(Business
    // Owner at Location)" -- not networks anyone can choose.
    private val NOT_A_NETWORK = setOf(1, 45)
    // StatusTypeID: not operational, planned, removed (decommissioned, duplicate).
    private val NOT_IN_USE = setOf(100, 150, 200, 210)

    /** The operators' names by id, from Open Charge Map's reference data. */
    fun names(reference: JSONObject): Map<Int, String> {
        val ops = reference.optJSONArray("Operators") ?: return emptyMap()
        return (0 until ops.length()).mapNotNull { ops.optJSONObject(it) }
            .mapNotNull { o -> o.optInt("ID", -1).takeIf { it > 0 }?.let { it to o.optString("Title").trim() } }
            .filter { it.second.isNotBlank() }.toMap()
    }

    /** Count compact locations by network: most first, then by name. Pure. */
    fun count(locations: List<JSONArray>, names: Map<Int, String>): List<Network> {
        val counts = mutableMapOf<String, Int>()
        for (arr in locations) for (i in 0 until arr.length()) {
            val poi = arr.optJSONObject(i) ?: continue
            val op = if (poi.isNull("OperatorID")) continue else poi.optInt("OperatorID", -1)
            if (op <= 0 || op in NOT_A_NETWORK) continue
            if (!poi.isNull("UsageTypeID") && poi.optInt("UsageTypeID") in RouteServices.PRIVATE_USAGE) continue
            if (!poi.isNull("StatusTypeID") && poi.optInt("StatusTypeID") in NOT_IN_USE) continue
            val name = names[op] ?: continue
            if ("tesla-only" in name.lowercase()) continue
            counts[name] = (counts[name] ?: 0) + 1
        }
        return counts.map { (n, c) -> Network(n, c) }
            .sortedWith(compareByDescending<Network> { it.locations }.thenBy { it.name.lowercase() })
    }

    /** The countries to count: the place-search limit, else the phone's own. */
    fun countriesFor(s: RouteSettings): List<String> =
        RouteServices.countryList(s.countries).split(",").map { it.trim().uppercase() }
            .filter { it.length == 2 }.ifEmpty { listOf(Locale.getDefault().country.ifBlank { "GB" }) }
            .take(3)

    fun cached(context: Context): NetworkList? = decode(
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString(KEY, null))

    /**
     * The list for [s]: the kept one while fresh, else fetched (and kept).
     * Throws RoutingException without a key or when Open Charge Map cannot
     * be reached; the kept list, if any, stays.
     */
    suspend fun load(context: Context, s: RouteSettings, refresh: Boolean = false,
                     minKw: Double = 50.0): NetworkList = withContext(Dispatchers.IO) {
        val countries = countriesFor(s)
        val key = countries.joinToString(",")
        val now = System.currentTimeMillis()
        cached(context)?.takeIf { !refresh && it.fresh(now, key, minKw) }?.let { return@withContext it }
        val services = RouteServices(s.orsKey, s.ocmKey, minChargerKw = minKw)
        val names = names(services.referenceData())
        val list = NetworkList(count(countries.map { services.locationsIn(it) }, names), key, minKw, now)
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            .putString(KEY, encode(list)).apply()
        list
    }

    fun encode(l: NetworkList): String = JSONObject().put("countries", l.countries)
        .put("min_kw", l.minKw).put("at", l.fetchedAtMs)
        .put("networks", JSONArray(l.networks.map { JSONObject().put("n", it.name).put("c", it.locations) }))
        .toString()

    fun decode(text: String?): NetworkList? = runCatching {
        val o = JSONObject(text ?: return null)
        val a = o.getJSONArray("networks")
        NetworkList((0 until a.length()).map { a.getJSONObject(it) }
            .map { Network(it.getString("n"), it.getInt("c")) },
            o.getString("countries"), o.getDouble("min_kw"), o.getLong("at"))
    }.getOrNull()

    /**
     * [name] added to one list (prefer or avoid), and taken out of the other:
     * a network cannot be both. Lists are the settings' comma-separated text.
     */
    fun choose(name: String, into: String, other: String): Pair<String, String> {
        val n = name.trim()
        if (n.isEmpty()) return into to other
        val added = (Planner.names(into) + n).distinctBy { it.lowercase() }
        val rest = Planner.names(other).filterNot { it.equals(n, ignoreCase = true) }
        return added.joinToString(", ") to rest.joinToString(", ")
    }

    /** [list] without [name]. */
    fun remove(name: String, list: String): String =
        Planner.names(list).filterNot { it.equals(name, ignoreCase = true) }.joinToString(", ")
}
