package com.odbscanner.ai

/** Where the voice is: one value, never a set of flags that can disagree. */
enum class VoiceState { IDLE, LISTENING, TRANSCRIBING, THINKING, SPEAKING, INTERRUPTED, ERROR }

/**
 * What started a turn: decides, e.g., whether going to the background stops
 * it (only PHONE: the car, the wake word and a trip's yes/no question have
 * their own microphone permission while the app is not in front).
 */
enum class VoiceSource { PHONE, CAR, WAKE_WORD, TYPED, PROMPT, TEST }

/**
 * The voice's one authoritative status: the state, the turn it belongs to,
 * what started that turn, and the last failure (shown until the next turn).
 */
data class VoiceStatus(
    val state: VoiceState = VoiceState.IDLE,
    val turn: Int = 0,
    val source: VoiceSource? = null,
    val error: AiError? = null,
) {
    /** A new turn may start (a failed one does not block the next). */
    val free: Boolean get() = state == VoiceState.IDLE || state == VoiceState.ERROR

    /** Something is under way that Stop would end. */
    val active: Boolean get() = !free && state != VoiceState.INTERRUPTED

    /** The microphone is (or is about to be) taken by the recogniser. */
    val usesMicrophone: Boolean
        get() = state == VoiceState.LISTENING || state == VoiceState.TRANSCRIBING
}

sealed class VoiceEvent {
    /** Begin a turn: listen first, or straight to thinking for a typed question. */
    data class Start(val source: VoiceSource, val typed: Boolean = false) : VoiceEvent()
    data class EndOfSpeech(val turn: Int) : VoiceEvent()
    data class Heard(val turn: Int) : VoiceEvent()
    /** The answer is ready: spoken (SPEAKING) or only shown (IDLE). */
    data class Answered(val turn: Int, val speak: Boolean) : VoiceEvent()
    data class Spoken(val turn: Int) : VoiceEvent()
    data class Failed(val turn: Int, val error: AiError) : VoiceEvent()
    /** The driver's Stop: whatever is under way ends. Harmless when nothing is. */
    object Stop : VoiceEvent()
    /** The driver speaks over the assistant: the old turn ends, a new one listens. */
    data class BargeIn(val source: VoiceSource) : VoiceEvent()
    /** After INTERRUPTED or ERROR: back to IDLE, if [turn] is still the latest. */
    data class Settle(val turn: Int) : VoiceEvent()
    /** The app went to the background: a phone turn lets go of the microphone. */
    object Background : VoiceEvent()
}

/**
 * The voice state machine. Every event either moves the status along an
 * allowed transition or is refused (returns false, status unchanged), so no
 * callback can put it somewhere it cannot be:
 *
 *     IDLE/ERROR   --Start--------> LISTENING (THINKING when typed), new turn
 *     LISTENING    --EndOfSpeech--> TRANSCRIBING
 *     LISTENING/TRANSCRIBING --Heard--> THINKING
 *     THINKING     --Answered-----> SPEAKING (IDLE when not spoken)
 *     SPEAKING     --Spoken-------> IDLE
 *     any active   --Failed-------> ERROR (the error kept)
 *     any active   --Stop---------> INTERRUPTED --Settle--> IDLE
 *     THINKING/SPEAKING/LISTENING/TRANSCRIBING --BargeIn--> LISTENING, new turn
 *     LISTENING/TRANSCRIBING of a phone turn --Background--> INTERRUPTED
 *     ERROR        --Settle-------> IDLE
 *
 * Events naming a turn are refused unless it is the current one: an answer,
 * a recognition result or a "spoken" from a turn that was stopped or
 * replaced can never move the status. Repeated Stops are accepted and
 * change nothing. Pure and synchronous: the caller serialises events.
 */
class VoiceMachine(initial: VoiceStatus = VoiceStatus()) {
    var status: VoiceStatus = initial
        private set

    /** Apply [e]; false when it is not allowed now (or is about an old turn). */
    fun on(e: VoiceEvent): Boolean {
        val s = status
        val next: VoiceStatus? = when (e) {
            is VoiceEvent.Start -> if (s.free || s.state == VoiceState.INTERRUPTED) {
                VoiceStatus(if (e.typed) VoiceState.THINKING else VoiceState.LISTENING,
                    s.turn + 1, e.source)
            } else null
            is VoiceEvent.EndOfSpeech -> if (current(e.turn) && s.state == VoiceState.LISTENING)
                s.copy(state = VoiceState.TRANSCRIBING) else null
            is VoiceEvent.Heard -> if (current(e.turn) && s.usesMicrophone)
                s.copy(state = VoiceState.THINKING) else null
            is VoiceEvent.Answered -> if (current(e.turn) && s.state == VoiceState.THINKING)
                s.copy(state = if (e.speak) VoiceState.SPEAKING else VoiceState.IDLE) else null
            is VoiceEvent.Spoken -> if (current(e.turn) && s.state == VoiceState.SPEAKING)
                s.copy(state = VoiceState.IDLE) else null
            is VoiceEvent.Failed -> if (current(e.turn) && s.active)
                s.copy(state = VoiceState.ERROR, error = e.error) else null
            VoiceEvent.Stop -> when {
                s.active -> s.copy(state = VoiceState.INTERRUPTED)
                else -> s                                   // already stopped: nothing to do
            }
            is VoiceEvent.BargeIn -> if (s.active)
                VoiceStatus(VoiceState.LISTENING, s.turn + 1, e.source) else null
            is VoiceEvent.Settle -> if (current(e.turn) &&
                (s.state == VoiceState.INTERRUPTED || s.state == VoiceState.ERROR))
                s.copy(state = VoiceState.IDLE) else null
            VoiceEvent.Background -> if (s.usesMicrophone && s.source == VoiceSource.PHONE)
                s.copy(state = VoiceState.INTERRUPTED) else null
        }
        if (next == null) return false
        status = next
        return true
    }

    private fun current(turn: Int) = turn == status.turn
}
