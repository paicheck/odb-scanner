package com.odbscanner.ai

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The voice state machine: every scenario asked for, as a sequence of
 * events. Pure and synchronous, so each step is exact.
 */
class VoiceMachineTest {
    private val err = AiError(AiErrorCategory.TIMEOUT, "Svc", "took too long")

    private fun listening(): Pair<VoiceMachine, Int> {
        val m = VoiceMachine()
        assertTrue(m.on(VoiceEvent.Start(VoiceSource.PHONE)))
        return m to m.status.turn
    }

    private fun speaking(): Pair<VoiceMachine, Int> {
        val (m, t) = listening()
        assertTrue(m.on(VoiceEvent.EndOfSpeech(t)))
        assertEquals(VoiceState.TRANSCRIBING, m.status.state)
        assertTrue(m.on(VoiceEvent.Heard(t)))
        assertTrue(m.on(VoiceEvent.Answered(t, speak = true)))
        assertEquals(VoiceState.SPEAKING, m.status.state)
        return m to t
    }

    @Test
    fun aWholeTurnGoesRoundAndBack() {
        val (m, t) = speaking()
        assertTrue(m.on(VoiceEvent.Spoken(t)))
        assertEquals(VoiceState.IDLE, m.status.state)
    }

    @Test
    fun interruptingDuringSpeech() {
        val (m, t) = speaking()
        assertTrue(m.on(VoiceEvent.Stop))
        assertEquals(VoiceState.INTERRUPTED, m.status.state)
        assertFalse(m.on(VoiceEvent.Spoken(t)))       // the engine's late "done" moves nothing
        assertTrue(m.on(VoiceEvent.Settle(t)))
        assertEquals(VoiceState.IDLE, m.status.state)
    }

    @Test
    fun interruptingDuringAiGenerationAndALateAnswer() {
        val (m, t) = listening()
        m.on(VoiceEvent.Heard(t))
        assertEquals(VoiceState.THINKING, m.status.state)
        assertTrue(m.on(VoiceEvent.Stop))
        // The answer that arrives after the stop is refused: never spoken.
        assertFalse(m.on(VoiceEvent.Answered(t, speak = true)))
        assertEquals(VoiceState.INTERRUPTED, m.status.state)
    }

    @Test
    fun cancellingDuringRecognition() {
        val (m, t) = listening()
        m.on(VoiceEvent.EndOfSpeech(t))
        assertTrue(m.on(VoiceEvent.Stop))
        assertFalse(m.on(VoiceEvent.Heard(t)))          // the recogniser's late result
        assertTrue(m.on(VoiceEvent.Settle(t)))
        assertTrue(m.status.free)
    }

    @Test
    fun speechOutputFailingEndsInErrorAndANewTurnCanStart() {
        val (m, t) = speaking()
        val tts = AiError(AiErrorCategory.AUDIO_ERROR, AiErrors.SPEECH_OUT, "could not play")
        assertTrue(m.on(VoiceEvent.Failed(t, tts)))
        assertEquals(VoiceState.ERROR, m.status.state)
        assertEquals(tts, m.status.error)
        assertTrue(m.status.free)                          // retry at once, no waiting
        assertTrue(m.on(VoiceEvent.Start(VoiceSource.PHONE)))
        assertEquals(t + 1, m.status.turn)
        assertEquals(null, m.status.error)
    }

    @Test
    fun aNetworkTimeoutAndAProviderRejectionRecover() {
        for (e in listOf(err, AiError(AiErrorCategory.INVALID_CREDENTIALS, "Svc", "refused"))) {
            val (m, t) = listening()
            m.on(VoiceEvent.Heard(t))
            assertTrue(m.on(VoiceEvent.Failed(t, e)))
            assertEquals(e.category, m.status.error?.category)
            assertFalse(m.on(VoiceEvent.Answered(t, speak = true)))
            assertTrue(m.on(VoiceEvent.Settle(t)))
            assertEquals(VoiceState.IDLE, m.status.state)
        }
    }

    @Test
    fun repeatedStopsAreHarmless() {
        val (m, t) = speaking()
        repeat(5) { assertTrue(m.on(VoiceEvent.Stop)) }
        assertEquals(VoiceState.INTERRUPTED, m.status.state)
        assertEquals(t, m.status.turn)
        m.on(VoiceEvent.Settle(t))
        repeat(3) { assertTrue(m.on(VoiceEvent.Stop)) }   // nothing running: nothing changes
        assertEquals(VoiceState.IDLE, m.status.state)
    }

    @Test
    fun aNewRequestWhileAnOldOneIsFinishing() {
        val (m, old) = speaking()
        assertTrue(m.on(VoiceEvent.BargeIn(VoiceSource.PHONE)))   // the driver talks over it
        val new = m.status.turn
        assertEquals(old + 1, new)
        assertEquals(VoiceState.LISTENING, m.status.state)
        // The old turn finishing late changes nothing about the new one.
        assertFalse(m.on(VoiceEvent.Spoken(old)))
        assertFalse(m.on(VoiceEvent.Failed(old, err)))
        assertFalse(m.on(VoiceEvent.Settle(old)))
        assertEquals(VoiceState.LISTENING, m.status.state)
        assertTrue(m.on(VoiceEvent.Heard(new)))
    }

    @Test
    fun onlyOneConversationAtATime() {
        val (m, t) = listening()
        assertFalse(m.on(VoiceEvent.Start(VoiceSource.CAR)))
        assertFalse(m.on(VoiceEvent.Start(VoiceSource.TYPED, typed = true)))
        assertEquals(t, m.status.turn)
    }

    @Test
    fun staleAudioCannotOutliveItsTurn() {
        // What the speaker asks before playing (VoiceAssistant.isCurrentTurn):
        // the turn must be the latest and not interrupted.
        val (m, t) = speaking()
        fun mayPlay(turn: Int) = m.status.turn == turn && m.status.state != VoiceState.INTERRUPTED
        assertTrue(mayPlay(t))
        m.on(VoiceEvent.Stop)
        assertFalse(mayPlay(t))
        m.on(VoiceEvent.Start(VoiceSource.PHONE))
        assertFalse(mayPlay(t))
        assertTrue(mayPlay(t + 1))
    }

    @Test
    fun backgroundingFreesTheMicrophoneOfAPhoneTurnOnly() {
        val (m, t) = listening()
        assertTrue(m.on(VoiceEvent.Background))
        assertEquals(VoiceState.INTERRUPTED, m.status.state)
        m.on(VoiceEvent.Settle(t))

        val (car, ct) = VoiceMachine().let { it.on(VoiceEvent.Start(VoiceSource.CAR)); it to it.status.turn }
        assertFalse(car.on(VoiceEvent.Background))        // the car keeps listening
        assertEquals(VoiceState.LISTENING, car.status.state)

        val (sp, _) = speaking()
        assertFalse(sp.on(VoiceEvent.Background))         // an answer carries on being spoken
        assertEquals(VoiceState.SPEAKING, sp.status.state)
        // Back in front: nothing to undo; the next Speak just starts.
        assertTrue(m.on(VoiceEvent.Start(VoiceSource.PHONE)))
        assertEquals(ct, car.status.turn)
    }

    @Test
    fun aTypedQuestionThinksWithoutListeningAndCanBeStopped() {
        val m = VoiceMachine()
        assertTrue(m.on(VoiceEvent.Start(VoiceSource.TYPED, typed = true)))
        assertEquals(VoiceState.THINKING, m.status.state)
        assertTrue(m.on(VoiceEvent.Stop))
        assertEquals(VoiceState.INTERRUPTED, m.status.state)
        assertTrue(m.on(VoiceEvent.Start(VoiceSource.PHONE)))   // straight after a stop
    }

    @Test
    fun eventsOutOfOrderAreRefused() {
        val m = VoiceMachine()
        assertFalse(m.on(VoiceEvent.Heard(0)))
        assertFalse(m.on(VoiceEvent.Spoken(0)))
        assertFalse(m.on(VoiceEvent.BargeIn(VoiceSource.PHONE)))   // nothing to talk over
        val (l, t) = listening()
        assertFalse(l.on(VoiceEvent.Spoken(t)))
        assertFalse(l.on(VoiceEvent.Answered(t, true)))
        assertEquals(VoiceState.LISTENING, l.status.state)
    }

    @Test
    fun retryStaysOfferedAfterTheErrorSettlesUntilANewTurn() {
        val (m, t) = listening()
        m.on(VoiceEvent.Heard(t))
        m.on(VoiceEvent.Failed(t, err))
        assertTrue(m.status.retryable)
        m.on(VoiceEvent.Settle(t))
        assertEquals(VoiceState.IDLE, m.status.state)
        assertTrue("still offered after the 6 s hold", m.status.retryable)
        m.on(VoiceEvent.Start(VoiceSource.CAR))
        assertFalse(m.status.retryable)
        val (s, st) = speaking()                           // stopped, not failed: nothing to retry
        s.on(VoiceEvent.Stop); s.on(VoiceEvent.Settle(st))
        assertFalse(s.status.retryable)
        assertFalse(VoiceMachine().status.retryable)
    }
}
