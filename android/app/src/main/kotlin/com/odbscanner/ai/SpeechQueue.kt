package com.odbscanner.ai

/** Whose words: the assistant's (one turn's), or the app's own (guidance, alerts). */
enum class SpeechOwner { ASSISTANT, APP }

data class Utterance(
    val id: String,
    val text: String,
    val owner: SpeechOwner = SpeechOwner.APP,
    val turn: Int = 0,                // the assistant turn it belongs to
    val urgent: Boolean = false,      // cut in now (a close junction), as QUEUE_FLUSH did
)

/** What the speech engine is told to do, in order. */
sealed class SpeechAction {
    data class Play(val u: Utterance) : SpeechAction()
    /** Stop the engine now (what it was saying is over). */
    object Halt : SpeechAction()
    /** These will never be said: release whoever waits for them. */
    data class Dropped(val ids: List<String>) : SpeechAction()
}

/**
 * What the phone says, in order, one utterance at a time -- the assistant's
 * answers and the app's guidance and alerts through one engine. Pure: it
 * decides, the Speaker carries it out on Android's TextToSpeech.
 *
 *  * The assistant's speech is only played while its turn is [isCurrent]:
 *    an answer from a turn that was stopped or replaced is dropped, never
 *    played late over a newer one.
 *  * [stopAssistant] drops the assistant's speech -- playing or queued --
 *    and leaves guidance and alerts to carry on.
 *  * An urgent utterance cuts in: everything playing or queued is dropped,
 *    as the engine's QUEUE_FLUSH did.
 *  * [clear] silences everything (muting the navigation voice).
 */
class SpeechQueue(private val isCurrent: (Int) -> Boolean) {
    private val queue = ArrayDeque<Utterance>()
    var playing: Utterance? = null
        private set

    val idle: Boolean get() = playing == null && queue.isEmpty()

    fun add(u: Utterance): List<SpeechAction> {
        if (u.owner == SpeechOwner.ASSISTANT && !isCurrent(u.turn)) {
            return listOf(SpeechAction.Dropped(listOf(u.id)))
        }
        if (u.urgent) {
            val dropped = listOfNotNull(playing?.id) + queue.map { it.id }
            queue.clear()
            playing = u
            return listOfNotNull(if (dropped.isNotEmpty()) SpeechAction.Dropped(dropped) else null,
                SpeechAction.Halt, SpeechAction.Play(u))
        }
        queue.addLast(u)
        return if (playing == null) next() else emptyList()
    }

    /** The engine finished (or failed, or was stopped on) [id]. */
    fun done(id: String?): List<SpeechAction> {
        if (id == null || playing?.id != id) return emptyList()   // an old or halted one
        playing = null
        return next()
    }

    fun stopAssistant(): List<SpeechAction> {
        val dropped = queue.filter { it.owner == SpeechOwner.ASSISTANT }.map { it.id }
        queue.removeAll { it.owner == SpeechOwner.ASSISTANT }
        val out = mutableListOf<SpeechAction>()
        val p = playing
        if (p != null && p.owner == SpeechOwner.ASSISTANT) {
            playing = null
            out += SpeechAction.Dropped(listOf(p.id) + dropped)
            out += SpeechAction.Halt
            out += next()
        } else if (dropped.isNotEmpty()) {
            out += SpeechAction.Dropped(dropped)
        }
        return out
    }

    fun clear(): List<SpeechAction> {
        val dropped = listOfNotNull(playing?.id) + queue.map { it.id }
        queue.clear()
        playing = null
        return listOfNotNull(if (dropped.isNotEmpty()) SpeechAction.Dropped(dropped) else null,
            SpeechAction.Halt)
    }

    /** The next utterance to play, dropping the assistant's from turns now over. */
    private fun next(): List<SpeechAction> {
        val stale = mutableListOf<String>()
        while (queue.isNotEmpty()) {
            val u = queue.removeFirst()
            if (u.owner == SpeechOwner.ASSISTANT && !isCurrent(u.turn)) {
                stale += u.id
                continue
            }
            playing = u
            return listOfNotNull(if (stale.isNotEmpty()) SpeechAction.Dropped(stale) else null,
                SpeechAction.Play(u))
        }
        return if (stale.isNotEmpty()) listOf(SpeechAction.Dropped(stale)) else emptyList()
    }
}
