package com.odbscanner.ai

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.odbscanner.MainActivity
import com.odbscanner.route.PhoneLocation
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.RecognitionListener
import org.vosk.android.SpeechService
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.zip.ZipInputStream

/**
 * The wake word's speech model: Vosk's small English model (~40 MB),
 * downloaded once when the wake word is first turned on, kept in the app's
 * private files. Recognition runs on the phone; no audio leaves it.
 */
object WakeModel {
    const val URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
    const val NAME = "vosk-model-small-en-us-0.15"
    private const val MIN_BYTES = 30_000_000L     // a truncated download is not a model

    fun dir(context: Context) = File(context.filesDir, "wake/$NAME")

    fun installed(context: Context) = File(dir(context), "am/final.mdl").isFile

    /** Download and unpack; [progress] gets 0..100. Throws AiException on failure. */
    suspend fun download(context: Context, progress: (Int) -> Unit) = withContext(Dispatchers.IO) {
        val root = File(context.filesDir, "wake").apply { mkdirs() }
        val zip = File(context.cacheDir, "$NAME.zip")
        try {
            val conn = URL(URL).openConnection() as HttpURLConnection
            try {
                conn.connectTimeout = 20_000
                conn.readTimeout = 60_000
                if (conn.responseCode != 200) throw AiException("model download failed (HTTP ${conn.responseCode})")
                val total = conn.contentLengthLong
                conn.inputStream.use { input ->
                    zip.outputStream().use { out ->
                        val buf = ByteArray(64 * 1024)
                        var done = 0L
                        while (true) {
                            val n = input.read(buf)
                            if (n < 0) break
                            out.write(buf, 0, n)
                            done += n
                            if (total > 0) progress((done * 90 / total).toInt())
                        }
                    }
                }
            } finally {
                conn.disconnect()
            }
            if (zip.length() < MIN_BYTES) throw AiException("the model download was incomplete")
            val staging = File(root, "$NAME.partial").apply { deleteRecursively(); mkdirs() }
            unzip(zip, staging)
            val unpacked = File(staging, NAME).takeIf { it.isDirectory } ?: staging
            dir(context).deleteRecursively()
            if (!unpacked.renameTo(dir(context))) throw AiException("could not install the model")
            staging.deleteRecursively()
            progress(100)
        } catch (e: IOException) {
            throw AiException("model download failed: ${e.message}", e)
        } finally {
            zip.delete()
        }
    }

    /** Unzip, refusing any entry that would land outside [into] (zip slip). */
    fun unzip(zip: File, into: File) {
        val base = into.canonicalPath + File.separator
        ZipInputStream(zip.inputStream().buffered()).use { zin ->
            while (true) {
                val e = zin.nextEntry ?: break
                val out = File(into, e.name)
                if (!out.canonicalPath.startsWith(base)) throw IOException("bad entry ${e.name}")
                if (e.isDirectory) out.mkdirs()
                else {
                    out.parentFile?.mkdirs()
                    out.outputStream().use { zin.copyTo(it) }
                }
            }
        }
    }
}

/** Is a Vosk result the wake phrase, said clearly enough? Pure, so it is unit-tested. */
object WakeMatch {
    const val MIN_CONF = 0.7

    /** The grammar Vosk listens with: the phrase, or anything else ([unk]). */
    fun grammar(phrase: String): String = JSONArray(listOf(normalise(phrase), "[unk]")).toString()

    fun normalise(phrase: String) =
        phrase.lowercase().replace(Regex("[^a-z ]"), " ").split(' ').filter { it.isNotBlank() }
            .joinToString(" ")

    fun matches(resultJson: String, phrase: String, minConf: Double = MIN_CONF): Boolean {
        val r = runCatching { JSONObject(resultJson) }.getOrNull() ?: return false
        if (normalise(r.optString("text")) != normalise(phrase)) return false
        val words = r.optJSONArray("result") ?: return true      // no word detail: text match
        if (words.length() == 0) return true
        return (0 until words.length()).all { words.optJSONObject(it)?.optDouble("conf", 0.0) ?: 0.0 >= minConf }
    }
}

/**
 * Always listening for the wake phrase, while the driver has it on. A
 * foreground service of type microphone (the notification says it is
 * listening, with Stop): Vosk spots the phrase on the phone; then the mic is
 * handed to the speech recogniser for the question, the answer is spoken,
 * and spotting resumes.
 */
class WakeWordService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var model: Model? = null
    private var speech: SpeechService? = null
    private var phrase = "hey scanner"

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            AiPrefs.save(this, AiPrefs.load(this).copy(wakeWord = false))
            stopSelf()
            return START_NOT_STICKY
        }
        phrase = AiPrefs.load(this).wakePhrase
        running = this
        ensureChannel()
        // Microphone, and location too when allowed: a trip planned by voice
        // with the phone out of sight still needs the phone's position, and
        // only a service started from the open app may read it.
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or
                (if (PhoneLocation.permitted(this)) ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else 0)
        } else 0
        try {
            ServiceCompat.startForeground(this, NOTIFICATION_ID, notification("Starting…"), type)
        } catch (e: Exception) {
            Log.w(TAG, "cannot listen in the background", e)
            stopSelf()
            return START_NOT_STICKY
        }
        if (!WakeModel.installed(this)) {
            _status.value = "Download the wake-word model first"
            stopSelf()
            return START_NOT_STICKY
        }
        if (model == null) {
            scope.launch {
                model = runCatching { withContext(Dispatchers.IO) { Model(WakeModel.dir(this@WakeWordService).path) } }
                    .onFailure { Log.w(TAG, "model failed to load", it) }.getOrNull()
                if (model == null) {
                    _status.value = "The wake-word model would not load; download it again"
                    stopSelf()
                } else spot()
            }
        }
        return START_STICKY
    }

    private fun spot() {
        val m = model ?: return
        speech?.shutdown()
        speech = try {
            val rec = Recognizer(m, SAMPLE_RATE, WakeMatch.grammar(phrase)).apply { setWords(true) }
            SpeechService(rec, SAMPLE_RATE).also { it.startListening(listener) }
        } catch (e: IOException) {
            Log.w(TAG, "microphone unavailable", e)
            _status.value = "Microphone unavailable"
            null
        }
        if (speech != null) {
            _status.value = "Listening for \"$phrase\""
            notify("Say \"$phrase\" and ask")
        }
    }

    private val listener = object : RecognitionListener {
        override fun onResult(hypothesis: String?) {
            if (hypothesis != null && WakeMatch.matches(hypothesis, phrase)) wake()
        }
        override fun onPartialResult(hypothesis: String?) = Unit
        override fun onFinalResult(hypothesis: String?) = Unit
        override fun onError(exception: Exception?) {
            Log.w(TAG, "spotting stopped", exception)
            _status.value = "Listening stopped (${exception?.message ?: "error"})"
        }
        override fun onTimeout() = Unit
    }

    /** The phrase was heard: take the question and answer it (converse pauses spotting). */
    private fun wake() {
        pauseSpotting()
        scope.launch {
            _status.value = "Heard \"$phrase\""
            notify("Listening…")
            runCatching { VoiceAssistant.converse(this@WakeWordService) }
        }
    }

    /** Give up the microphone (a question is being taken). */
    private fun pauseSpotting() {
        speech?.shutdown()
        speech = null
    }

    /** Take the microphone back, unless already listening or not ready. */
    private fun resumeSpotting() {
        if (speech == null && model != null) spot()
    }

    override fun onDestroy() {
        if (running === this) running = null
        speech?.shutdown()
        speech = null
        model?.close()
        model = null
        scope.cancel()
        _status.value = "Off"
        super.onDestroy()
    }

    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(this, 3, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(this, 4,
            Intent(this, WakeWordService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Assistant is listening for its wake word")
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(open)
            .addAction(0, "Stop", stop)
            .build()
    }

    private fun notify(text: String) {
        getSystemService(NotificationManager::class.java)?.notify(NOTIFICATION_ID, notification(text))
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        getSystemService(NotificationManager::class.java)?.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Wake word", NotificationManager.IMPORTANCE_LOW))
    }

    companion object {
        private const val TAG = "WakeWord"
        private const val CHANNEL_ID = "wake_word"
        private const val NOTIFICATION_ID = 4713
        private const val ACTION_STOP = "com.odbscanner.WAKE_STOP"
        const val SAMPLE_RATE = 16000f

        private val _status = MutableStateFlow("Off")
        val status: StateFlow<String> = _status

        @Volatile
        private var running: WakeWordService? = null

        /** Free the microphone for a spoken question (main thread). */
        fun pause() {
            running?.pauseSpotting()
        }

        /** The question is done: listen for the phrase again (main thread). */
        fun resume() {
            running?.resumeSpotting()
        }

        /** Start listening (from the phone screen: a microphone service must start in front). */
        fun start(context: Context): Boolean = try {
            ContextCompat.startForegroundService(context, Intent(context, WakeWordService::class.java))
            true
        } catch (e: Exception) {
            Log.w(TAG, "cannot start", e)
            false
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, WakeWordService::class.java))
        }

        /** Start again if the driver left it on and everything it needs is there. */
        fun resumeIfOn(context: Context) {
            if (AiPrefs.load(context).wakeWord && WakeModel.installed(context) &&
                ContextCompat.checkSelfPermission(context, android.Manifest.permission.RECORD_AUDIO) ==
                android.content.pm.PackageManager.PERMISSION_GRANTED
            ) start(context)
        }
    }
}
