package com.odbscanner.route

import android.content.Context
import org.json.JSONObject

/**
 * The trip being driven, saved so it survives the app being stopped: where
 * to, how to plan it, the roads it keeps off, whether it was navigated in the
 * app, and the RouteState (revisions, the driver's picks, what Google Maps
 * was handed). The plan itself is not saved -- after a restart the car is
 * somewhere else, so the trip is planned again from there (TripMonitor.restore).
 *
 * Kept in the app's private preferences; nothing leaves the phone.
 */
data class SavedTrip(
    val destination: Place,
    val mode: String,
    val avoid: Avoid,
    val navigating: Boolean,
    val route: RouteState,
    val savedAtMs: Long,
)

object TripStore {
    private const val FILE = "trip"
    private const val KEY = "active"
    const val MAX_AGE_MS = 12 * 3600_000L      // older than this, the trip is over

    fun encode(t: SavedTrip): String = JSONObject()
        .put("to", JSONObject().put("label", t.destination.label)
            .put("lat", t.destination.lat).put("lon", t.destination.lon))
        .put("mode", t.mode)
        .put("avoid", JSONObject().put("motorways", t.avoid.motorways)
            .put("tolls", t.avoid.tolls).put("ferries", t.avoid.ferries))
        .put("navigating", t.navigating)
        .put("route", RouteRevisions.toJson(t.route))
        .put("saved_at", t.savedAtMs)
        .toString()

    /** The saved trip, or null when there is none or it cannot be read. */
    fun decode(text: String?): SavedTrip? = runCatching {
        val o = JSONObject(text ?: return null)
        val to = o.getJSONObject("to")
        val a = o.getJSONObject("avoid")
        SavedTrip(Place(to.getString("label"), to.getDouble("lat"), to.getDouble("lon")),
            o.getString("mode"),
            Avoid(a.optBoolean("motorways"), a.optBoolean("tolls"), a.optBoolean("ferries")),
            o.optBoolean("navigating"), RouteRevisions.fromJson(o.getJSONObject("route")),
            o.getLong("saved_at"))
    }.getOrNull()

    /** Worth restoring: recent enough to still be the trip being driven. */
    fun fresh(t: SavedTrip, nowMs: Long): Boolean = nowMs - t.savedAtMs in 0..MAX_AGE_MS

    fun save(context: Context, t: SavedTrip) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            .putString(KEY, encode(t)).apply()
    }

    fun load(context: Context): SavedTrip? =
        decode(context.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString(KEY, null))

    /**
     * What to tell the driver after a restore: the trip is watched again;
     * how old the position it was planned from is, when not fresh (a
     * remembered position is not where the car is now); and what changed if
     * the plan made from here differs from the one saved -- a restore must
     * never change the stops without saying so. Pure, so it is unit-tested.
     */
    fun restoreNote(destination: String, before: Revision, after: Revision,
                    positionAgeMs: Long, googleMapsOlder: Boolean): String {
        val parts = mutableListOf("Watching your trip to $destination again")
        if (positionAgeMs > FRESH_POSITION_MS) {
            parts[0] += ", planned from where the phone was ${positionAgeMs / 60_000L} min ago"
        }
        parts[0] += "."
        if (before.keys != after.keys) {
            parts += "The plan has changed since: " + RouteRevisions.describe(before, after)
        }
        if (googleMapsOlder) parts += "Google Maps has an older plan: tap to send this one."
        return parts.joinToString(" ")
    }

    const val FRESH_POSITION_MS = 2 * 60_000L

    fun clear(context: Context) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit().remove(KEY).apply()
    }
}
