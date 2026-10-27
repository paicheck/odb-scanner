package com.odbscanner

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.odbscanner.ai.AiException
import com.odbscanner.ai.AiPrefs
import com.odbscanner.ai.AiPresets
import com.odbscanner.ai.Assistant
import com.odbscanner.ai.ClaudeChatSession
import com.odbscanner.ai.OpenAiChatSession
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
    val busy by Assistant.busy.collectAsState()
    var question by remember { mutableStateOf("") }
    var showSettings by remember { mutableStateOf(!Assistant.ready(context)) }
    val settings = remember(showSettings) { AiPrefs.load(context) }

    if (showSettings) {
        AssistantSettingsDialog(onDone = { showSettings = false })
        return
    }

    fun send() {
        val q = question.trim()
        if (q.isEmpty() || busy) return
        question = ""
        scope.launch { Assistant.ask(context, q) }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Assistant") },
        text = {
            Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
                Text("${settings.preset.label} · ${settings.effectiveModel.ifBlank { "no model" }}",
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
                        color = if (line.fromDriver) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.padding(top = 6.dp))
                }
                if (busy) Text("Thinking…", style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(top = 6.dp))
                OutlinedTextField(question, { question = it.take(500) },
                    label = { Text("Ask") }, modifier = Modifier.fillMaxWidth().padding(top = 8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { send() }, enabled = !busy && question.isNotBlank()) {
                        Text("Send")
                    }
                    TextButton(onClick = { Assistant.newConversation() }) { Text("New chat") }
                    TextButton(onClick = { showSettings = true }) { Text("Settings") }
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
    var pickProvider by remember { mutableStateOf(false) }
    var models by remember { mutableStateOf<List<String>>(emptyList()) }
    var pickModel by remember { mutableStateOf(false) }
    var note by remember { mutableStateOf<String?>(null) }
    val preset = AiPresets.byId(provider)

    fun save() {
        AiPrefs.save(context, s.copy(provider = provider, model = model, baseUrl = baseUrl,
            effort = effort, speakAlerts = speak))
        if (key.isNotBlank()) AiPrefs.setKey(context, provider, key)
        Assistant.newConversation()
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
                OutlinedTextField(key, { key = it.trim() },
                    label = { Text(if (hasKey) "API key (saved; type to replace)" else "API key") },
                    singleLine = true, visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    modifier = Modifier.fillMaxWidth())
                Box {
                    OutlinedTextField(model, { model = it.trim() },
                        label = { Text(if (preset.protocol == "anthropic")
                            "Model (blank: ${AiPresets.CLAUDE_DEFAULT_MODEL})" else "Model") },
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
                Row {
                    Text("Speak trip alerts (re-plans, stops ahead)", modifier = Modifier.weight(1f))
                    TextButton(onClick = { speak = !speak }) { Text(if (speak) "On" else "Off") }
                }
            }
        },
        confirmButton = { TextButton(onClick = { save(); onDone() }) { Text("Save") } },
        dismissButton = { TextButton(onClick = onDone) { Text("Cancel") } },
    )
}
