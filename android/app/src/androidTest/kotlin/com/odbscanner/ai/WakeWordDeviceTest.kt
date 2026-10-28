package com.odbscanner.ai

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.BeforeClass
import org.junit.Test
import org.junit.runner.RunWith
import org.vosk.Model
import org.vosk.Recognizer

/**
 * The wake word through the real Vosk engine on a device, without a
 * microphone: recorded speech (synthesised, 16 kHz mono, in the test assets)
 * is fed to the same grammar the service uses. The model is downloaded and
 * installed the way the app does it, so that path is exercised too.
 *
 * Run on an emulator: ./gradlew :app:connectedDebugAndroidTest
 */
@RunWith(AndroidJUnit4::class)
class WakeWordDeviceTest {

    companion object {
        private lateinit var model: Model

        @JvmStatic
        @BeforeClass
        fun load() {
            val ctx = InstrumentationRegistry.getInstrumentation().targetContext
            if (!WakeModel.installed(ctx)) runBlocking { WakeModel.download(ctx) {} }
            assertTrue(WakeModel.installed(ctx))
            model = Model(WakeModel.dir(ctx).path)
        }
    }

    /** What the spotter makes of a recording: Vosk's final result JSON. */
    private fun hear(asset: String, phrase: String = "hey scanner"): String {
        val bytes = InstrumentationRegistry.getInstrumentation().context.assets.open(asset)
            .use { it.readBytes() }
        val pcm = bytes.copyOfRange(44, bytes.size)                 // after the WAV header
        Recognizer(model, WakeWordService.SAMPLE_RATE, WakeMatch.grammar(phrase)).use { rec ->
            rec.setWords(true)
            var i = 0
            while (i < pcm.size) {
                val n = minOf(4096, pcm.size - i)
                rec.acceptWaveForm(pcm.copyOfRange(i, i + n), n)
                i += n
            }
            return rec.finalResult
        }
    }

    @Test
    fun thePhraseWakesIt() {
        val heard = hear("hey_scanner.wav")
        assertTrue(heard, WakeMatch.matches(heard, "hey scanner"))
    }

    @Test
    fun otherSpeechDoesNot() {
        val heard = hear("other_speech.wav")
        assertFalse(heard, WakeMatch.matches(heard, "hey scanner"))
    }

    @Test
    fun aDifferentPhraseIsNotWokenByThisOne() {
        val heard = hear("hey_scanner.wav", phrase = "okay car")
        assertFalse(heard, WakeMatch.matches(heard, "okay car"))
    }
}
