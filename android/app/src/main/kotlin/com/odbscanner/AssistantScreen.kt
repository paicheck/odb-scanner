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
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.odbscanner.ai.AiException
import com.odbscanner.ai.AiPrefs
import com.odbscanner.ai.AiPresets
import com.odbscanner.ai.Assistant
import com.odbscanner.ai.ClaudeChatSession
import com.odbscanner.ai.OpenAiChatSession
import com.odbscanner.ai.VoiceAssistant
import com.odbscanner.ai.VoiceSource
import com.odbscanner.ai.VoiceState
import com.odbscanner.ai.WakeMatch
import com.odbscanner.ai.WakeModel
import com.odbscanner.ai.WakeWordService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * The assistant on the phone: the conversation, a box to type into, and its
 * settings. Answers are short, written to be spoken; the car screen and the
 * wake word use the same conversation.
 */
@Composable
fun AssistantDialog(onDismiss: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val lines by Assistant.transcript.collectAsState()
    val status by VoiceAssistant.status.collectAsState()
    val active by Assistant.active.collectAsState()
    val voice = status.state
    // The microphone is asked for on the first Speak; then it listens at once.
    val askMic = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> if (granted) VoiceAssistant.start(context, VoiceSource.PHONE) }
    var question by remember { mutableStateOf("") }
    var showSettings by remember { mutableStateOf(!Assistant.ready(context)) }
    var showSetup by remember { mutableStateOf(false) }
    val settings = remember(showSettings) { AiPrefs.load(context) }

    if (showSettings) {
        AssistantSettingsDialog(onDone = { showSettings = false })
        return
    }
    if (showSetup) {
        AiVoiceSetupDialog(onDone = { showSetup = false })
        return
    }

    fun send() {
        val q = question.trim()
        if (q.isEmpty() || !status.free) return
        question = ""
        VoiceAssistant.ask(context, q)
    }

    fun speak() {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) !=
            PackageManager.PERMISSION_GRANTED) {
            askMic.launch(Manifest.permission.RECORD_AUDIO)
        } else if (status.active) {
            VoiceAssistant.bargeIn(context, VoiceSource.PHONE)   // talk over it
        } else {
            VoiceAssistant.start(context, VoiceSource.PHONE)
        }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Assistant") },
        text = {
            Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
                Text("${settings.preset.label} · ${settings.effectiveModel.ifBlank { "no model" }}" +
                    (active?.takeIf { it.endsWith("(backup)") }?.let { " · now answering: $it" } ?: ""),
                    style = MaterialTheme.typography.bodySmall)
                if (lines.isEmpty()) {
                    Text("Ask about the battery, fault codes, your trip, the next charging " +
                        "stop or junction, or chargers nearby.",
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(vertical = 6.dp))
                }
                lines.forEach { line ->
                    Text(line.text,
                        fontWeight = if (line.fromDriver) FontWeight.Medium else FontWeight.Normal,
                        color = when {
                            line.error != null -> MaterialTheme.colorScheme.error
                            line.fromDriver -> MaterialTheme.colorScheme.primary
                            else -> MaterialTheme.colorScheme.onSurface
                        },
                        modifier = Modifier.padding(top = 6.dp))
                }
                val showing = when (voice) {
                    VoiceState.LISTENING -> "Listening…"
                    VoiceState.TRANSCRIBING -> "Recognising…"
                    VoiceState.THINKING -> "Thinking…"
                    VoiceState.SPEAKING -> "Speaking… (tap Stop, or Speak to talk over it)"
                    VoiceState.INTERRUPTED -> "Stopped"
                    VoiceState.ERROR -> status.error?.let { "${it.category}: ${it.message}" }
                    VoiceState.IDLE -> null
                }
                showing?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall,
                        color = if (voice == VoiceState.ERROR) MaterialTheme.colorScheme.error
                        else MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.padding(top = 6.dp))
                }
                OutlinedTextField(question, { question = it.take(500) },
                    label = { Text("Ask") }, modifier = Modifier.fillMaxWidth().padding(top = 8.dp))
                // While anything is under way, Stop is the biggest thing here.
                if (status.active) {
                    Button(onClick = { VoiceAssistant.stop() },
                        colors = ButtonDefaults.buttonColors(
                            containerColor = MaterialTheme.colorScheme.error),
                        modifier = Modifier.fillMaxWidth().padding(top = 8.dp).height(56.dp)) {
                        Text("Stop", style = MaterialTheme.typography.titleLarge)
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        enabled = voice != VoiceState.INTERRUPTED &&
                            voice != VoiceState.LISTENING && voice != VoiceState.TRANSCRIBING,
                        onClick = { speak() },
                    ) { Text(if (status.active) "Interrupt" else "Speak") }
                    if (status.retryable && VoiceAssistant.lastQuestion != null) {
                        OutlinedButton(onClick = { VoiceAssistant.retry(context) }) { Text("Retry") }
                    }
                    Button(onClick = { send() }, enabled = status.free && question.isNotBlank()) {
                        Text("Send")
                    }
                }
                // A second row: four buttons do not fit across a phone-width dialog.
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = { Assistant.newConversation() }) { Text("New chat") }
                    TextButton(onClick = { showSettings = true }) { Text("Settings") }
                    TextButton(onClick = { showSetup = true }) { Text("Setup & tests") }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
}

@Composable
fun AssistantSettingsDialog(onDone: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val s = remember { AiPrefs.load(context) }
    var provider by remember { mutableStateOf(s.provider) }
    var key by remember { mutableStateOf("") }
    var hasKey by remember { mutableStateOf(AiPrefs.key(context, s.provider).isNotBlank()) }
    var model by remember { mutableStateOf(s.model) }
    var baseUrl by remember { mutableStateOf(s.baseUrl) }
    var effort by remember { mutableStateOf(s.effort) }
    var speak by remember { mutableStateOf(s.speakAlerts) }
    var wake by remember { mutableStateOf(s.wakeWord) }
    var phrase by remember { mutableStateOf(s.wakePhrase) }
    var backups by remember { mutableStateOf(s.backups) }
    var modelReady by remember { mutableStateOf(WakeModel.installed(context)) }
    var downloading by remember { mutableStateOf<Int?>(null) }
    val wakeStatus by WakeWordService.status.collectAsState()
    val askWakeMic = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> if (!granted) wake = false }
    var pickProvider by remember { mutableStateOf(false) }
    var models by remember { mutableStateOf<List<String>>(emptyList()) }
    var pickModel by remember { mutableStateOf(false) }
    var note by remember { mutableStateOf<String?>(null) }
    val preset = AiPresets.byId(provider)

    fun save() {
        val on = wake && modelReady && ContextCompat.checkSelfPermission(context,
            Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        AiPrefs.save(context, s.copy(provider = provider, model = model, baseUrl = baseUrl,
            effort = effort, speakAlerts = speak, wakeWord = on,
            wakePhrase = WakeMatch.normalise(phrase).ifBlank { "hey scanner" },
            backups = backups))
        if (key.isNotBlank()) AiPrefs.setKey(context, provider, key)
        Assistant.newConversation()
        // Restart so a changed phrase takes effect; started here, in front, as
        // Android requires for a microphone service.
        WakeWordService.stop(context)
        if (on) WakeWordService.start(context)
    }

    AlertDialog(
        onDismissRequest = onDone,
        title = { Text("Assistant settings") },
        text = {
            Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
                Text("Your questions, and the app data the assistant looks up to answer them " +
                    "(battery and fault readings, the trip plan, chargers, distances along the " +
                    "route), are sent to the service you choose. The VIN and your exact " +
                    "position are never sent. The key is stored encrypted on this phone.",
                    style = MaterialTheme.typography.bodySmall)
                Box(modifier = Modifier.padding(top = 8.dp)) {
                    OutlinedButton(onClick = { pickProvider = true }, modifier = Modifier.fillMaxWidth()) {
                        Text("Service: ${preset.label}")
                    }
                    DropdownMenu(expanded = pickProvider, onDismissRequest = { pickProvider = false }) {
                        AiPresets.ALL.forEach { p ->
                            DropdownMenuItem(text = { Text(p.label) }, onClick = {
                                provider = p.id
                                hasKey = AiPrefs.key(context, p.id).isNotBlank()
                                key = ""
                                models = emptyList()
                                pickProvider = false
                            })
                        }
                    }
                }
                if (preset.keyUrl.isNotBlank()) {
                    Text("Create a key at ${preset.keyUrl}", style = MaterialTheme.typography.bodySmall)
                }
                if (preset.freeNote.isNotBlank()) {
                    Text(preset.freeNote, style = MaterialTheme.typography.bodySmall)
                }
                OutlinedTextField(key, { key = it.trim() },
                    label = { Text(if (hasKey) "API key (saved; type to replace)" else "API key") },
                    singleLine = true, visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    modifier = Modifier.fillMaxWidth())
                Box {
                    OutlinedTextField(model, { model = it.trim() },
                        label = { Text(if (preset.defaultModel.isNotBlank())
                            "Model (blank: ${preset.defaultModel})" else "Model") },
                        singleLine = true, modifier = Modifier.fillMaxWidth())
                    DropdownMenu(expanded = pickModel, onDismissRequest = { pickModel = false }) {
                        models.take(40).forEach { m ->
                            DropdownMenuItem(text = { Text(m) }, onClick = { model = m; pickModel = false })
                        }
                    }
                }
                TextButton(onClick = {
                    note = "Loading models…"
                    scope.launch {
                        val k = key.ifBlank { AiPrefs.key(context, provider) }
                        note = try {
                            models = withContext(Dispatchers.IO) {
                                if (preset.protocol == "anthropic") {
                                    if (k.isBlank()) ClaudeChatSession.SUGGESTED
                                    else ClaudeChatSession.listModels(k, baseUrl)
                                } else OpenAiChatSession.listModels(baseUrl.ifBlank { preset.baseUrl }, k)
                            }
                            pickModel = models.isNotEmpty()
                            if (models.isEmpty()) "The service listed no models" else null
                        } catch (e: AiException) {
                            e.message
                        }
                    }
                }) { Text("Choose from the service's models") }
                note?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                if (preset.protocol == "anthropic") {
                    Text("Effort (low answers fastest)", style = MaterialTheme.typography.bodySmall)
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        listOf("low", "medium", "high").forEach { e ->
                            if (e == effort) Button(onClick = { effort = e }) { Text(e) }
                            else OutlinedButton(onClick = { effort = e }) { Text(e) }
                        }
                    }
                }
                OutlinedTextField(baseUrl, { baseUrl = it.trim() },
                    label = { Text(if (provider == "custom") "Service address, e.g. https://example.com/v1"
                        else "Service address (blank: the default)") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                HorizontalDivider(modifier = Modifier.padding(vertical = 6.dp))
                // Backups: the other services with a saved key that can run on
                // their default model, asked in this order if the one above fails.
                val candidates = remember(provider, hasKey) {
                    AiPresets.ALL.filter { p ->
                        p.id != provider && AiPresets.canBackUp(p.id) &&
                            AiPrefs.key(context, p.id).isNotBlank()
                    }
                }
                Text("Backups: if ${preset.label} fails (rate limit, outage), ask these in turn",
                    style = MaterialTheme.typography.bodySmall)
                if (candidates.isEmpty()) {
                    Text("To add one, choose another service above, enter its key and Save; " +
                        "then choose this one again and tick it here.",
                        style = MaterialTheme.typography.bodySmall)
                }
                candidates.forEach { p ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = p.id in backups, onCheckedChange = { on ->
                            backups = if (on) AiPresets.ALL.map { it.id }
                                .filter { it in backups || it == p.id }
                            else backups - p.id
                        })
                        Text(p.label)
                    }
                }
                HorizontalDivider(modifier = Modifier.padding(vertical = 6.dp))
                Row {
                    Text("Speak trip alerts (re-plans, stops ahead)", modifier = Modifier.weight(1f))
                    TextButton(onClick = { speak = !speak }) { Text(if (speak) "On" else "Off") }
                }
                HorizontalDivider(modifier = Modifier.padding(vertical = 6.dp))
                Row {
                    Text("Wake word: always listening", modifier = Modifier.weight(1f))
                    TextButton(onClick = {
                        wake = !wake
                        if (wake && ContextCompat.checkSelfPermission(context,
                                Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED
                        ) askWakeMic.launch(Manifest.permission.RECORD_AUDIO)
                    }) { Text(if (wake) "On" else "Off") }
                }
                if (wake) {
                    Text("The phone listens for the phrase itself, offline -- no audio leaves " +
                        "it until you have said it. It uses some battery; a notification shows " +
                        "while it listens, with Stop.", style = MaterialTheme.typography.bodySmall)
                    OutlinedTextField(phrase, { phrase = it.take(30) },
                        label = { Text("Wake phrase (two or three words)") }, singleLine = true,
                        modifier = Modifier.fillMaxWidth())
                    if (!modelReady) {
                        Button(enabled = downloading == null, onClick = {
                            downloading = 0
                            scope.launch {
                                note = try {
                                    WakeModel.download(context) { p -> downloading = p }
                                    modelReady = true
                                    null
                                } catch (e: AiException) {
                                    e.message
                                }
                                downloading = null
                            }
                        }) {
                            Text(downloading?.let { "Downloading… $it %" }
                                ?: "Download the speech model (40 MB)")
                        }
                    }
                    Text("Status: $wakeStatus", style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = { TextButton(onClick = { save(); onDone() }) { Text("Save") } },
        dismissButton = { TextButton(onClick = onDone) { Text("Cancel") } },
    )
}
