package com.odbscanner.ai

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The interaction history's numbers and its retention. */
class HistoryTest {
    private val day = 86_400_000L
    private fun i(at: Long, p: String, out: String = Interaction.OUTCOME_ANSWERED, ai: Long? = 2000,
                  stt: Long? = 900, cat: String? = null, stage: String? = null, fb: Boolean = false) =
        Interaction(at, "PHONE", p, "m", stt, ai?.let { it / 2 }, ai, 1500, out, cat, stage, false, fb)

    @Test
    fun successRatesTimesFailuresAndFallbacks() {
        val all = listOf(i(1, "Groq"), i(2, "Groq", ai = 12_000), i(3, "Gemini", fb = true),
            i(4, "Groq", Interaction.OUTCOME_FAILED, ai = null, cat = "RATE_LIMITED", stage = "AI"),
            i(5, "", Interaction.OUTCOME_FAILED, ai = null, cat = "AUDIO_ERROR", stage = "recognition"),
            i(6, "Groq", Interaction.OUTCOME_STOPPED))
        val s = History.stats(all)
        assertEquals(6, s.count)
        assertEquals(3, s.answered); assertEquals(2, s.failed); assertEquals(1, s.stopped)
        assertEquals(3.0 / 5.0, s.successRate!!, 1e-9)        // a stopped one is neither
        assertEquals(1, s.slow); assertEquals(1, s.fallbacks)
        assertEquals(mapOf("RATE_LIMITED" to 1, "AUDIO_ERROR" to 1), s.byCategory)
        assertEquals(2 to 1, s.byProvider["Groq"])
        val groq = History.stats(all, provider = "Groq")
        assertEquals(4, groq.count)
        val llm = History.stats(all, capability = History.CAP_LLM)
        assertTrue(llm.byCategory.containsKey("RATE_LIMITED") && !llm.byCategory.containsKey("AUDIO_ERROR"))
        assertNull(History.stats(emptyList()).successRate)
    }

    @Test
    fun keptOnlyAsLongAsYouSaidAndNeverMoreThanTheMaximum() {
        val now = 100 * day
        val all = (0 until 600).map { i(now - it * 60_000L, "Groq") } + i(now - 40 * day, "Old")
        val kept = History.prune(all, now, 30)
        assertEquals(History.MAX, kept.size)
        assertTrue(kept.none { it.provider == "Old" })
        assertEquals(0, History.prune(all, now, 0).size)        // history off: nothing kept
    }

    @Test
    fun aRecordSurvivesStorage() {
        val r = Interaction(5L, "CAR", "Claude (Anthropic)", "claude-opus-5-5", 800, 1200, 3000,
            2100, Interaction.OUTCOME_FAILED, "TIMEOUT", "AI", true, true, "where next?")
        assertEquals(r, Interaction.decode(Interaction.encode(r)))
        assertNull(Interaction.decode("{"))
    }

    @Test
    fun aChangedSettingAppliesToWhatIsAlreadyStored() {
        val now = 100 * day
        val all = listOf(i(now - 2 * day, "Groq").copy(question = "where next?"),
            i(now - 10 * day, "Groq").copy(question = "how far?"), i(now, "Groq"))
        assertTrue(History.kept(all, now, 30, text = false).all { it.question == null })
        assertEquals("where next?", History.kept(all, now, 30, text = true)[1].question)
        assertEquals(2, History.kept(all, now, 7, text = true).size)    // a shorter keep
        assertTrue(History.kept(all, now, 0, text = true).isEmpty())    // off: none kept
    }

    @Test
    fun aTurnIsCreditedToWhoAnsweredItNotToTheTurnBefore() {
        val failed = TurnTrace(VoiceSource.PHONE).apply {
            sttMs = 700; aiMs = 4000
            failed(AiError(AiErrorCategory.TIMEOUT, "Groq", "slow"), "AI")
        }.build()
        assertEquals("Groq", failed.provider)
        assertEquals("", failed.model)                           // no model answered
        assertEquals(false, failed.fallback)
        val backup = TurnTrace(VoiceSource.CAR).apply {
            answered("Gemini (backup)", "gemini-3.8-flash")
        }.build()
        assertEquals("Gemini", backup.provider); assertTrue(backup.fallback)
        // A speech failure after the answer stays with the service that answered.
        val voice = TurnTrace(VoiceSource.PHONE).apply {
            answered("Groq", "llama"); failed(AiError(AiErrorCategory.AUDIO_ERROR,
                AiErrors.SPEECH_OUT, "x"), "speech")
        }.build()
        assertEquals("Groq", voice.provider); assertEquals("speech", voice.stage)
    }

    @Test
    fun aStoppedTurnSaysWhereItWasStopped() {
        fun stopped(f: TurnTrace.() -> Unit, src: VoiceSource = VoiceSource.PHONE) =
            TurnTrace(src).apply(f).apply { stopped() }.build()
        val listening = stopped({})
        assertEquals(Interaction.OUTCOME_STOPPED, listening.outcome)
        assertTrue(listening.interrupted)
        assertEquals("recognition", listening.stage)
        assertEquals("", listening.provider)                      // nobody was asked
        assertEquals("AI", stopped({}, VoiceSource.TYPED).stage)
        assertEquals("AI", stopped({ sttMs = 800 }).stage)
        val speaking = stopped({ sttMs = 800; aiMs = 2000; answered("Groq", "llama") })
        assertEquals("speech", speaking.stage); assertEquals("Groq", speaking.provider)
    }
}
