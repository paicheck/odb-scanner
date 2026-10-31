package com.odbscanner.ai

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What the phone says: the assistant's speech by turn, the app's guidance
 * and alerts by order, an urgent cue cutting in, and Stop silencing only the
 * assistant.
 */
class SpeechQueueTest {
    private var current = 1
    private val q = SpeechQueue { it == current }

    private fun said(actions: List<SpeechAction>) =
        actions.filterIsInstance<SpeechAction.Play>().map { it.u.id }

    private fun dropped(actions: List<SpeechAction>) =
        actions.filterIsInstance<SpeechAction.Dropped>().flatMap { it.ids }

    private fun a(id: String, turn: Int = current) = Utterance(id, id, SpeechOwner.ASSISTANT, turn)
    private fun app(id: String, urgent: Boolean = false) = Utterance(id, id, urgent = urgent)

    @Test
    fun oneAtATimeInOrder() {
        assertEquals(listOf("a1"), said(q.add(a("a1"))))
        assertEquals(emptyList<String>(), said(q.add(app("n1"))))
        assertEquals(listOf("n1"), said(q.done("a1")))
        assertEquals(emptyList<String>(), said(q.done("n1")))
        assertTrue(q.idle)
    }

    @Test
    fun stoppingTheAssistantLeavesGuidanceToCarryOn() {
        q.add(a("a1"))
        q.add(a("a2"))
        q.add(app("n1"))
        val out = q.stopAssistant()
        assertEquals(listOf("a1", "a2"), dropped(out))
        assertTrue(out.contains(SpeechAction.Halt))
        assertEquals(listOf("n1"), said(out))
        // The engine's late "stopped" for a1 changes nothing.
        assertEquals(emptyList<String>(), said(q.done("a1")))
        assertEquals("n1", q.playing?.id)
    }

    @Test
    fun stoppingWhileGuidanceSpeaksDropsOnlyTheQueuedAnswer() {
        q.add(app("n1"))
        q.add(a("a1"))
        val out = q.stopAssistant()
        assertEquals(listOf("a1"), dropped(out))
        assertTrue(SpeechAction.Halt !in out)              // guidance is not cut off
        assertEquals("n1", q.playing?.id)
    }

    @Test
    fun anAnswerFromAStoppedTurnNeverPlays() {
        current = 2
        assertEquals(listOf("late"), dropped(q.add(a("late", turn = 1))))
        assertNull(q.playing)
    }

    @Test
    fun aQueuedAnswerWhoseTurnEndedIsSkippedNotPlayedOverTheNewOne() {
        q.add(app("n1"))
        q.add(a("old", turn = 1))
        current = 2                                         // a barge-in started turn 2
        q.add(a("new", turn = 2))
        val out = q.done("n1")
        assertEquals(listOf("old"), dropped(out))
        assertEquals(listOf("new"), said(out))
    }

    @Test
    fun anUrgentCueCutsInAsTheEngineFlushDid() {
        q.add(a("a1"))
        q.add(app("n1"))
        val out = q.add(app("turn left", urgent = true))
        assertEquals(listOf("a1", "n1"), dropped(out))
        assertEquals(listOf("turn left"), said(out))
        assertEquals(SpeechAction.Halt, out[1])
    }

    @Test
    fun clearSilencesEverything() {
        q.add(a("a1"))
        q.add(app("n1"))
        val out = q.clear()
        assertEquals(listOf("a1", "n1"), dropped(out))
        assertTrue(q.idle)
    }
}
