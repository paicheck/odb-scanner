package com.odbscanner

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.odbscanner.collect.Collector
import com.odbscanner.collect.Reading
import com.odbscanner.spec.SpecLoader
import com.odbscanner.transport.BluetoothSpp
import com.odbscanner.transport.Elm327Protocol
import com.odbscanner.uds.ReadOnlyGuardImpl
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val spec = loadSpec()
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    CollectorScreen(spec)
                }
            }
        }
    }

    private fun loadSpec() = SpecLoader.load(
        assets.open("spec.json").bufferedReader(Charsets.UTF_8).use { it.readText() }
    )
}

@SuppressLint("MissingPermission")
@Composable
private fun CollectorScreen(spec: com.odbscanner.spec.Spec) {
    var status by remember { mutableStateOf("Idle") }
    var readings by remember { mutableStateOf(listOf<Reading>()) }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text(
            "ODB Scanner (read-only) — MEB profile ${spec.specVersion}",
            style = MaterialTheme.typography.titleMedium,
        )
        Text("${spec.ecus.size} ECUs, ${spec.profiles["meb"]?.measurements?.size ?: 0} DIDs",
            style = MaterialTheme.typography.bodySmall)

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(vertical = 8.dp)) {
            Button(onClick = {
                status = "Connecting…"
                runCollection(spec) { msg, result -> status = msg; result?.let { readings = it } }
            }) { Text("Connect & collect") }
            Button(onClick = { readings = emptyList(); status = "Idle" }) { Text("Clear") }
        }

        Text("Status: $status", style = MaterialTheme.typography.bodyMedium)
        Text("Live / decoded (${readings.count { it.status == "ok" }} ok)")

        LazyColumn(modifier = Modifier.fillMaxWidth().weight(1f)) {
            items(readings) { r ->
                Row(modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
                    Text("${r.key}: ", modifier = Modifier.weight(0.45f))
                    Text(
                        "${r.value ?: "—"}  [${r.status}]",
                        modifier = Modifier.weight(0.55f),
                    )
                }
            }
        }
    }
}

/** Run one collection pass on a background thread. */
private suspend fun runCollection(
    spec: com.odbscanner.spec.Spec,
    onUpdate: (String, List<Reading>?) -> Unit,
) = withContext(Dispatchers.IO) {
    try {
        val adapter = BluetoothAdapter.getDefaultAdapter()
        val device = adapter?.bondedDevices?.firstOrNull()
            ?: run { onUpdate("No paired ELM327 adapter", null); return@withContext }
        val port = BluetoothSpp.connect(device)
        val protocol = Elm327Protocol(port)
        protocol.init()
        val guard = ReadOnlyGuardImpl(spec.guard)
        val profile = spec.profiles["meb"] ?: error("no meb profile")

        val collector = Collector(spec, profile, protocol, guard)
        val ecus = collector.discover()
        onUpdate("ECU discovery: $ecus", null)
        val readings = collector.collectOnce()
        onUpdate("Collected ${readings.size} readings", readings)
        port.close()
    } catch (e: Exception) {
        onUpdate("Error: ${e.message}", null)
    }
}
