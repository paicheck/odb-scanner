package com.odbscanner.ai

import org.json.JSONObject
import kotlin.math.roundToInt

/**
 * Answers the phone can give on its own, when no AI service can be reached
 * (no connection, none set up, out of quota): the battery, the range, the
 * next charging stop, the arrival charge, the trip left, fault codes. The
 * words are matched to one of those, the same read-only tools the assistant
 * uses give the figures (the app's own, with their age), and a fixed
 * sentence says them. Nothing is guessed: no tool figure, no answer.
 *
 * Deliberately small: anything else waits for an AI service. Pure apart
 * from the tool calls, which are passed in.
 */
object LocalAnswers {
    enum class Intent { ARRIVAL, NEXT_STOP, RANGE, BATTERY, TRIP_LEFT, FAULTS }

    /** What the question asks about, or null when it is not one of these. */
    fun intent(text: String): Intent? {
        val t = text.lowercase()
        fun any(vararg w: String) = w.any { it in t }
        return when {
            any("make it", "arrive with", "arrival", "when i get there", "will i get there",
                "enough charge", "enough battery") -> Intent.ARRIVAL
            any("next stop", "next charg", "where do i charge", "where am i charging",
                "charging stop") -> Intent.NEXT_STOP
            any("range", "how far can i", "how many miles left", "miles can i") -> Intent.RANGE
            any("battery", "charge level", "state of charge", "how much charge", "percent",
                "per cent") -> Intent.BATTERY
            any("how far", "how long", "time left", "eta", "distance left", "remaining") ->
                Intent.TRIP_LEFT
            any("fault", "error code", "trouble code", "dtc", "warning light") -> Intent.FAULTS
            else -> null
        }
    }

    /** Which tool gives the figures for [intent]. */
    fun tool(intent: Intent): String = when (intent) {
        Intent.BATTERY, Intent.RANGE -> "get_battery"
        Intent.FAULTS -> "get_fault_codes"
        else -> "get_trip"
    }

    /** The sentence for [intent] from [data] (the tool's JSON), or null: nothing to say. */
    fun say(intent: Intent, data: JSONObject): String? {
        if (data.has("error")) return when (intent) {
            Intent.NEXT_STOP, Intent.ARRIVAL, Intent.TRIP_LEFT -> "There's no trip planned."
            else -> null
        }
        fun pct(o: JSONObject?, k: String) = o?.optDouble(k)?.takeIf { !it.isNaN() }?.roundToInt()
        fun miles(o: JSONObject?) = o?.optDouble("miles")?.takeIf { !it.isNaN() }?.roundToInt()
        fun reading(k: String) = data.optJSONObject(k)
        return when (intent) {
            Intent.BATTERY -> {
                val r = reading("charge_display_pct") ?: reading("charge_bms_pct") ?: return null
                val v = r.optDouble("value").takeIf { !it.isNaN() }?.roundToInt() ?: return null
                aged(r.optString("age"), "The battery is at $v per cent",
                    "The last battery reading", "$v per cent")
            }
            Intent.RANGE -> {
                val r = reading("range_shown_by_car") ?: return null
                val v = r.optDouble("value").takeIf { !it.isNaN() }?.roundToInt() ?: return null
                val unit = r.optString("unit").ifBlank { "miles" }
                aged(r.optString("age"), "The car shows $v $unit of range",
                    "The last range the car showed", "$v $unit")
            }
            Intent.FAULTS -> when (val n = data.optInt("count", -1)) {
                -1 -> null
                0 -> "No fault codes are stored."
                else -> "$n fault code${if (n == 1) " is" else "s are"} stored; " +
                    "the details are on the phone."
            }
            Intent.NEXT_STOP -> {
                val s = data.optJSONArray("stops_ahead")?.optJSONObject(0)
                    ?: return "There's no charging stop ahead on this trip."
                val planned = pct(s, "arrive_pct")?.let { ". Planned arrival charge is $it per cent" } ?: ""
                val far = miles(s.optJSONObject("from_here"))
                if (far == null && unplaced(data)) {
                    "The first charging stop on the plan is ${s.optString("charger")}" +
                        (miles(s.optJSONObject("from_start"))?.let { ", about $it miles from the start" }
                            ?: "") + planned + ". $NO_POSITION"
                } else {
                    "Your next stop is ${s.optString("charger")}" +
                        (far?.let { ", about $it miles away" } ?: "") + planned + "."
                }
            }
            Intent.ARRIVAL -> {
                val live = data.optJSONObject("live_check")
                val projected = pct(live, "projected_at_next_pct")
                val s = data.optJSONArray("stops_ahead")?.optJSONObject(0)
                val where = s?.optString("charger")?.takeIf { it.isNotBlank() } ?: "the destination"
                // The projection is only as fresh as the battery reading behind it.
                val read = live?.optString("battery_reading_age")?.takeIf { it.isNotBlank() }
                    ?.let { if (it == "unknown") ", from a battery reading of unknown age" else
                        ", from the battery reading $it" } ?: ""
                when {
                    projected != null -> "At the rate you're using, about $projected per cent at " +
                        "$where" + (pct(live, "planned_at_next_pct")?.let { "; the plan said $it" } ?: "") +
                        read + "."
                    s != null -> pct(s, "arrive_pct")?.let { "The plan has you arriving at $where " +
                        "with $it per cent." }
                    else -> pct(data, "arrival_charge_pct")?.let { "The plan has you arriving " +
                        "with $it per cent." }
                }
            }
            Intent.TRIP_LEFT -> {
                val to = data.optString("destination").ifBlank { "go" }
                miles(data.optJSONObject("remaining"))?.let { return "About $it miles to $to." }
                // Without a position only the whole trip is known: never passed off as what is left.
                val total = miles(data.optJSONObject("total")) ?: return null
                "The whole trip to ${data.optString("destination").ifBlank { "the destination" }} " +
                    "is about $total miles. $NO_POSITION"
            }
        }
    }

    const val NO_POSITION = "The phone doesn't know where you are on the route right now."

    /** The trip tool had no position for the phone (AssistantTools' "position"). */
    private fun unplaced(data: JSONObject) = data.optString("position").startsWith("unknown")

    /**
     * A reading said with how old it is: "is" only while it is recent (under
     * an hour); an older one, or one of unknown age, as the last reading.
     */
    private fun aged(age: String, now: String, last: String, value: String): String = when {
        age == "just now" || age.endsWith("min ago") -> "$now (read $age)."
        age.isBlank() || age == "unknown" -> "$last was $value; when it was read is not known."
        else -> "$last, $age, was $value."
    }

    /**
     * When the phone cannot answer by itself either: what it can do without an
     * AI service, said honestly rather than a guess. [intent]: the question
     * was one of those, but the phone's data has no answer to it now.
     */
    fun cannot(intent: Intent?): String = if (intent != null)
        "The phone's own data has no answer to that right now."
    else "Without an AI service I can only tell you the battery, the range, the next charging " +
        "stop, the arrival charge, the trip left and fault codes."

    /** Failures where the phone's own answer is better than none. */
    fun covers(e: AiError): Boolean = e.category == AiErrorCategory.NETWORK_ERROR ||
        e.category == AiErrorCategory.TIMEOUT || e.category == AiErrorCategory.NOT_CONFIGURED ||
        e.category == AiErrorCategory.QUOTA_EXCEEDED || e.category == AiErrorCategory.RATE_LIMITED ||
        e.category == AiErrorCategory.MODEL_UNAVAILABLE || e.category == AiErrorCategory.INVALID_CREDENTIALS
}
