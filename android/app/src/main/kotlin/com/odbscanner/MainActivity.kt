package com.odbscanner

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.odbscanner.collect.Collector
import com.odbscanner.collect.Reading
import com.odbscanner.spec.Spec
import com.odbscanner.spec.SpecLoader
import com.odbscanner.transport.BluetoothSpp
import com.odbscanner.transport.Elm327Protocol
import com.odbscanner.uds.ReadOnlyGuardImpl
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
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

    private fun loadSpec(): Spec = SpecLoader.load(
        assets.open("spec.json").bufferedReader(Charsets.UTF_8).use { it.readText() }
    )
}

private val BLUETOOTH_PERMISSIONS = arrayOf(
    Manifest.permission.BLUETOOTH_CONNECT,
    Manifest.permission.BLUETOOTH_SCAN,
)

private fun hasBluetoothPermission(context: android.content.Context): Boolean =
    BLUETOOTH_PERMISSIONS.all {
        ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
    }

@SuppressLint("MissingPermission")
private fun pairedDevices(): List<BluetoothDevice> =
    BluetoothAdapter.getDefaultAdapter()?.bondedDevices?.toList() ?: emptyList()

@Composable
private fun CollectorScreen(spec: Spec) {
    var status by remember { mutableStateOf("Idle") }
    var readings by remember { mutableStateOf(listOf<Reading>()) }
    var devices by remember { mutableStateOf(listOf<BluetoothDevice>()) }
    var showDeviceDialog by remember { mutableStateOf(false) }

    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { grants ->
        if (grants.values.all { it }) {
            devices = pairedDevices()
            showDeviceDialog = true
        } else {
            status = "Bluetooth permission denied"
        }
    }

    fun startConnectFlow() {
        if (hasBluetoothPermission(context)) {
            devices = pairedDevices()
            showDeviceDialog = true
        } else {
            permissionLauncher.launch(BLUETOOTH_PERMISSIONS)
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text(
            "ODB Scanner (read-only) — MEB profile ${spec.specVersion}",
            style = MaterialTheme.typography.titleMedium,
        )
        Text(
            "${spec.ecus.size} ECUs, ${spec.profiles["meb"]?.measurements?.size ?: 0} DIDs",
            style = MaterialTheme.typography.bodySmall,
        )

        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(vertical = 8.dp),
        ) {
            Button(onClick = { startConnectFlow() }) { Text("Connect & collect") }
            Button(onClick = { readings = emptyList(); status = "Idle" }) { Text("Clear") }
        }

        Text("Status: $status", style = MaterialTheme.typography.bodyMedium)
        Text("Decoded ${readings.count { it.status == "ok" }} of ${readings.size} readings")

        LazyColumn(modifier = Modifier.fillMaxWidth().weight(1f)) {
            items(readings) { r ->
                Row(modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
                    Text("${r.key}: ", modifier = Modifier.weight(0.45f))
                    Text("${r.value ?: "—"}  [${r.status}]", modifier = Modifier.weight(0.55f))
                }
            }
        }
    }

    if (showDeviceDialog) {
        AlertDialog(
            onDismissRequest = { showDeviceDialog = false },
            title = { Text("Choose adapter") },
            text = {
                if (devices.isEmpty()) {
                    Text("No paired devices. Pair the ELM327 in Android Bluetooth settings.")
                } else {
                    LazyColumn {
                        items(devices, key = { it.address }) { d ->
                            Text(
                                "${d.name ?: d.address}\n${d.address}",
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable {
                                        showDeviceDialog = false
                                        status = "Connecting…"
                                        scope.launch {
                                            runCollection(spec, d) { msg, result ->
                                                status = msg
                                                result?.let { readings = it }
                                            }
                                        }
                                    }
                                    .padding(vertical = 10.dp),
                            )
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { showDeviceDialog = false }) { Text("Cancel") }
            },
        )
    }
}

/** Run one collection pass on a background thread against [device]. */
private suspend fun runCollection(
    spec: Spec,
    device: BluetoothDevice,
    onUpdate: (String, List<Reading>?) -> Unit,
) = withContext(Dispatchers.IO) {
    try {
        val port = BluetoothSpp.connect(device)
        try {
            val protocol = Elm327Protocol(port)
            protocol.init()
            val guard = ReadOnlyGuardImpl(spec.guard)
            val profile = spec.profiles["meb"] ?: error("no meb profile")

            val collector = Collector(spec, profile, protocol, guard)
            val ecus = collector.discover()
            onUpdate("ECU discovery: $ecus", null)
            val readings = collector.collectOnce()
            onUpdate("Collected ${readings.size} readings", readings)
        } finally {
            port.close()
        }
    } catch (e: Exception) {
        onUpdate("Error: ${e.message}", null)
    }
}
