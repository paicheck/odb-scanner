package com.odbscanner

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.odbscanner.ai.AiPrefs
import com.odbscanner.ai.AiPresets
import com.odbscanner.ai.AiSettings
import com.odbscanner.ai.Assistant
import com.odbscanner.ai.Diagnostics
import com.odbscanner.ai.Speaker
import com.odbscanner.ai.TestKind
import com.odbscanner.ai.TestResult
import com.odbscanner.ai.VoiceAssistant
import com.odbscanner.ai.VoiceState
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/**
 * AI & Voice Setup: every provider by what it can do, what it is set to,
 * and a test for each of its capabilities -- with Stop always there while a
 * test runs. Keys and models are entered in Assistant settings; here they
 * are checked, chosen as main or backup, or turned off.
 *
 * Each test is a turn of the voice assistant's own (VoiceAssistant.test):
 * it cannot overlap a conversation, and Stop ends it at once. Results are
 * kept on the phone, never with a key in them.
 */
@Composable
fun AiVoiceSetupDialog(onDone: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var settings by remember { mutableStateOf(AiPrefs.load(context)) }
    val voice by VoiceAssistant.status.collectAsState()
    val active by Assistant.active.collectAsState()
    var running by remember { mutableStateOf<String?>(null) }
    var job by remember { mutableStateOf<Job?>(null) }
    var note by remember { mutableStateOf<String?>(null) }
    var results by remember { mutableStateOf(emptyMap<String, TestResult>()) }
    val recogniser = remember { Diagnostics.recogniser(context) }
    var voices by remember { mutableStateOf<Pair<String, List<String>>?>(null) }
    var pickVoice by remember { mutableStateOf(false) }
    var showOthers by remember { mutableStateOf(false) }
    var pendingMic by remember { mutableStateOf<(() -> Unit)?>(null) }
    val askMic = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) pendingMic?.invoke() else note = "The microphone permission is needed for that test"
        pendingMic = null
    }

    fun result(id: String, kind: TestKind): TestResult? =
        results["$id:$kind"] ?: Diagnostics.last(context, id, kind)

    fun save(s: AiSettings) {
        AiPrefs.save(context, s)
        settings = AiPrefs.load(context)
        Assistant.newConversation()
    }

    fun run(id: String, kind: TestKind, mic: Boolean, block: suspend (Int) -> TestResult) {
        if (running != null) return
        val go = {
            running = "$id:$kind"
            note = null
            job = scope.launch {
                try {
                    val r = VoiceAssistant.test(context, mic, block)
                    if (r == null) note = "The assistant is busy: stop it first"
                    else {
                        Diagnostics.save(context, id, r)
                        results = results + ("$id:$kind" to r)
                    }
                } finally {
                    running = null
                }
            }
        }
        if (mic && ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) !=
            PackageManager.PERMISSION_GRANTED) {
            pendingMic = go
            askMic.launch(Manifest.permission.RECORD_AUDIO)
        } else go()
    }

    fun stop() {
        VoiceAssistant.stop()
        job?.cancel()
        if (running != null) note = "Stopped"
        running = null
    }

    AlertDialog(
        onDismissRequest = { stop(); onDone() },
        title = { Text("AI & Voice Setup") },
        text = {
            Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
                Text("Who receives what: your voice goes to the phone's speech recogniser; the " +
                    "words recognised, and the app data the assistant looks up, go to the AI " +
                    "service in use (never the VIN or your exact position); answers are spoken " +
                    "by the phone's voice. Tests send only a fixed test sentence -- no car data.",
                    style = MaterialTheme.typography.bodySmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.padding(top = 8.dp)) {
                    Button(enabled = running == null && voice.free, onClick = {
                        val key = AiPrefs.key(context, settings.provider)
                        run("chain", TestKind.END_TO_END, mic = true) { turn ->
                            Diagnostics.endToEnd(context, turn, settings, key)
                        }
                    }) { Text("Test the whole voice chain") }
                    Button(onClick = { stop() }, enabled = running != null || voice.active) {
                        Text("Stop")
                    }
                }
                ResultLine(result("chain", TestKind.END_TO_END), running == "chain:${TestKind.END_TO_END}")
                note?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                if (voice.state == VoiceState.ERROR) voice.error?.let {
                    Text("Last voice error: ${it.line}", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error)
                }

                Section("Speech recognition (STT)")
                Text("The phone's recogniser: " + (if (recogniser.first) "available" else
                    "NOT available") + "; on the phone itself: " + when (recogniser.second) {
                    true -> "yes"; false -> "no"; null -> "unknown on this Android" },
                    style = MaterialTheme.typography.bodySmall)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = settings.sttOffline,
                        onCheckedChange = { save(settings.copy(sttOffline = it)) })
                    Text("Recognise on the phone only, where it can (your voice stays on it)",
                        style = MaterialTheme.typography.bodySmall)
                }
                OutlinedButton(enabled = running == null && voice.free, onClick = {
                    run(Diagnostics.PHONE_STT, TestKind.STT, mic = true) {
                        Diagnostics.stt(context, settings.sttOffline)
                    }
                }) { Text("Test: say a short sentence") }
                ResultLine(result(Diagnostics.PHONE_STT, TestKind.STT),
                    running == "${Diagnostics.PHONE_STT}:${TestKind.STT}")

                Section("Speech output (TTS)")
                Text("The phone's voice: " + (settings.ttsVoice.ifBlank { "the engine's own" }) +
                    (voices?.let { " · engine ${it.first}" } ?: ""),
                    style = MaterialTheme.typography.bodySmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Box {
                        OutlinedButton(onClick = {
                            voices = Speaker.voices(context)
                            pickVoice = true
                        }) { Text("Choose voice") }
                        DropdownMenu(expanded = pickVoice, onDismissRequest = { pickVoice = false }) {
                            DropdownMenuItem(text = { Text("The engine's own") }, onClick = {
                                save(settings.copy(ttsVoice = "")); pickVoice = false
                            })
                            voices?.second.orEmpty().take(40).forEach { v ->
                                DropdownMenuItem(text = { Text(v) }, onClick = {
                                    save(settings.copy(ttsVoice = v.removeSuffix(" (online)")))
                                    pickVoice = false
                                })
                            }
                        }
                    }
                    OutlinedButton(enabled = running == null && voice.free, onClick = {
                        run(Diagnostics.PHONE_TTS, TestKind.TTS, mic = false) { turn ->
                            Diagnostics.tts(context, turn)
                        }
                    }) { Text("Test: speak") }
                }
                ResultLine(result(Diagnostics.PHONE_TTS, TestKind.TTS),
                    running == "${Diagnostics.PHONE_TTS}:${TestKind.TTS}")

                Section("AI (LLM)")
                Text("Asked in order: " + settings.chain.joinToString(" → ") {
                    AiPresets.byId(it).label + if (it == settings.provider) " (main)" else " (backup)"
                }.ifBlank { "nothing -- every service is turned off" } +
                    (active?.let { "\nAnswered last: $it" } ?: ""),
                    style = MaterialTheme.typography.bodySmall)
                Text("The AI services do chat only: speech in and out are the phone's own. Add " +
                    "keys and choose models in Assistant → Settings.",
                    style = MaterialTheme.typography.bodySmall)
                val configured = AiPresets.ALL.filter { p ->
                    AiPrefs.key(context, p.id).isNotBlank() || p.id == settings.provider ||
                        p.id in settings.backups
                }
                configured.forEach { p ->
                    ProviderCard(p.id, settings, results = { k -> result(p.id, k) },
                        running = running, free = running == null && voice.free,
                        onTest = { kind ->
                            val key = AiPrefs.key(context, p.id)
                            val main = p.id == settings.provider
                            val model = if (main) settings.effectiveModel else p.defaultModel
                            val base = if (main) settings.baseUrl else ""
                            run(p.id, kind, mic = false) {
                                when (kind) {
                                    TestKind.CONNECTION -> Diagnostics.connection(p, base)
                                    TestKind.AUTH -> Diagnostics.auth(p, key, model, base)
                                    else -> Diagnostics.llm(p, key, model, base)
                                }
                            }
                        },
                        onChange = { save(it) })
                }
                TextButton(onClick = { showOthers = !showOthers }) {
                    Text(if (showOthers) "Hide the other services" else "Other services")
                }
                if (showOthers) {
                    AiPresets.ALL.filter { it !in configured }.forEach { p ->
                        Text("${p.label}: not configured (no key) · ${p.keyUrl}".trimEnd(' ', '·'),
                            style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { stop(); onDone() }) { Text("Close") } },
    )
}

@Composable
private fun Section(title: String) {
    HorizontalDivider(modifier = Modifier.padding(top = 10.dp))
    Text(title, fontWeight = FontWeight.Medium, modifier = Modifier.padding(top = 6.dp))
}

@Composable
private fun ResultLine(r: TestResult?, running: Boolean) {
    val text = when {
        running -> "Testing… (Stop ends it)"
        r == null -> return
        r.ok -> "✓ ${r.summary}" + (r.error?.let { " · ${it.category}" } ?: "") + " · ${ago(r.atMs)}"
        else -> "✗ ${r.error?.category ?: "FAILED"}" + (if (r.stage.isNotBlank()) " at ${r.stage}" else "") +
            ": ${r.summary}" + (r.error?.detail?.takeIf { it.isNotBlank() }?.let { " ($it)" } ?: "") +
            (r.ms?.let { " · $it ms" } ?: "") + " · ${ago(r.atMs)}"
    }
    Text(text, style = MaterialTheme.typography.bodySmall,
        color = if (r != null && !r.ok && !running) MaterialTheme.colorScheme.error
        else MaterialTheme.colorScheme.onSurface)
    if (r != null && !running && r.stages.isNotEmpty()) {
        Text(r.stages.entries.joinToString(" · ") { "${it.key} ${it.value} ms" },
            style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun ProviderCard(id: String, s: AiSettings, results: (TestKind) -> TestResult?,
                         running: String?, free: Boolean, onTest: (TestKind) -> Unit,
                         onChange: (AiSettings) -> Unit) {
    val context = LocalContext.current
    val p = AiPresets.byId(id)
    val main = id == s.provider
    val backup = id in s.backups
    val off = id in s.disabled
    val hasKey = AiPrefs.key(context, id).isNotBlank()
    val role = when {
        off -> "turned off"
        main -> "main"
        backup -> "backup ${s.backups.indexOf(id) + 1}"
        else -> "not used"
    }
    val model = if (main) s.effectiveModel else p.defaultModel
    Text("${p.label} — $role", fontWeight = FontWeight.Medium, modifier = Modifier.padding(top = 8.dp))
    Text((if (hasKey || id == "custom") "Configured" else "Not configured: no key") +
        " · can do: AI chat with tools (no speech in or out) · model: " +
        model.ifBlank { "none chosen" }, style = MaterialTheme.typography.bodySmall)
    val auth = results(TestKind.AUTH)
    Text("Key: " + when {
        !hasKey && id != "custom" -> "none stored"
        auth == null -> "not tested"
        auth.ok -> "accepted"
        else -> auth.error?.category?.name ?: "failed"
    }, style = MaterialTheme.typography.bodySmall)
    listOf(TestKind.CONNECTION, TestKind.AUTH, TestKind.LLM).forEach { k ->
        ResultLine(results(k), running == "$id:$k")
    }
    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        TextButton(enabled = free, onClick = { onTest(TestKind.CONNECTION) }) { Text("Connection") }
        TextButton(enabled = free, onClick = { onTest(TestKind.AUTH) }) { Text("Key") }
        TextButton(enabled = free, onClick = { onTest(TestKind.LLM) }) { Text("AI test") }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        if (!main) {
            TextButton(onClick = { onChange(s.copy(provider = id, backups = s.backups - id,
                disabled = s.disabled - id)) }) { Text("Make main") }
        }
        if (!main && AiPresets.canBackUp(id)) {
            TextButton(onClick = {
                onChange(s.copy(backups = if (backup) s.backups - id else s.backups + id))
            }) { Text(if (backup) "Not a backup" else "Use as backup") }
        }
        TextButton(onClick = {
            onChange(s.copy(disabled = if (off) s.disabled - id else s.disabled + id))
        }) { Text(if (off) "Turn on" else "Turn off") }
    }
}

private fun ago(atMs: Long): String {
    val s = (System.currentTimeMillis() - atMs) / 1000
    return when {
        s < 60 -> "just now"
        s < 3600 -> "${s / 60} min ago"
        s < 86_400 -> "${s / 3600} h ago"
        else -> "${s / 86_400} days ago"
    }
}
