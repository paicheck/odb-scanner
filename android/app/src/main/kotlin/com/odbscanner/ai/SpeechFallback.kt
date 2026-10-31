package com.odbscanner.ai

/**
 * When the phone's speech falls back, and which way. Pure, so unit-tested.
 *
 * Recognition: online recognition that could not reach its service (no
 * network, its timeout, its server gone) is tried again on the phone, when
 * the phone can -- that keeps the voice on the phone, so it needs no
 * consent. The other way -- on-the-phone recognition lacking the language
 * or unsupported -- goes online only if you allowed it, because then your
 * voice leaves the phone. Silence and "no words" are not failures of the
 * service and are not retried.
 *
 * Speech output: a chosen voice that needs a connection, timed out, or
 * whose data is missing falls back to the engine's own voice.
 */
object SpeechFallback {
    /** Try again with offline = the returned value, or null: do not. */
    fun recogniserRetry(error: AiError, wasOffline: Boolean, localAvailable: Boolean,
                        onlineAllowed: Boolean): Boolean? = when {
        error.reason == "silence" || error.reason == "no_match" -> null
        !wasOffline && localAvailable && (error.category == AiErrorCategory.NETWORK_ERROR ||
            error.category == AiErrorCategory.TIMEOUT) -> true
        wasOffline && onlineAllowed && (error.category == AiErrorCategory.MODEL_UNAVAILABLE ||
            error.category == AiErrorCategory.UNSUPPORTED_CAPABILITY) -> false
        else -> null
    }

    fun voiceShouldFallBack(error: AiError): Boolean =
        error.category == AiErrorCategory.NETWORK_ERROR || error.category == AiErrorCategory.TIMEOUT ||
            error.category == AiErrorCategory.MODEL_UNAVAILABLE
}
