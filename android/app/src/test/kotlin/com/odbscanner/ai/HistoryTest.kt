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
}
