package com.odbscanner.ai

/** How a service is doing, as the assistant decides whether to ask it. */
enum class Health {
    /** In use. */
    HEALTHY,
    /** Failed repeatedly: rested until [HealthStatus.untilMs], then one trial. */
    RESTING,
    /** The rest is over: one request is let through to see if it is back. */
    TRIAL,
    /** Refused the key, out of quota, not allowed: retrying will not help. */
    NEEDS_ATTENTION,
}

data class HealthStatus(
    val state: Health = Health.HEALTHY,
    val failures: Int = 0,               // in a row
    val untilMs: Long? = null,
    val lastError: AiError? = null,
    val lastOkMs: Long? = null,
    val lastFailMs: Long? = null,
    val rests: Int = 0,                  // times rested since it last worked
    val probing: Boolean = false,        // its one trial request is out
)

/**
 * A circuit breaker per service. [threshold] failures in a row that a retry
 * could cure (network, timeout, rate limit, overload) rest the service for
 * a cooldown that doubles each time it is rested again, up to
 * [maxCooldownMs]. When the cooldown is over one request is let through (a
 * trial, the health check): success restores it, failure rests it longer.
 * A refused key, a used-up quota, a missing permission or a model the
 * service no longer has marks it NEEDS_ATTENTION at once for [attentionMs] -- no point asking again -- and
 * [reset] (the key or settings changed) clears it.
 *
 * Rapid switching is avoided twice over: a service is only rested after
 * [threshold] failures, and the conversation stays with whichever service
 * answered (FallbackChatSession). Pure: [clock] is given, so every case is
 * a unit test.
 */
class CircuitBreaker(
    private val threshold: Int = 3,
    private val baseCooldownMs: Long = 60_000L,
    private val maxCooldownMs: Long = 10 * 60_000L,
    private val attentionMs: Long = 30 * 60_000L,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val states = mutableMapOf<String, HealthStatus>()

    @Synchronized
    fun status(id: String): HealthStatus {
        val s = states[id] ?: return HealthStatus()
        if (s.probing) return s.copy(state = Health.TRIAL)
        // A rest that is over shows as ready for a trial.
        val until = s.untilMs
        return if ((s.state == Health.RESTING || s.state == Health.NEEDS_ATTENTION) &&
            until != null && clock() >= until) s.copy(state = Health.TRIAL) else s
    }

    /** May [id] be asked now? A trial is let through once. */
    @Synchronized
    fun allow(id: String): Boolean {
        val stored = states[id] ?: return true
        if (stored.probing) return false                  // one trial at a time
        return when (status(id).state) {
            Health.HEALTHY -> true
            Health.TRIAL -> {
                states[id] = stored.copy(probing = true)
                true
            }
            Health.RESTING, Health.NEEDS_ATTENTION -> false
        }
    }

    @Synchronized
    fun success(id: String) {
        states[id] = HealthStatus(Health.HEALTHY, 0, null, null, clock(), states[id]?.lastFailMs, 0)
    }

    @Synchronized
    fun failure(id: String, error: AiError) {
        val now = clock()
        val s = states[id] ?: HealthStatus()
        val next = if (permanent(error)) {
            s.copy(state = Health.NEEDS_ATTENTION, failures = s.failures + 1,
                untilMs = now + attentionMs, lastError = error, lastFailMs = now)
        } else {
            val failures = s.failures + 1
            val trial = s.probing || (s.untilMs != null && now >= s.untilMs)
            if (trial || failures >= threshold) {
                val cooldown = minOf(maxCooldownMs, baseCooldownMs shl minOf(s.rests, 16))
                s.copy(state = Health.RESTING, failures = failures, untilMs = now + cooldown,
                    lastError = error, lastFailMs = now, rests = s.rests + 1)
            } else {
                s.copy(state = Health.HEALTHY, failures = failures, untilMs = null,
                    lastError = error, lastFailMs = now)
            }
        }
        states[id] = next.copy(probing = false)
    }

    /**
     * A trial (or any request) that ended without an answer either way --
     * stopped by the driver, or held back by your daily limit: [id] may be
     * tried again, its rest unchanged.
     */
    @Synchronized
    fun release(id: String) {
        states[id]?.let { if (it.probing) states[id] = it.copy(probing = false) }
    }

    /** The key or the settings changed: start afresh. */
    @Synchronized
    fun reset(id: String) {
        states.remove(id)
    }

    /** Of [ids], the one whose rest ends first (when every one is resting). */
    @Synchronized
    fun soonest(ids: List<String>): String? = ids.minByOrNull { status(it).untilMs ?: 0L }

    companion object {
        /**
         * Failures that asking again will not cure -- a model that is not
         * there (retired, misnamed) included; an overloaded one is not.
         */
        fun permanent(e: AiError): Boolean = when (e.category) {
            AiErrorCategory.INVALID_CREDENTIALS, AiErrorCategory.PERMISSION_DENIED,
            AiErrorCategory.QUOTA_EXCEEDED, AiErrorCategory.NOT_CONFIGURED -> true
            AiErrorCategory.MODEL_UNAVAILABLE -> e.reason != "overloaded"
            else -> false
        }
    }
}

/** The services' health, for the assistant and the setup screen. */
object ProviderHealth {
    val breaker = CircuitBreaker()
}

/**
 * When one request may be tried again on the same service, and after how
 * long: only when it cannot have been processed, so a question is never
 * asked twice -- a connection that never opened, a refused one, a rate limit
 * or an overload the service says to wait out. Never after a read timeout or
 * a dropped connection once the request was sent (the service may have
 * answered, and billed), never for a key, quota, permission, model or
 * capability problem, and at most MAX_ATTEMPTS in all. A cancelled turn is
 * never retried: the wait between attempts is a suspension, and
 * cancellation ends it.
 */
object RetryPolicy {
    const val MAX_ATTEMPTS = 2
    const val MAX_WAIT_MS = 5_000L

    /** How long to wait before attempt [attempt] + 1, or null: do not retry. */
    fun delayFor(error: AiError?, attempt: Int, retryAfterMs: Long? = null): Long? {
        if (error == null || attempt >= MAX_ATTEMPTS) return null
        return when (error.category) {
            AiErrorCategory.RATE_LIMITED -> when {
                retryAfterMs == null -> 1_000L * attempt
                retryAfterMs <= MAX_WAIT_MS -> maxOf(0L, retryAfterMs)
                else -> null                        // too long: a backup instead
            }
            AiErrorCategory.MODEL_UNAVAILABLE -> if (error.reason == "overloaded")
                minOf(MAX_WAIT_MS, retryAfterMs ?: 1_500L) else null
            AiErrorCategory.NETWORK_ERROR -> if (error.reason == "connect") 800L else null
            AiErrorCategory.TIMEOUT -> if (error.reason == "connect") 800L else null
            else -> null
        }
    }

    /** A Retry-After (seconds) or retry-after-ms header, as milliseconds. */
    fun retryAfterMs(headers: Map<String, String>): Long? {
        headers["retry-after-ms"]?.trim()?.toDoubleOrNull()?.let { return it.toLong() }
        return headers["retry-after"]?.trim()?.toDoubleOrNull()?.let { (it * 1000).toLong() }
    }
}
