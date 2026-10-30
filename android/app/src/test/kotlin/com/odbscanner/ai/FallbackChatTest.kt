package com.odbscanner.ai

import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * Backups for the assistant: the chosen service, then each backup in turn.
 * What is pinned: a failure hands the words on, a backup that cannot even be
 * made is one more failure, the conversation stays with whoever answered, a
 * turn that already acted is never repeated on a backup, and the settings
 * only keep services that can be backups.
 */
class FallbackChatTest {

    private class Fake(
        override val model: String,
        private val answer: String? = null,
        private val error: String? = null,
        private val tool: String? = null,
    ) : ChatSession {
        val asked = mutableListOf<String>()
        override suspend fun ask(text: String, tools: ToolRunner): String {
            asked += text
            tool?.let { tools.run(ToolCall("1", it, JSONObject())) }
            error?.let { throw AiException(it) }
            return answer!!
        }
    }

    private val noTools = ToolRunner { "{}" }
    private val acts: (String) -> Boolean = { it in AssistantTools.CHANGES_STATE }

    private fun chain(vararg members: Pair<String, () -> ChatSession>) =
        FallbackChatSession(members.toList(), acts)

    @Test
    fun theChosenServiceAnswersAndNoBackupIsMade() = runBlocking {
        var made = 0
        val s = chain("Claude" to { Fake("c", "hi") }, "Groq" to { made++; Fake("g", "no") })
        assertEquals("hi", s.ask("q", noTools))
        assertEquals(0, made)
        assertNull(s.takeNote())
    }

    @Test
    fun aFailureHandsTheWordsToTheBackupAndSaysSo() = runBlocking {
        val claude = Fake("c", error = "Anthropic rate limit reached")
        val groq = Fake("g", "battery at 64 %")
        val s = chain("Claude" to { claude }, "Groq" to { groq })
        assertEquals("battery at 64 %", s.ask("battery?", noTools))
        assertEquals(listOf("battery?"), groq.asked)
        assertEquals("Answered by Groq: Claude failed (Anthropic rate limit reached).",
            s.takeNote())
        assertNull("the note is given once", s.takeNote())
    }

    @Test
    fun theConversationStaysWithTheBackupThatAnswered() = runBlocking {
        val claude = Fake("c", error = "rate limit")
        val groq = Fake("g", "ok")
        val s = chain("Claude" to { claude }, "Groq" to { groq })
        s.ask("one", noTools)
        s.ask("two", noTools)
        assertEquals(listOf("one"), claude.asked)
        assertEquals(listOf("one", "two"), groq.asked)
        assertEquals("g", s.model)
    }

    @Test
    fun aBackupThatCannotBeMadeIsOneMoreFailure() = runBlocking {
        val s = chain(
            "Claude" to { throw AiException("there is no API key for Claude") },
            "Gemini" to { Fake("m", "fine") },
        )
        assertEquals("fine", s.ask("q", noTools))
        assertTrue(s.takeNote()!!.contains("no API key for Claude"))
    }

    @Test
    fun whenEveryServiceFailsTheErrorSaysWhyForEach() = runBlocking {
        val s = chain("Claude" to { Fake("c", error = "key refused") },
            "Groq" to { Fake("g", error = "Could not reach Groq") })
        try {
            s.ask("q", noTools)
            fail("expected AiException")
        } catch (e: AiException) {
            assertEquals("every AI service failed: Claude failed (key refused); " +
                "Groq failed (Could not reach Groq)", e.message)
        }
    }

    @Test
    fun aTurnThatAlreadyActedIsNotRepeatedOnABackup() = runBlocking {
        val groq = Fake("g", "navigating")
        val s = chain("Claude" to { Fake("c", error = "connection reset", tool = "start_navigation") },
            "Groq" to { groq })
        try {
            s.ask("take me home", noTools)
            fail("expected AiException")
        } catch (e: AiException) {
            assertTrue(e.message!!, e.message!!.contains("already acted"))
        }
        assertEquals("the backup must not start navigation again", emptyList<String>(), groq.asked)
    }

    @Test
    fun aLookUpBeforeAFailureStillFallsBack() = runBlocking {
        val s = chain("Claude" to { Fake("c", error = "timed out", tool = "get_battery") },
            "Groq" to { Fake("g", "64 %") })
        assertEquals("64 %", s.ask("battery?", noTools))
    }

    @Test
    fun theActingToolsAreRealTools() {
        val names = AssistantTools.SPECS.map { it.name }.toSet()
        assertTrue(AssistantTools.CHANGES_STATE.toString(), names.containsAll(AssistantTools.CHANGES_STATE))
    }

    @Test
    fun onlyServicesWithADefaultModelCanBeBackups() {
        assertEquals(listOf("anthropic", "gemini", "groq", "mistral"),
            AiPresets.ALL.map { it.id }.filter { AiPresets.canBackUp(it) })
    }

    @Test
    fun theSavedBackupListIsCleaned() {
        assertEquals(listOf("groq", "mistral"),
            AiPrefs.cleanBackups(" groq,gemini,openai,groq,,nonsense,mistral", "gemini"))
        assertEquals(emptyList<String>(), AiPrefs.cleanBackups("", "anthropic"))
    }
}
