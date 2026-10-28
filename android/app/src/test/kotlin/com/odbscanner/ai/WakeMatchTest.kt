package com.odbscanner.ai

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.io.IOException
import java.nio.file.Files
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class WakeMatchTest {

    @Test
    fun grammarIsThePhraseOrAnythingElse() {
        val g = JSONArray(WakeMatch.grammar("Hey, Scanner!"))
        assertEquals("hey scanner", g.getString(0))
        assertEquals("[unk]", g.getString(1))
    }

    @Test
    fun onlyTheWholePhraseSaidClearlyWakes() {
        val clear = """{"result":[{"conf":0.98,"word":"hey"},{"conf":0.91,"word":"scanner"}],
            "text":"hey scanner"}"""
        assertTrue(WakeMatch.matches(clear, "hey scanner"))
        assertTrue(WakeMatch.matches("""{"text":"hey scanner"}""", "Hey Scanner"))
        // Mumbled: one word below the confidence floor.
        assertFalse(WakeMatch.matches(clear.replace("0.91", "0.42"), "hey scanner"))
        // Anything else, or nothing.
        assertFalse(WakeMatch.matches("""{"text":"[unk]"}""", "hey scanner"))
        assertFalse(WakeMatch.matches("""{"text":"hey"}""", "hey scanner"))
        assertFalse(WakeMatch.matches("""{"text":""}""", "hey scanner"))
        assertFalse(WakeMatch.matches("not json", "hey scanner"))
    }

    @Test
    fun unzipRefusesEntriesThatEscape() {
        val tmp = Files.createTempDirectory("wake").toFile()
        try {
            val good = File(tmp, "good.zip")
            ZipOutputStream(good.outputStream()).use { z ->
                z.putNextEntry(ZipEntry("model/am/final.mdl")); z.write(byteArrayOf(1, 2, 3)); z.closeEntry()
            }
            val into = File(tmp, "out").apply { mkdirs() }
            WakeModel.unzip(good, into)
            assertEquals(3L, File(into, "model/am/final.mdl").length())

            val evil = File(tmp, "evil.zip")
            ZipOutputStream(evil.outputStream()).use { z ->
                z.putNextEntry(ZipEntry("../escaped.txt")); z.write(byteArrayOf(9)); z.closeEntry()
            }
            try {
                WakeModel.unzip(evil, into)
                throw AssertionError("an escaping entry was unpacked")
            } catch (e: IOException) {
                assertFalse(File(tmp, "escaped.txt").exists())
            }
        } finally {
            tmp.deleteRecursively()
        }
    }
}
