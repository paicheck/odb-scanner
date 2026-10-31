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
import android.os.SystemClock
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.coroutineContext
import java.util.Locale
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.coroutines.resume

/**
 * Speech out: the phone's own text-to-speech, as navigation guidance -- it
 * ducks music and, with Android Auto connected, plays through the car's
 * speakers. British English, or the voice chosen in AI & Voice Setup.
 *
 * What is said, and in what order, is decided by a SpeechQueue: the
 * assistant's answers belong to a turn and are dropped (never played late)
 * once that turn is stopped or replaced; [stopAssistant] silences only the
 * assistant, so guidance and alerts carry on; an urgent cue still cuts in.
 * The engine is given one utterance at a time.
 */
object Speaker {
    private val main = Handler(Looper.getMainLooper())
    private var tts: TextToSpeech? = null
    private var ready = false
    private var app: Context? = null
    private val queue = SpeechQueue { turn -> VoiceAssistant.isCurrentTurn(turn) }
    private val waiters = mutableMapOf<String, (AiError?) -> Unit>()
    private val startedAt = mutableMapOf<String, Long>()
    private val seq = AtomicInteger()            // utterances are made on any thread
    private var given = 0                        // handed to the engine (main thread)
    private var customVoice = false             // a chosen voice is in use
    private val replayed = mutableSetOf<String>()
    private val _note = MutableStateFlow<String?>(null)
    /** Shown, not said: the chosen voice failed and the phone's own is speaking. */
    val note: StateFlow<String?> = _note

    private val attributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
        .build()

    private fun engine(context: Context): TextToSpeech {
        tts?.let { return it }
        val app = context.applicationContext
        this.app = app
        lateinit var created: TextToSpeech
        created = TextToSpeech(app) { status ->
            main.post {
                ready = status == TextToSpeech.SUCCESS
                if (ready) {
                    created.language = locale(app)
                    created.setAudioAttributes(attributes)
                    applyVoice(app, created)
                    queue.playing?.let { speakNow(app, it) }
                } else {
                    // Everything waiting is told why; the next utterance tries again.
                    val why = AiErrors.fromTts(TextToSpeech.ERROR).copy(
                        message = "The phone's speech engine would not start")
                    runCatching { created.shutdown() }
                    if (tts === created) tts = null
                    run(app, queue.clear(), why)
                }
            }
        }
        created.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {
                main.post { utteranceId?.let { startedAt[it] = SystemClock.elapsedRealtime() } }
            }
            override fun onDone(utteranceId: String?) = finished(app, utteranceId, null)
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) =
                finished(app, utteranceId, AiErrors.fromTts(TextToSpeech.ERROR))
            override fun onError(utteranceId: String?, errorCode: Int) =
                finished(app, utteranceId, AiErrors.fromTts(errorCode))
            // Cut off by a Halt: its waiter must not wait on, nor music stay ducked.
            override fun onStop(utteranceId: String?, interrupted: Boolean) =
                finished(app, utteranceId, null)
        })
        tts = created
        return created
    }

    private fun locale(context: Context): Locale =
        if (AiPrefs.load(context).language == "en-US") Locale.US else Locale.UK

    private fun applyVoice(context: Context, engine: TextToSpeech) {
        val name = AiPrefs.load(context).ttsVoice
        if (name.isBlank()) return
        runCatching { engine.voices?.firstOrNull { it.name == name } }.getOrNull()
            ?.let { engine.voice = it; customVoice = true }
    }

    private fun finished(context: Context, id: String?, error: AiError?) {
        main.post {
            // The chosen voice failed for want of a connection or its data:
            // say it again, once, in the phone's own voice, and show why.
            if (error != null && id != null && customVoice && id !in replayed &&
                SpeechFallback.voiceShouldFallBack(error)) {
                replayed += id
                customVoice = false
                tts?.language = locale(context)
                _note.value = "The chosen voice could not speak (${error.category}): using " +
                    "the phone's own voice"
                queue.playing?.takeIf { it.id == id }?.let { speakNow(context, it); return@post }
            }
            id?.let { waiters.remove(it)?.invoke(error); startedAt.remove(it) }
            run(context, queue.done(id), null)
            if (queue.idle) Focus.release(context)
        }
    }

    private fun run(context: Context, actions: List<SpeechAction>, error: AiError?) {
        for (a in actions) when (a) {
            is SpeechAction.Play -> if (ready) speakNow(context, a.u)
            SpeechAction.Halt -> tts?.stop()
            is SpeechAction.Dropped -> a.ids.forEach { waiters.remove(it)?.invoke(error) }
        }
        if (queue.idle) Focus.release(context)
    }

    private fun speakNow(context: Context, u: Utterance) {
        Focus.duck(context)
        tts?.setSpeechRate(AiPrefs.load(context).speechRate)
        tts?.speak(u.text, TextToSpeech.QUEUE_FLUSH, Bundle(), u.id)
        val mine = ++given
        main.postDelayed({ if (given == mine) overdue(context, u) }, SpeechQueue.limitMs(u.text))
    }

    /** The engine never finished [u] (SpeechQueue.overdue): a fresh one says the rest. */
    private fun overdue(context: Context, u: Utterance) {
        val actions = queue.overdue(u.id)
        if (actions.isEmpty()) return
        val dead = tts
        tts = null
        ready = false
        runCatching { dead?.stop(); dead?.shutdown() }
        startedAt.remove(u.id)
        waiters.remove(u.id)?.invoke(AiError(AiErrorCategory.TIMEOUT, AiErrors.SPEECH_OUT,
            "The phone's speech engine stopped responding"))
        run(context, actions, null)
        if (!queue.idle) engine(context)
    }

    private fun submit(context: Context, u: Utterance, done: (AiError?) -> Unit) {
        waiters[u.id] = done
        engine(context)
        run(context.applicationContext, queue.add(u), null)
    }

    private fun utterance(text: String, owner: SpeechOwner, turn: Int, urgent: Boolean) =
        Utterance("u${seq.getAndIncrement()}", text, owner, turn, urgent)

    /**
     * Say [text]; returns at once. [interrupt]: cut off whatever is being said
     * -- for a turn that is close, which must not wait behind a long answer.
     * [owner]/[turn]: the assistant's words belong to a turn (see SpeechQueue).
     */
    fun say(context: Context, text: String, interrupt: Boolean = false,
            owner: SpeechOwner = SpeechOwner.APP, turn: Int = 0) {
        main.post { submit(context, utterance(text, owner, turn, interrupt)) {} }
    }

    /**
     * Say [text] and wait until it has been spoken, dropped or stopped: null,
     * or the failure (the engine would not start, the voice is not
     * installed...). Bounded; cancelling the caller stops waiting.
     */
    suspend fun sayAndWait(context: Context, text: String, owner: SpeechOwner = SpeechOwner.APP,
                           turn: Int = 0): AiError? =
        timed(context, text, owner, turn).error

    /** As sayAndWait, with when it started and finished (the setup screen's test). */
    data class Spoken(val error: AiError?, val startMs: Long?, val totalMs: Long)

    suspend fun timed(context: Context, text: String, owner: SpeechOwner = SpeechOwner.APP,
                      turn: Int = 0): Spoken {
        val begun = SystemClock.elapsedRealtime()
        val u = utterance(text, owner, turn, false)
        var started: Long? = null
        val err = withTimeoutOrNull(SpeechQueue.limitMs(text)) {
            suspendCancellableCoroutine<AiError?> { cont ->
                main.post {
                    submit(context, u) { e ->
                        started = startedAt[u.id]
                        if (cont.isActive) cont.resume(e)
                    }
                }
                cont.invokeOnCancellation { main.post { waiters.remove(u.id) } }
            }
        } ?: AiError(AiErrorCategory.TIMEOUT, AiErrors.SPEECH_OUT,
            "The speech did not finish in time").also { main.post { waiters.remove(u.id) } }
        val now = SystemClock.elapsedRealtime()
        return Spoken(err, started?.let { it - begun }, now - begun)
    }

    /**
     * Wait until none of [turn]'s speech is left -- a streamed answer said
     * sentence by sentence. Bounded; cancelling the caller stops waiting.
     */
    suspend fun drain(turn: Int, limitMs: Long = SPEECH_LIMIT_MS) {
        withTimeoutOrNull(limitMs) {
            while (withContext(Dispatchers.Main) { queue.has(turn) }) delay(100)
        }
    }

    /** Silence the assistant: its answer stops, and nothing of it is said later. */
    fun stopAssistant() {
        main.post { app?.let { run(it, queue.stopAssistant(), null) } }
    }

    /** Silence everything (the navigation voice muted). */
    fun stop() {
        main.post { app?.let { run(it, queue.clear(), null) } }
    }

    /** The engine and its British voices, for the setup screen: (engine, voices). */
    fun voices(context: Context): Pair<String, List<String>> {
        val e = engine(context)
        val names = runCatching {
            e.voices.orEmpty().filter { it.locale.language == "en" }
                .sortedWith(compareBy({ it.locale.country != "GB" }, { it.name }))
                .map { it.name + (if (it.isNetworkConnectionRequired) " (online)" else "") }
        }.getOrDefault(emptyList())
        return (e.defaultEngine ?: "unknown") to names
    }

    const val SPEECH_LIMIT_MS = SpeechQueue.LIMIT_MS
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

/** What listening gave: the words, or why there were none. */
sealed class ListenResult {
    abstract val ms: Long
    data class Heard(val text: String, override val ms: Long, val note: String? = null) :
        ListenResult()
    data class Failed(val error: AiError, override val ms: Long) : ListenResult()
}

/**
 * Speech in: one question at a time through the phone's speech recogniser
 * (Google's on most phones; with [offline], on the phone where it can).
 * Main thread, as SpeechRecognizer requires. Cancelling the caller destroys
 * the recogniser and frees the microphone.
 */
object Listener {
    /** The words heard, or null (nothing understood, timed out, not available). */
    suspend fun listenOnce(context: Context, timeoutMs: Long = 15_000L): String? =
        (listen(context, timeoutMs, AiPrefs.load(context).sttOffline) as? ListenResult.Heard)?.text

    /**
     * Listen as the settings say, and fall back once where it helps: online
     * recognition that could not connect is tried again on the phone (more
     * private, so always allowed); on-the-phone that lacks the language goes
     * online only with your consent (SpeechFallback). [again] is said before
     * listening the second time -- the words were lost with the first.
     */
    suspend fun hear(context: Context, s: AiSettings, timeoutMs: Long, again: suspend () -> Unit,
                     onEndOfSpeech: () -> Unit = {}): ListenResult {
        val first = listen(context, timeoutMs, s.sttOffline, s.language, onEndOfSpeech)
        val err = (first as? ListenResult.Failed)?.error ?: return first
        val local = Diagnostics.recogniser(context).second == true
        val offline = SpeechFallback.recogniserRetry(err, s.sttOffline, local, s.sttOnlineFallback)
            ?: return first
        again()
        return when (val second = listen(context, timeoutMs, offline, s.language, onEndOfSpeech)) {
            is ListenResult.Heard -> second.copy(note = if (offline)
                "Recognised on the phone: the online recogniser could not be reached"
            else "Recognised online: the phone's own recogniser could not")
            is ListenResult.Failed -> second
        }
    }

    suspend fun listen(context: Context, timeoutMs: Long = 15_000L, offline: Boolean = false,
                       language: String = "en-GB", onEndOfSpeech: () -> Unit = {}): ListenResult =
        withContext(Dispatchers.Main) {
            val begun = SystemClock.elapsedRealtime()
            fun took() = SystemClock.elapsedRealtime() - begun
            if (!SpeechRecognizer.isRecognitionAvailable(context)) {
                return@withContext ListenResult.Failed(AiError(AiErrorCategory.UNSUPPORTED_CAPABILITY,
                    AiErrors.RECOGNISER, "This phone has no speech recognition"), took())
            }
            withTimeoutOrNull(timeoutMs) {
                suspendCancellableCoroutine<ListenResult> { cont ->
                    val sr = SpeechRecognizer.createSpeechRecognizer(context.applicationContext)
                    val once = AtomicBoolean(false)
                    fun finish(r: ListenResult) {
                        if (!once.compareAndSet(false, true)) return
                        sr.destroy()
                        if (cont.isActive) cont.resume(r)
                    }
                    cont.invokeOnCancellation {
                        Handler(Looper.getMainLooper()).post {
                            if (once.compareAndSet(false, true)) {
                                runCatching { sr.cancel() }
                                sr.destroy()
                            }
                        }
                    }
                    sr.setRecognitionListener(object : RecognitionListener {
                        override fun onResults(results: Bundle?) {
                            val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                                ?.firstOrNull()?.trim()?.takeIf { it.isNotEmpty() }
                            finish(if (text != null) ListenResult.Heard(text, took())
                            else ListenResult.Failed(AiErrors.fromRecognizer(
                                SpeechRecognizer.ERROR_NO_MATCH), took()))
                        }
                        override fun onError(error: Int) =
                            finish(ListenResult.Failed(AiErrors.fromRecognizer(error), took()))
                        override fun onReadyForSpeech(params: Bundle?) = Unit
                        override fun onBeginningOfSpeech() = Unit
                        override fun onRmsChanged(rmsdB: Float) = Unit
                        override fun onBufferReceived(buffer: ByteArray?) = Unit
                        override fun onEndOfSpeech() = onEndOfSpeech()
                        override fun onPartialResults(partialResults: Bundle?) = Unit
                        override fun onEvent(eventType: Int, params: Bundle?) = Unit
                    })
                    sr.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                        putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                            RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                        putExtra(RecognizerIntent.EXTRA_LANGUAGE, language)
                        putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                        if (offline) putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
                    })
                }
            } ?: ListenResult.Failed(AiError(AiErrorCategory.TIMEOUT, AiErrors.RECOGNISER,
                "The speech recogniser gave nothing within ${timeoutMs / 1000} s"), took())
        }
}

/**
 * The voice assistant: one turn at a time, always stoppable. The
 * authoritative state is a VoiceMachine; this owns the turn's job and drives
 * it. The phone dialog, the car screen, the wake word and the Google Maps
 * question all go through here.
 *
 *  * [stop]: the turn's job is cancelled -- the recogniser is destroyed, the
 *    AI request aborted (Assistant/Cancellable), the assistant's speech
 *    dropped (Speaker.stopAssistant) -- and the state goes INTERRUPTED, then
 *    IDLE. Repeated stops do nothing more.
 *  * [bargeIn]: the same, then a new turn listens at once. Saying the wake
 *    phrase while the assistant thinks or talks does it hands-free (the
 *    wake word listens again from THINKING on).
 *  * A failure ends the turn in ERROR: a few words are said (never the
 *    provider's detail, which is shown), and the state settles to IDLE.
 *  * Every callback names its turn: a late recognition, answer or
 *    "spoken" from a stopped turn is refused, and its speech never plays.
 */
object VoiceAssistant {
    private const val INTERRUPTED_MS = 500L
    const val ERROR_HOLD_MS = 6_000L
    const val LISTEN_MS = 15_000L

    private val machine = VoiceMachine()
    private val lock = Any()
    private val _status = MutableStateFlow(VoiceStatus())
    val status: StateFlow<VoiceStatus> = _status
    private val _state = MutableStateFlow(VoiceState.IDLE)
    val state: StateFlow<VoiceState> = _state

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var job: Job? = null
    @Volatile var appContext: Context? = null
        private set

    private fun fire(e: VoiceEvent): Boolean = synchronized(lock) {
        val ok = machine.on(e)
        if (ok) {
            _status.value = machine.status
            _state.value = machine.status.state
        }
        ok
    }

    /** Whether [turn]'s speech may still play: it is the latest, and not stopped. */
    fun isCurrentTurn(turn: Int): Boolean = synchronized(lock) {
        machine.status.turn == turn && machine.status.state != VoiceState.INTERRUPTED
    }

    private fun remember(context: Context) {
        if (appContext == null) appContext = context.applicationContext
    }

    /** Start a spoken turn from [source]; the turn's number, or null when one is under way. */
    fun start(context: Context, source: VoiceSource): Int? {
        remember(context)
        if (!fire(VoiceEvent.Start(source))) return null
        val turn = _status.value.turn
        job = scope.launch { voiceTurn(context.applicationContext, turn) }
        return turn
    }

    /** The last question asked, for Retry. */
    @Volatile var lastQuestion: String? = null
        private set

    /** A typed question: thinking only (and spoken if [speak]); Stop ends it too. */
    fun ask(context: Context, text: String, speak: Boolean = false): Int? {
        remember(context)
        if (!fire(VoiceEvent.Start(VoiceSource.TYPED, typed = true))) return null
        val turn = _status.value.turn
        val app = context.applicationContext
        lastQuestion = text
        job = scope.launch {
            val trace = TurnTrace(VoiceSource.TYPED).also { it.question = text }
            traced(app, trace) { answerTurn(app, turn, text, speak, trace) }
        }
        return turn
    }

    /**
     * Ask the last question again, after a failure -- spoken, without
     * listening again (the words were heard; it was the answer that failed).
     */
    fun retry(context: Context): Int? {
        val q = lastQuestion ?: return start(context, VoiceSource.PHONE)
        return ask(context, q, speak = true)
    }

    /** One turn's record for the interaction history, filled in as it goes. */
    /** Run [block] and record its trace, however it ends (a stop included). */
    private suspend fun traced(context: Context, trace: TurnTrace, block: suspend () -> Unit) {
        try {
            block()
        } catch (e: kotlinx.coroutines.CancellationException) {
            trace.stopped()
            throw e
        } finally {
            val record = trace.build()
            scope.launch(Dispatchers.IO) { History.add(context, record) }
        }
    }

    /** End whatever the assistant is doing, at once. */
    fun stop() {
        val before = synchronized(lock) { machine.status }
        if (!before.active) {
            Speaker.stopAssistant()               // a Repeat still being said
            return
        }
        if (!fire(VoiceEvent.Stop)) return
        job?.cancel()
        Speaker.stopAssistant()
        val turn = before.turn
        scope.launch {
            delay(INTERRUPTED_MS)
            fire(VoiceEvent.Settle(turn))
        }
    }

    /**
     * Say the last answer again (Repeat) as the assistant's speech, so Stop,
     * Speak and a new question silence it like the answer itself.
     */
    fun repeat(context: Context, text: String) {
        Speaker.say(context, text, owner = SpeechOwner.ASSISTANT, turn = _status.value.turn)
    }

    /** The driver speaks over the assistant: stop it and listen to them. */
    fun bargeIn(context: Context, source: VoiceSource): Int? {
        remember(context)
        val old = job
        if (!fire(VoiceEvent.BargeIn(source))) return start(context, source)
        old?.cancel()
        Speaker.stopAssistant()
        val turn = _status.value.turn
        job = scope.launch {
            old?.join()                   // let the old turn free the microphone first
            voiceTurn(context.applicationContext, turn)
        }
        return turn
    }

    /** The wake phrase: a new question, or -- while it thinks or talks -- a barge-in. */
    fun wake(context: Context): Int? =
        if (_status.value.active) bargeIn(context, VoiceSource.WAKE_WORD)
        else start(context, VoiceSource.WAKE_WORD)

    /** The app left the screen: a phone turn still listening lets go of the microphone. */
    fun onAppBackground() {
        val turn = _status.value.turn
        if (!fire(VoiceEvent.Background)) return
        job?.cancel()
        scope.launch {
            delay(INTERRUPTED_MS)
            fire(VoiceEvent.Settle(turn))
        }
    }

    /**
     * Ask [question] out loud and listen for the answer -- the Google Maps
     * yes/no -- as a turn of its own, so it cannot overlap another and Stop
     * ends it. The words heard, or null.
     */
    suspend fun question(context: Context, question: String, listenMs: Long): String? {
        remember(context)
        if (!fire(VoiceEvent.Start(VoiceSource.PROMPT))) return null
        val turn = _status.value.turn
        job = coroutineContext[Job]
        withContext(Dispatchers.Main) { WakeWordService.pause() }
        try {
            Speaker.sayAndWait(context, question, SpeechOwner.ASSISTANT, turn)
            if (!isCurrentTurn(turn)) return null
            beep()
            val s = AiPrefs.load(context)
            val r = Listener.listen(context, listenMs, s.sttOffline, s.language) {
                fire(VoiceEvent.EndOfSpeech(turn))
            }
            if (fire(VoiceEvent.Heard(turn))) fire(VoiceEvent.Answered(turn, speak = false))
            return (r as? ListenResult.Heard)?.text
        } finally {
            if (_status.value.turn == turn && !_status.value.free) {
                fire(VoiceEvent.Stop)
                fire(VoiceEvent.Settle(turn))
            }
            withContext(Dispatchers.Main + NonCancellable) { WakeWordService.resume() }
        }
    }

    /**
     * Run a setup-screen test as a turn of its own: never over a
     * conversation, and Stop ends it (its speech is the assistant's, its job
     * the caller's). [mic]: the test listens, so the wake word lets go.
     * Null when something else is under way.
     */
    suspend fun <T> test(context: Context, mic: Boolean, block: suspend (turn: Int) -> T): T? {
        remember(context)
        if (!fire(VoiceEvent.Start(VoiceSource.TEST, typed = true))) return null
        val turn = _status.value.turn
        job = coroutineContext[Job]
        if (mic) withContext(Dispatchers.Main) { WakeWordService.pause() }
        try {
            return block(turn)
        } finally {
            if (_status.value.turn == turn && !_status.value.free) {
                fire(VoiceEvent.Stop)
                fire(VoiceEvent.Settle(turn))
            }
            if (mic) withContext(Dispatchers.Main + NonCancellable) { WakeWordService.resume() }
        }
    }

    private suspend fun voiceTurn(context: Context, turn: Int) {
        val trace = TurnTrace(_status.value.source ?: VoiceSource.PHONE)
        traced(context, trace) { listenAndAnswer(context, turn, trace) }
    }

    private suspend fun listenAndAnswer(context: Context, turn: Int, trace: TurnTrace) {
        withContext(Dispatchers.Main) { WakeWordService.pause() }   // one user of the microphone
        try {
            Speaker.stopAssistant()
            beep()
            val r = Listener.hear(context, AiPrefs.load(context), LISTEN_MS, again = {
                Speaker.sayAndWait(context, "Say that again.", SpeechOwner.ASSISTANT, turn)
                beep()
            }) { fire(VoiceEvent.EndOfSpeech(turn)) }
            trace.sttMs = r.ms
            when (r) {
                is ListenResult.Failed -> {
                    trace.failed(r.error, "recognition")
                    fail(context, turn, r.error, speak = true)
                }
                is ListenResult.Heard -> if (fire(VoiceEvent.Heard(turn))) {
                    trace.question = r.text
                    lastQuestion = r.text
                    // Listening is over: the wake phrase may now cut in.
                    withContext(Dispatchers.Main) { WakeWordService.resume() }
                    answerTurn(context, turn, r.text, speak = true, trace)
                }
            }
        } finally {
            // A newer turn (a barge-in) manages the microphone itself.
            if (_status.value.turn == turn) {
                withContext(Dispatchers.Main + NonCancellable) { WakeWordService.resume() }
            }
        }
    }

    /**
     * Ask, and speak the answer. Where the service streams, each sentence is
     * spoken as soon as it is complete -- in order, as the assistant's
     * speech for this turn, so a stop or a barge-in drops whatever is left.
     */
    private suspend fun answerTurn(context: Context, turn: Int, text: String, speak: Boolean,
                                   trace: TurnTrace) {
        val begun = SystemClock.elapsedRealtime()
        val chunker = SentenceChunker()
        var streamed = false
        val onText: ((String) -> Unit)? = if (!speak) null else { delta ->
            val sentences = chunker.feed(delta)
            if (sentences.isNotEmpty() && isCurrentTurn(turn)) {
                if (!streamed) {
                    streamed = true
                    trace.firstWordMs = SystemClock.elapsedRealtime() - begun
                    fire(VoiceEvent.Answered(turn, speak = true))
                }
                sentences.forEach { Speaker.say(context, it, owner = SpeechOwner.ASSISTANT, turn = turn) }
            }
        }
        val reply = Assistant.answer(context, text,
            driving = _status.value.source == VoiceSource.CAR, onText = onText)
        trace.aiMs = SystemClock.elapsedRealtime() - begun
        if (trace.firstWordMs == null) trace.firstWordMs = trace.aiMs
        reply.error?.let {
            trace.failed(it, "AI")
            return fail(context, turn, it, speak, reply.spoken)
        }
        trace.answered(Assistant.active.value, Assistant.lastModel)
        if (!streamed && (!fire(VoiceEvent.Answered(turn, speak)) || !speak)) return
        if (!speak) return
        // What is left to say: the rest of a streamed answer, or all of it.
        val rest = if (streamed) chunker.flush() else reply.spoken
        val err = if (rest.isNullOrBlank()) {
            val t0 = SystemClock.elapsedRealtime()
            Speaker.drain(turn)
            trace.ttsMs = SystemClock.elapsedRealtime() - t0
            null
        } else {
            val spoken = Speaker.timed(context, rest, SpeechOwner.ASSISTANT, turn)
            trace.ttsMs = spoken.totalMs
            spoken.error
        }
        if (err != null) {
            trace.failed(err, "speech")
            fail(context, turn, err, speak = false)
        } else fire(VoiceEvent.Spoken(turn))
    }

    /** End [turn] in ERROR: a few words said (unless it was the speech that failed). */
    private fun fail(context: Context, turn: Int, error: AiError, speak: Boolean,
                     said: String = error.spoken) {
        if (!fire(VoiceEvent.Failed(turn, error))) return
        if (error.provider == AiErrors.SPEECH_OUT) Speaker.stopAssistant()   // a stuck voice
        else if (speak) {
            Speaker.say(context, said, owner = SpeechOwner.ASSISTANT, turn = turn)
        }
        scope.launch {
            delay(ERROR_HOLD_MS)
            fire(VoiceEvent.Settle(turn))
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
