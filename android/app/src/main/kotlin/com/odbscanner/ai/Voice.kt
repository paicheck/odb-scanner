package com.odbscanner.ai

import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.coroutines.resume

/**
 * Speech out: the phone's own text-to-speech, as navigation guidance -- it
 * ducks music and, with Android Auto connected, plays through the car's
 * speakers. British English.
 */
object Speaker {
    private val main = Handler(Looper.getMainLooper())
    private var tts: TextToSpeech? = null
    private var ready = false
    private val pending = mutableListOf<Pair<String, String>>()
    private val waiters = mutableMapOf<String, () -> Unit>()
    private var seq = 0

    private val attributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
        .build()

    private fun engine(context: Context): TextToSpeech {
        tts?.let { return it }
        val app = context.applicationContext
        lateinit var created: TextToSpeech
        created = TextToSpeech(app) { status ->
            main.post {
                ready = status == TextToSpeech.SUCCESS
                if (ready) {
                    created.language = Locale.UK
                    created.setAudioAttributes(attributes)
                    pending.forEach { (id, text) -> speakNow(app, id, text) }
                } else {
                    pending.forEach { (id, _) -> waiters.remove(id)?.invoke() }
                }
                pending.clear()
            }
        }
        created.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) = Unit
            override fun onDone(utteranceId: String?) = finished(app, utteranceId)
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) = finished(app, utteranceId)
        })
        tts = created
        return created
    }

    private fun finished(context: Context, id: String?) {
        main.post {
            id?.let { waiters.remove(it)?.invoke() }
            if (waiters.isEmpty()) Focus.release(context)
        }
    }

    private fun speakNow(context: Context, id: String, text: String) {
        Focus.duck(context)
        tts?.speak(text, TextToSpeech.QUEUE_ADD, Bundle(), id)
    }

    /** Say [text]; returns at once. */
    fun say(context: Context, text: String) {
        main.post { queue(context, text) {} }
    }

    /** Say [text] and wait until it has been spoken (or failed). */
    suspend fun sayAndWait(context: Context, text: String) {
        withTimeoutOrNull(60_000L + text.length * 120L) {
            suspendCancellableCoroutine { cont ->
                main.post { queue(context, text) { if (cont.isActive) cont.resume(Unit) } }
            }
        }
    }

    private fun queue(context: Context, text: String, done: () -> Unit) {
        val id = "u${seq++}"
        waiters[id] = done
        engine(context)
        if (ready) speakNow(context.applicationContext, id, text) else pending.add(id to text)
    }

    fun stop() {
        main.post { tts?.stop() }
    }
}

/** Audio focus while speaking: music ducks, then comes back. */
private object Focus {
    private var request: AudioFocusRequest? = null

    fun duck(context: Context) {
        if (request != null) return
        val am = context.getSystemService(AudioManager::class.java) ?: return
        val r = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
            .setAudioAttributes(AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .build()
        am.requestAudioFocus(r)
        request = r
    }

    fun release(context: Context) {
        val r = request ?: return
        context.getSystemService(AudioManager::class.java)?.abandonAudioFocusRequest(r)
        request = null
    }
}

/**
 * Speech in: one question at a time through the phone's speech recogniser
 * (Google's on most phones). Main thread, as SpeechRecognizer requires.
 */
object Listener {
    /** The words heard, or null (nothing understood, timed out, not available). */
    suspend fun listenOnce(context: Context, timeoutMs: Long = 15_000L): String? =
        withContext(Dispatchers.Main) {
            if (!SpeechRecognizer.isRecognitionAvailable(context)) {
                throw AiException("this phone has no speech recognition")
            }
            withTimeoutOrNull(timeoutMs) {
                suspendCancellableCoroutine { cont ->
                    val sr = SpeechRecognizer.createSpeechRecognizer(context.applicationContext)
                    val once = AtomicBoolean(false)
                    fun finish(text: String?) {
                        if (!once.compareAndSet(false, true)) return
                        sr.destroy()
                        if (cont.isActive) cont.resume(text)
                    }
                    cont.invokeOnCancellation {
                        Handler(Looper.getMainLooper()).post {
                            if (once.compareAndSet(false, true)) sr.destroy()
                        }
                    }
                    sr.setRecognitionListener(object : RecognitionListener {
                        override fun onResults(results: Bundle?) = finish(
                            results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                                ?.firstOrNull()?.trim()?.takeIf { it.isNotEmpty() })
                        override fun onError(error: Int) = finish(null)
                        override fun onReadyForSpeech(params: Bundle?) = Unit
                        override fun onBeginningOfSpeech() = Unit
                        override fun onRmsChanged(rmsdB: Float) = Unit
                        override fun onBufferReceived(buffer: ByteArray?) = Unit
                        override fun onEndOfSpeech() = Unit
                        override fun onPartialResults(partialResults: Bundle?) = Unit
                        override fun onEvent(eventType: Int, params: Bundle?) = Unit
                    })
                    sr.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                        putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                            RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                        putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-GB")
                        putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                    })
                }
            }
        }
}

/** Listen, ask, answer out loud: one spoken exchange with the assistant. */
object VoiceAssistant {
    enum class State { IDLE, LISTENING, THINKING, SPEAKING }

    private val _state = MutableStateFlow(State.IDLE)
    val state: StateFlow<State> = _state

    /** One exchange; the spoken answer, or null when nothing was heard. */
    suspend fun converse(context: Context): String? {
        if (_state.value != State.IDLE) return null
        try {
            Speaker.stop()
            beep()
            _state.value = State.LISTENING
            val heard = try {
                Listener.listenOnce(context)
            } catch (e: AiException) {
                _state.value = State.SPEAKING
                Speaker.sayAndWait(context, "Sorry, ${e.message}.")
                return null
            }
            if (heard.isNullOrBlank()) {
                _state.value = State.SPEAKING
                Speaker.sayAndWait(context, "Sorry, I didn't catch that.")
                return null
            }
            _state.value = State.THINKING
            val answer = Assistant.ask(context, heard)
            _state.value = State.SPEAKING
            Speaker.sayAndWait(context, answer)
            return answer
        } finally {
            _state.value = State.IDLE
        }
    }

    /** The cue that it is listening now. */
    fun beep() {
        runCatching {
            val tone = ToneGenerator(AudioManager.STREAM_NOTIFICATION, 70)
            tone.startTone(ToneGenerator.TONE_PROP_BEEP, 150)
            Handler(Looper.getMainLooper()).postDelayed({ tone.release() }, 400)
        }
    }
}
