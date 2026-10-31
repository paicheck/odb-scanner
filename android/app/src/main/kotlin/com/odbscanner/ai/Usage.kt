package com.odbscanner.ai

import android.content.Context
import org.json.JSONObject
import java.time.Instant
import java.time.ZoneId

/** What you pay a service per million tokens, as you entered it (never assumed). */
data class Price(val inPerMillion: Double, val outPerMillion: Double)

/** Your daily limits: 0 means none. Spending counts only services you gave a price. */
data class UsageLimits(val requestsPerDay: Int = 0, val spendPerDay: Double = 0.0)

/** One service's use on one day, from what its replies reported. */
data class UsageDay(
    val day: String,
    val requests: Int = 0,
    val inputTokens: Long = 0,
    val outputTokens: Long = 0,
    val unreported: Int = 0,         // requests whose reply gave no token counts
) {
    /**
     * An estimate from [price] -- your price, not the service's bill -- or
     * null without a price or when some requests did not report tokens
     * (the estimate would be too low).
     */
    fun cost(price: Price?): Double? {
        if (price == null || unreported > 0) return null
        return inputTokens / 1e6 * price.inPerMillion + outputTokens / 1e6 * price.outPerMillion
    }
}

/**
 * Token use per service per day, as the services' replies report it, and
 * your limits on it. Pure: the day comes from [clock]; persisted by
 * [UsageLedger]. Cost is only ever an estimate from prices you entered,
 * kept apart from any bill.
 */
class UsageBook(private val clock: () -> Long = System::currentTimeMillis,
                private val zone: ZoneId = ZoneId.systemDefault()) {
    private val days = mutableMapOf<String, UsageDay>()       // "<id>|<day>" -> use

    fun today(): String = Instant.ofEpochMilli(clock()).atZone(zone).toLocalDate().toString()

    @Synchronized
    fun record(id: String, input: Int?, output: Int?) {
        val day = today()
        val k = "$id|$day"
        val u = days[k] ?: UsageDay(day)
        days[k] = if (input == null && output == null) u.copy(requests = u.requests + 1,
            unreported = u.unreported + 1)
        else u.copy(requests = u.requests + 1, inputTokens = u.inputTokens + (input ?: 0),
            outputTokens = u.outputTokens + (output ?: 0))
        // Keep a week.
        val keep = (0..6).map { Instant.ofEpochMilli(clock() - it * 86_400_000L).atZone(zone)
            .toLocalDate().toString() }.toSet()
        days.keys.removeAll { it.substringAfter('|') !in keep }
    }

    @Synchronized
    fun day(id: String, day: String = today()): UsageDay = days["$id|$day"] ?: UsageDay(day)

    @Synchronized
    fun ids(): Set<String> = days.keys.map { it.substringBefore('|') }.toSet()

    private fun requestsToday() = ids().sumOf { day(it).requests }

    private fun spendToday(prices: Map<String, Price>) =
        ids().sumOf { day(it).cost(prices[it]) ?: 0.0 }

    /** The limit [id] has reached today, as a QUOTA_EXCEEDED, or null. */
    fun check(id: String, label: String, limits: UsageLimits, prices: Map<String, Price>): AiError? {
        if (limits.requestsPerDay > 0 && requestsToday() >= limits.requestsPerDay) {
            return AiError(AiErrorCategory.QUOTA_EXCEEDED, label,
                "Your daily limit of ${limits.requestsPerDay} AI requests is reached " +
                    "(AI & Voice Setup)", reason = "local_limit")
        }
        if (limits.spendPerDay > 0 && prices[id] != null &&
            spendToday(prices) >= limits.spendPerDay) {
            return AiError(AiErrorCategory.QUOTA_EXCEEDED, label,
                "Your daily spending limit is reached (estimated from your prices)",
                reason = "local_limit")
        }
        return null
    }

    /** "80 % of today's request limit used", once a limit is that close; else null. */
    fun warning(limits: UsageLimits, prices: Map<String, Price>): String? {
        if (limits.requestsPerDay > 0) {
            val used = requestsToday()
            if (used >= limits.requestsPerDay * 0.8) {
                return "$used of today's ${limits.requestsPerDay} AI requests used"
            }
        }
        if (limits.spendPerDay > 0) {
            val spent = spendToday(prices)
            if (spent >= limits.spendPerDay * 0.8) {
                return String.format(java.util.Locale.ROOT,
                    "About %.2f of today's %.2f spending limit used (estimated from your prices)",
                    spent, limits.spendPerDay)
            }
        }
        return null
    }

    @Synchronized
    fun encode(): String = JSONObject().apply {
        days.forEach { (k, u) ->
            put(k, JSONObject().put("r", u.requests).put("i", u.inputTokens)
                .put("o", u.outputTokens).put("u", u.unreported))
        }
    }.toString()

    @Synchronized
    fun load(text: String?) {
        days.clear()
        val o = runCatching { JSONObject(text ?: return) }.getOrNull() ?: return
        o.keys().forEach { k ->
            val u = o.getJSONObject(k)
            days[k] = UsageDay(k.substringAfter('|'), u.optInt("r"), u.optLong("i"),
                u.optLong("o"), u.optInt("u"))
        }
    }
}

/** The app's usage book, kept in private preferences (no question text, no key). */
object UsageLedger : UsageSink {
    private const val FILE = "ai_usage"
    val book = UsageBook()
    @Volatile private var app: Context? = null

    fun attach(context: Context) {
        if (app != null) return
        app = context.applicationContext
        book.load(context.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString("book", null))
    }

    override fun record(provider: String, inputTokens: Int?, outputTokens: Int?) {
        book.record(provider, inputTokens, outputTokens)
        app?.getSharedPreferences(FILE, Context.MODE_PRIVATE)?.edit()
            ?.putString("book", book.encode())?.apply()
    }
}
