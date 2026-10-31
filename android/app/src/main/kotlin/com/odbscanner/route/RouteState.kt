package com.odbscanner.route

import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import kotlin.math.abs

/**
 * The trip's route as the app holds it: the one authoritative state, kept in
 * TripMonitor's ActiveTrip (review_output/navigation_review.md §4).
 *
 *  * Each **revision** is where the car must go -- the charging stops in
 *    order, then the destination -- with why it changed and when. Only a
 *    change of where to go is a new revision; new figures for the same
 *    stops (a fresher arrival charge) update the current one.
 *  * A re-plan is worked out **against** a revision. One worked out against
 *    an older revision is refused as stale, so a slow re-plan can never
 *    overwrite a newer one, and one that changes nothing is not applied
 *    twice.
 *  * A change the driver did not ask for waits as a **proposal** until they
 *    accept it; one they turned down is not offered again for the same stops.
 *  * The driver's **picks** (chargers to avoid or to use) belong to the trip
 *    and go into every re-plan.
 *  * The **provider sync** records what an outside navigation app (Google
 *    Maps) was last given. Google Maps cannot be updated from outside, only
 *    handed a new trip, so it is "stale" exactly when the current revision
 *    goes somewhere it was not sent; it never feeds anything back.
 *
 * Pure (org.json only), so it is unit-tested and can be saved and restored.
 */
data class Waypoint(val key: String, val name: String, val lat: Double, val lon: Double,
                    val isStop: Boolean)

data class Revision(
    val number: Int,
    val atMs: Long,
    val reason: String,
    val waypoints: List<Waypoint>,     // the charging stops in order, then the destination
    val arrivalPct: Double?,
    val status: String,                // the plan's: direct | with_stops | unreachable
    val distanceKm: Double,
) {
    val keys: List<String> get() = waypoints.map { it.key }
}

enum class SyncStatus { NOT_SENT, CURRENT, STALE, FAILED }

data class ProviderSync(
    val provider: String,              // GOOGLE_MAPS or IN_APP
    val sentRevision: Int? = null,
    val sentKeys: List<String> = emptyList(),
    val atMs: Long? = null,
    val status: SyncStatus = SyncStatus.NOT_SENT,
    val message: String = "",
)

/**
 * The driver's charger choices for this trip: chargers by Open Charge Map id,
 * and networks to keep off on top of the ones in Route settings.
 */
data class Picks(val avoidChargers: Set<String> = emptySet(),
                 val useChargers: Set<String> = emptySet(),
                 val avoidNetworks: Set<String> = emptySet()) {
    /** Never this charger (and no longer "use" it). */
    fun avoid(id: String) = copy(avoidChargers = avoidChargers + id, useChargers = useChargers - id)

    /** [altId] in place of the stop at [stopId]: use the one, keep off the other. */
    fun insteadOf(stopId: String, altId: String) = copy(
        avoidChargers = avoidChargers - altId + listOfNotNull(stopId.takeIf { it.isNotBlank() }),
        useChargers = useChargers - stopId + altId)

    fun avoidNetwork(name: String) = copy(avoidNetworks = avoidNetworks + name.trim())

    val isEmpty: Boolean get() = avoidChargers.isEmpty() && useChargers.isEmpty() && avoidNetworks.isEmpty()
}

/** A re-plan waiting for the driver: worked out against revision [basedOn]. */
data class Proposal(val basedOn: Int, val reason: String, val change: String, val atMs: Long,
                    val keys: List<String>, val urgent: Boolean)

data class RouteState(
    val tripId: String,
    val revision: Int,
    val history: List<Revision>,       // oldest first, the current last
    val sync: ProviderSync,
    val picks: Picks = Picks(),
    val proposal: Proposal? = null,
    val declined: Set<String> = emptySet(),   // signatures of proposals turned down
) {
    val current: Revision get() = history.last()

    /** Whether the outside navigation app should be handed the current revision. */
    val needsHandoff: Boolean
        get() = sync.provider == RouteRevisions.GOOGLE_MAPS && sync.sentKeys != current.keys
}

object RouteRevisions {
    const val GOOGLE_MAPS = "google_maps"
    const val IN_APP = "in_app"
    const val HISTORY = 12

    /** A stop's identity: its Open Charge Map id, else its position to ~11 m. */
    fun key(id: String, lat: Double, lon: Double): String =
        if (id.isNotBlank()) "ocm:$id" else String.format(Locale.ROOT, "ll:%.4f,%.4f", lat, lon)

    fun waypoints(plan: Plan, destination: Place): List<Waypoint> =
        plan.stops.map { s ->
            Waypoint(key(s.charger.id, s.charger.lat, s.charger.lon), s.charger.name,
                s.charger.lat, s.charger.lon, true)
        } + Waypoint(key("", destination.lat, destination.lon), destination.label,
            destination.lat, destination.lon, false)

    fun revision(number: Int, r: TripResult, reason: String, nowMs: Long) = Revision(
        number, nowMs, reason, waypoints(r.plan, r.destination), r.plan.arrivalPct,
        r.plan.status, r.plan.distanceKm)

    fun start(tripId: String, r: TripResult, provider: String, nowMs: Long,
              picks: Picks = Picks()): RouteState =
        RouteState(tripId, 1, listOf(revision(1, r, "planned", nowMs)), ProviderSync(provider),
            picks)

    sealed class Outcome {
        /** Where to go changed: a new revision. */
        data class Revised(val state: RouteState, val change: String) : Outcome()
        /** Same stops and destination: the current revision's figures updated. */
        data class Same(val state: RouteState) : Outcome()
        /** Worked out against an older revision: refused, nothing changed. */
        data class Stale(val state: RouteState, val basedOn: Int) : Outcome()
    }

    /** Apply a re-plan of [r] worked out against revision [basedOn]. */
    fun revise(state: RouteState, basedOn: Int, r: TripResult, reason: String,
               nowMs: Long): Outcome {
        if (basedOn != state.revision) return Outcome.Stale(state, basedOn)
        val next = revision(state.revision + 1, r, reason, nowMs)
        if (next.keys == state.current.keys && next.status == state.current.status) {
            val same = state.current.copy(arrivalPct = next.arrivalPct,
                distanceKm = next.distanceKm, atMs = nowMs)
            return Outcome.Same(state.copy(history = state.history.dropLast(1) + same,
                proposal = null))
        }
        val change = describe(state.current, next)
        val sync = when {
            state.sync.sentRevision == null -> state.sync
            state.sync.sentKeys != next.keys -> state.sync.copy(status = SyncStatus.STALE,
                message = "Google Maps still has revision ${state.sync.sentRevision}")
            else -> state.sync.copy(status = SyncStatus.CURRENT,
                message = "Google Maps already has these stops")
        }
        return Outcome.Revised(state.copy(revision = next.number,
            history = (state.history + next).takeLast(HISTORY), sync = sync, proposal = null),
            change)
    }

    /** Whether a re-plan of [r] would change where the car goes. */
    fun changesRoute(state: RouteState, r: TripResult): Boolean {
        val w = waypoints(r.plan, r.destination).map { it.key }
        return w != state.current.keys || r.plan.status != state.current.status
    }

    fun signature(keys: List<String>): String = keys.joinToString(">")

    /** What to do with a re-plan of [r] worked out against revision [basedOn]. */
    enum class Action {
        REFRESH,   // same stops: apply the fresher figures, nothing to approve
        APPLY,     // a new route the driver asked for: apply it
        PROPOSE,   // a new route the driver did not ask for: hold it for them
        KEEP,      // stale, or turned down before and not urgent: keep the plan
    }

    fun decide(state: RouteState, basedOn: Int, r: TripResult, driverAsked: Boolean,
               urgent: Boolean): Action = when {
        basedOn != state.revision -> Action.KEEP
        !changesRoute(state, r) -> Action.REFRESH
        driverAsked -> Action.APPLY
        !urgent && signature(waypoints(r.plan, r.destination).map { it.key }) in state.declined ->
            Action.KEEP
        else -> Action.PROPOSE
    }

    /**
     * Hold [r] (against revision [basedOn]) for the driver, unless it was
     * worked out against an older revision or was turned down before.
     * Null: nothing to propose.
     */
    fun propose(state: RouteState, basedOn: Int, r: TripResult, reason: String,
                nowMs: Long, urgent: Boolean): RouteState? {
        if (basedOn != state.revision || !changesRoute(state, r)) return null
        val keys = waypoints(r.plan, r.destination).map { it.key }
        if (signature(keys) in state.declined && !urgent) return null
        val change = describe(state.current, revision(state.revision + 1, r, reason, nowMs))
        return state.copy(proposal = Proposal(basedOn, reason, change, nowMs, keys, urgent))
    }

    fun decline(state: RouteState): RouteState {
        val p = state.proposal ?: return state
        return state.copy(proposal = null, declined = state.declined + signature(p.keys))
    }

    /**
     * Google Maps was handed [revision] (null: the current one). A hand-off
     * of a revision a newer plan has replaced records nothing: it must not
     * mark the current plan as sent.
     */
    fun markSent(state: RouteState, nowMs: Long, revision: Int? = null): RouteState =
        if (revision != null && revision != state.revision) state
        else state.copy(sync = state.sync.copy(
            sentRevision = state.revision, sentKeys = state.current.keys, atMs = nowMs,
            status = SyncStatus.CURRENT, message = "Google Maps has revision ${state.revision}"))

    fun markFailed(state: RouteState, nowMs: Long, why: String): RouteState =
        state.copy(sync = state.sync.copy(atMs = nowMs, status = SyncStatus.FAILED, message = why))

    /** "Charging at Hub B instead of Hub A; arrive with 14 % (was 6 %)." */
    fun describe(old: Revision, new: Revision): String {
        val parts = mutableListOf<String>()
        val oldStops = old.waypoints.filter { it.isStop }
        val newStops = new.waypoints.filter { it.isStop }
        val dropped = oldStops.filter { o -> newStops.none { it.key == o.key } }
        val added = newStops.filter { n -> oldStops.none { it.key == n.key } }
        when {
            dropped.size == 1 && added.size == 1 ->
                parts += "charging at ${added[0].name} instead of ${dropped[0].name}"
            else -> {
                if (added.isNotEmpty()) parts += "adds " + added.joinToString(", ") { it.name }
                if (dropped.isNotEmpty()) parts += "drops " + dropped.joinToString(", ") { it.name }
            }
        }
        if (old.waypoints.last().key != new.waypoints.last().key) {
            parts += "now to ${new.waypoints.last().name}"
        }
        if (parts.isEmpty() && old.keys != new.keys) parts += "the stops in a new order"
        if (new.status == "unreachable") parts += "the destination is out of reach on this charge"
        val a = new.arrivalPct
        val b = old.arrivalPct
        if (a != null) {
            parts += if (b != null && abs(a - b) >= 0.5)
                String.format(Locale.ROOT, "arrive with %.0f %% (was %.0f %%)", a, b)
            else String.format(Locale.ROOT, "arrive with %.0f %%", a)
        }
        return parts.joinToString("; ").replaceFirstChar { it.uppercase() } + "."
    }

    // -- saving and restoring ---------------------------------------------------

    fun toJson(s: RouteState): JSONObject = JSONObject()
        .put("trip_id", s.tripId).put("revision", s.revision)
        .put("history", JSONArray(s.history.map { rev ->
            JSONObject().put("n", rev.number).put("at", rev.atMs).put("reason", rev.reason)
                .put("arrival", rev.arrivalPct ?: JSONObject.NULL).put("status", rev.status)
                .put("km", rev.distanceKm)
                .put("waypoints", JSONArray(rev.waypoints.map { w ->
                    JSONObject().put("key", w.key).put("name", w.name).put("lat", w.lat)
                        .put("lon", w.lon).put("stop", w.isStop)
                }))
        }))
        .put("sync", JSONObject().put("provider", s.sync.provider)
            .put("sent", s.sync.sentRevision ?: JSONObject.NULL)
            .put("sent_keys", JSONArray(s.sync.sentKeys))
            .put("at", s.sync.atMs ?: JSONObject.NULL)
            .put("status", s.sync.status.name).put("message", s.sync.message))
        .put("avoid", JSONArray(s.picks.avoidChargers.toList()))
        .put("use", JSONArray(s.picks.useChargers.toList()))
        .put("avoid_networks", JSONArray(s.picks.avoidNetworks.toList()))
        .put("declined", JSONArray(s.declined.toList()))

    fun fromJson(o: JSONObject): RouteState {
        fun strings(a: JSONArray?) = a?.let { (0 until it.length()).map(it::getString) } ?: emptyList()
        val history = o.getJSONArray("history").let { a ->
            (0 until a.length()).map { i ->
                val r = a.getJSONObject(i)
                Revision(r.getInt("n"), r.getLong("at"), r.getString("reason"),
                    r.getJSONArray("waypoints").let { w ->
                        (0 until w.length()).map { k ->
                            val p = w.getJSONObject(k)
                            Waypoint(p.getString("key"), p.getString("name"), p.getDouble("lat"),
                                p.getDouble("lon"), p.getBoolean("stop"))
                        }
                    },
                    if (r.isNull("arrival")) null else r.getDouble("arrival"),
                    r.getString("status"), r.getDouble("km"))
            }
        }
        val sync = o.getJSONObject("sync").let { y ->
            ProviderSync(y.getString("provider"),
                if (y.isNull("sent")) null else y.getInt("sent"), strings(y.optJSONArray("sent_keys")),
                if (y.isNull("at")) null else y.getLong("at"),
                runCatching { SyncStatus.valueOf(y.getString("status")) }.getOrDefault(SyncStatus.NOT_SENT),
                y.optString("message"))
        }
        return RouteState(o.getString("trip_id"), o.getInt("revision"), history, sync,
            Picks(strings(o.optJSONArray("avoid")).toSet(), strings(o.optJSONArray("use")).toSet(),
                strings(o.optJSONArray("avoid_networks")).toSet()),
            null, strings(o.optJSONArray("declined")).toSet())
    }
}
