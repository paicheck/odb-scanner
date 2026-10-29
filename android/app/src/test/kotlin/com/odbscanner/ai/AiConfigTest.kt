package com.odbscanner.ai

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Which model each service uses when the Model box is left blank. */
class AiConfigTest {

    @Test
    fun theFreeServicesNeedOnlyAKey() {
        assertEquals("gemini-3.8-flash", AiSettings(provider = "gemini").effectiveModel)
        assertEquals("llama-3.3-70b-versatile", AiSettings(provider = "groq").effectiveModel)
        assertEquals("mistral-small-latest", AiSettings(provider = "mistral").effectiveModel)
        assertEquals(AiPresets.CLAUDE_DEFAULT_MODEL, AiSettings(provider = "anthropic").effectiveModel)
        for (id in listOf("gemini", "groq", "mistral")) {
            assertTrue(id, AiPresets.byId(id).freeNote.isNotBlank())
        }
    }

    @Test
    fun aNamedModelWinsAndTheRestStillNeedOne() {
        assertEquals("mine", AiSettings(provider = "gemini", model = "mine").effectiveModel)
        assertEquals("", AiSettings(provider = "openai").effectiveModel)
        assertEquals("", AiSettings(provider = "custom").effectiveModel)
    }
}
