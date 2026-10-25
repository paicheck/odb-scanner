package com.odbscanner

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.util.Log
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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.odbscanner.collect.Collector
import com.odbscanner.collect.Reading
import com.odbscanner.db.AppDatabase
import com.odbscanner.db.MeasurementEntity
import com.odbscanner.db.Repository
import com.odbscanner.net.CloudPrefs
import com.odbscanner.net.CloudSync
import com.odbscanner.net.CloudSyncException
import com.odbscanner.spec.Spec
import com.odbscanner.spec.SpecLoader
import com.odbscanner.transport.BluetoothSpp
import com.odbscanner.transport.Elm327Protocol
import com.odbscanner.uds.ReadOnlyGuardImpl
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // A bad or unreadable spec must show an error screen, not a crash with
        // no explanation: the asset is generated, and a hand-edited or missing
        // one would otherwise take the whole app down before the UI exists.
        val (spec, error) = try {
            loadSpec() to null as String?
        } catch (e: Exception) {
            null as Spec? to "${e.javaClass.simpleName}: ${e.message}"
        }
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    if (spec != null) CollectorScreen(spec) else ErrorScreen(error ?: "unknown")
                }
            }
        }
    }

    private fun loadSpec(): Spec = SpecLoader.load(
        assets.open("spec.json").bufferedReader(Charsets.UTF_8).use { it.readText() }
    )
}

@Composable
private fun ErrorScreen(message: String) {
    Column(modifier = Modifier.fillMaxSize().padding(24.dp)) {
        Text(
            "Failed to load the diagnostic spec",
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.error,
        )
        Text(message, style = MaterialTheme.typography.bodyMedium)
    }
}

/**
 * Runtime Bluetooth permissions, by platform level.
 *
 * Android 12+ gates both the bonded-device lookup/RFCOMM connect and the
 * discovery scan behind BLUETOOTH_CONNECT + BLUETOOTH_SCAN. Android 6-11 needs
 * ACCESS_FINE_LOCATION to *discover* nearby classic devices (the BLUETOOTH /
 * BLUETOOTH_ADMIN pair is install-time and auto-granted), so that is the only
 * runtime permission asked there. Declaring neverForLocation on BLUETOOTH_SCAN
 * means 12+ does not also need location.
 */
private val BLUETOOTH_PERMISSIONS: Array<String> =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        arrayOf(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_SCAN)
    } else {
        arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
    }

private const val LOG_TAG = "odbscanner"

private fun hasBluetoothPermission(context: Context): Boolean =
    BLUETOOTH_PERMISSIONS.all {
        ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
    }

@SuppressLint("MissingPermission")
private fun pairedDevices(): List<BluetoothDevice> =
    BluetoothAdapter.getDefaultAdapter()?.bondedDevices?.toList() ?: emptyList()

/**
 * Run one classic-Bluetooth discovery scan, feeding each found device to
 * [onFound] and [onFinished] when the 12 s inquiry window closes. The receiver
 * is unregistered and discovery cancelled on [stop], so the radio is never left
 * scanning after the dialog closes.
 */
private class BluetoothScanner(
    private val context: Context,
    private val onFound: (BluetoothDevice) -> Unit,
    private val onFinished: () -> Unit,
    private val onBondChanged: (BluetoothDevice, Int) -> Unit = { _, _ -> },
) {
    private val adapter = BluetoothAdapter.getDefaultAdapter()
    private var receiver: BroadcastReceiver? = null

    fun start() {
        stop()
        val adapter = adapter ?: return
        val filter = IntentFilter().apply {
            addAction(BluetoothDevice.ACTION_FOUND)
            addAction(BluetoothAdapter.ACTION_DISCOVERY_FINISHED)
            addAction(BluetoothDevice.ACTION_BOND_STATE_CHANGED)
        }
        val receiver = object : BroadcastReceiver() {
            @SuppressLint("MissingPermission")
            override fun onReceive(context: Context?, intent: Intent?) {
                when (intent?.action) {
                    BluetoothDevice.ACTION_FOUND -> {
                        @Suppress("DEPRECATION")
                        val device = intent.getParcelableExtra<BluetoothDevice>(
                            BluetoothDevice.EXTRA_DEVICE,
                        )
                        device?.let(onFound)
                    }
                    BluetoothAdapter.ACTION_DISCOVERY_FINISHED -> onFinished()
                    BluetoothDevice.ACTION_BOND_STATE_CHANGED -> {
                        @Suppress("DEPRECATION")
                        val device = intent.getParcelableExtra<BluetoothDevice>(
                            BluetoothDevice.EXTRA_DEVICE,
                        )
                        val state = intent.getIntExtra(
                            BluetoothDevice.EXTRA_BOND_STATE,
                            BluetoothDevice.ERROR,
                        )
                        if (device != null) onBondChanged(device, state)
                    }
                }
            }
        }
        this.receiver = receiver
        context.registerReceiver(receiver, filter)
        @SuppressLint("MissingPermission")
        adapter.startDiscovery()
    }

    fun stop() {
        receiver?.let {
            try { context.unregisterReceiver(it) } catch (_: IllegalArgumentException) {}
        }
        receiver = null
        @SuppressLint("MissingPermission")
        try { adapter?.cancelDiscovery() } catch (_: SecurityException) {}
    }
}

fun MeasurementEntity.toReading() = Reading(
    key = key,
    // A non-scalar decode (VIN, gear, pair/multi-field JSON) lives in
    // text_value: Room's numeric column is a DOUBLE and cannot hold it.
    value = value ?: textValue,
    raw = rawResponse,
    status = if (success) "ok" else (error ?: "no-response"),
)

@Composable
private fun CollectorScreen(spec: Spec) {
    val context = LocalContext.current
    val repository = remember { Repository(AppDatabase.get(context)) }
    val scope = rememberCoroutineScope()

    var status by remember { mutableStateOf("Loading…") }
    var readings by remember { mutableStateOf(listOf<Reading>()) }
    var devices by remember { mutableStateOf(listOf<BluetoothDevice>()) }
    var showDeviceDialog by remember { mutableStateOf(false) }
    // The vehicle the persisted rows belong to; the VIN read at connect moves
    // this off its fallback, so the history shown is the car's, not row 1's.
    var vehicleId by remember { mutableStateOf(1L) }
    // One collection at a time: a second connect launched mid-pass would open a
    // second RFCOMM socket, and the adapter answers only one of them.
    var busy by remember { mutableStateOf(false) }
    // Devices found by the discovery scan, plus a flag so the dialog can show a
    // "searching" line. Bonded devices are read separately and merged below.
    var discovered by remember { mutableStateOf(listOf<BluetoothDevice>()) }
    var discovering by remember { mutableStateOf(false) }

    // Cloud upload settings live in SharedPreferences so the endpoint and token
    // can be changed on the device without rebuilding the app.
    var cloudSettings by remember { mutableStateOf(CloudPrefs.load(context)) }
    var showCloudDialog by remember { mutableStateOf(false) }
    var cloudEnabled by remember { mutableStateOf(false) }
    var cloudUrl by remember { mutableStateOf("") }
    var cloudToken by remember { mutableStateOf("") }
    // The running collection session, so the Stop button can cancel it.
    var collectJob by remember { mutableStateOf<Job?>(null) }

    // Upload whatever Room has not yet sent. Safe to call any time: it is
    // idempotent, and a failure only sets the status line.
    fun syncCloud() {
        if (!cloudSettings.isUsable) {
            status = "Cloud sync is not configured (set a URL and token)"
            return
        }
        scope.launch {
            status = "Uploading to cloud…"
            try {
                val result = CloudSync(repository, cloudSettings).uploadPending()
                val pending = repository.pendingUploadCount()
                status = if (result.batches == 0) {
                    "Cloud: nothing to upload ($pending pending)"
                } else {
                    "Cloud: uploaded ${result.uploaded}, ${result.duplicates} " +
                        "already present, $pending pending"
                }
            } catch (e: CloudSyncException) {
                // Nothing was lost: the watermark is the local row itself, and
                // rows stay pending until the API acknowledges them.
                status = "Cloud upload failed (will retry): ${e.message}"
            } catch (e: Exception) {
                status = "Cloud upload failed: ${e.message}"
            }
        }
    }

    // Load the latest persisted measurements on startup so history survives
    // an app restart (the collector is not running in the background on Android).
    LaunchedEffect(Unit) {
        try {
            vehicleId = repository.firstVehicleId() ?: 1L
            val latest = repository.latestMeasurements(vehicleId)
            readings = latest.map { it.toReading() }
            status = "Loaded ${readings.size} stored readings"
        } catch (e: Exception) {
            status = "DB error: ${e.message}"
        }
    }

    fun reloadStored() {
        // Reload from the persisted store without re-collecting.
        scope.launch {
            val latest = repository.latestMeasurements(vehicleId)
            readings = latest.map { it.toReading() }
            status = "Loaded ${readings.size} stored readings"
        }
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { grants ->
        if (grants.values.all { it }) {
            devices = pairedDevices()
            discovered = emptyList()
            showDeviceDialog = true
        } else {
            status = "Bluetooth permission denied"
        }
    }

    fun startConnectFlow() {
        if (hasBluetoothPermission(context)) {
            devices = pairedDevices()
            discovered = emptyList()
            showDeviceDialog = true
        } else {
            permissionLauncher.launch(BLUETOOTH_PERMISSIONS)
        }
    }

    /** Open the RFCOMM link and collect continuously until stopped. */
    fun connectTo(device: BluetoothDevice) {
        showDeviceDialog = false
        status = "Connecting…"
        collectJob?.cancel()
        collectJob = scope.launch {
            busy = true
            try {
                runContinuousCollection(
                    spec = spec,
                    device = device,
                    repository = repository,
                    pollIntervalMs = 5_000L,
                    onVehicleId = { vehicleId = it },
                    onStatus = { status = it },
                    onReadings = { batch -> readings = mergeReadings(readings, batch) },
                )
            } catch (e: CancellationException) {
                status = "Collection stopped"
            } catch (e: Exception) {
                status = "Error: ${e.message}"
            } finally {
                busy = false
            }
        }
    }

    /** Stop the running collection session and push anything pending. */
    fun stopCollection() {
        collectJob?.cancel()
        collectJob = null
        if (cloudSettings.isUsable) syncCloud()
    }

    /** Ask the OS to pair an unpaired device (raises the PIN prompt). */
    @SuppressLint("MissingPermission")
    fun pairWith(device: BluetoothDevice) {
        val started = try {
            device.createBond()
        } catch (_: SecurityException) {
            false
        }
        status = if (started) {
            "Pairing ${device.name ?: device.address}… enter the PIN on screen " +
                "(ELM327 clones usually use 1234 or 0000)"
        } else {
            "Could not start pairing ${device.address}"
        }
    }

    // Scan for nearby classic devices while the picker is open. This is what
    // finds an ELM327 dongle that was never paired: bondedDevices alone cannot
    // see it, and a dongle with no friendly name shows up as its MAC address.
    DisposableEffect(showDeviceDialog) {
        if (showDeviceDialog) {
            val scanner = BluetoothScanner(
                context,
                onFound = { d ->
                    if (discovered.none { it.address == d.address }) {
                        discovered = discovered + d
                    }
                },
                onFinished = { discovering = false },
                onBondChanged = { d, state ->
                    when (state) {
                        BluetoothDevice.BOND_BONDED -> {
                            devices = pairedDevices()
                            status = "Paired ${d.name ?: d.address}"
                        }
                        BluetoothDevice.BOND_NONE ->
                            status = "Pairing ${d.name ?: d.address} failed"
                    }
                },
            )
            discovering = true
            scanner.start()
            onDispose { scanner.stop() }
        } else {
            onDispose { }
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 12.dp, vertical = 8.dp),
    ) {
        Text(
            "ODB Scanner (read-only) — MEB ${spec.specVersion}",
            style = MaterialTheme.typography.titleMedium,
        )

        Row(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            modifier = Modifier.padding(vertical = 6.dp),
        ) {
            Button(
                onClick = { startConnectFlow() },
                enabled = !busy,
                modifier = Modifier.weight(1f),
            ) { Text("Connect") }
            Button(
                onClick = { stopCollection() },
                enabled = busy,
                modifier = Modifier.weight(1f),
            ) { Text("Stop") }
            Button(
                onClick = { reloadStored() },
                enabled = !busy,
                modifier = Modifier.weight(1f),
            ) { Text("Reload") }
            Button(
                onClick = { readings = emptyList(); status = "Idle" },
                enabled = !busy,
                modifier = Modifier.weight(1f),
            ) { Text("Clear") }
        }

        Row(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            modifier = Modifier.padding(bottom = 4.dp),
        ) {
            Button(
                onClick = { syncCloud() },
                enabled = !busy,
                modifier = Modifier.weight(1f),
            ) { Text("Sync") }
            Button(
                onClick = {
                    cloudEnabled = cloudSettings.enabled
                    cloudUrl = cloudSettings.apiUrl
                    cloudToken = cloudSettings.token
                    showCloudDialog = true
                },
                modifier = Modifier.weight(1f),
            ) { Text("Cloud") }
        }

        Text(
            "$status · ${readings.count { it.status == "ok" }}/${readings.size} ok",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 2,
            modifier = Modifier.padding(bottom = 4.dp),
        )

        ReadoutScreen(
            spec = spec,
            readings = readings,
            repository = repository,
            vehicleId = vehicleId,
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        )
    }

    if (showDeviceDialog) {
        AlertDialog(
            onDismissRequest = { showDeviceDialog = false },
            title = { Text("Choose adapter") },
            text = {
                val bonded = devices
                val combined = (bonded + discovered.filter { d ->
                    bonded.none { it.address == d.address }
                }).distinctBy { it.address }

                Column {
                    if (discovering) {
                        Text(
                            "Searching for nearby devices…",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                    Text(
                        "Already-paired devices are listed first. Tap an unpaired " +
                            "device to pair it (PIN is usually 1234 or 0000).",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    if (combined.isEmpty()) {
                        Text(
                            "Nothing found yet. Make sure the ELM327 is powered " +
                                "on and in range, then wait for the scan to finish.",
                            modifier = Modifier.padding(vertical = 8.dp),
                        )
                    } else {
                        LazyColumn(modifier = Modifier.heightIn(max = 320.dp)) {
                            items(combined, key = { it.address }) { d ->
                                val isBonded = d.bondState == BluetoothDevice.BOND_BONDED
                                Text(
                                    "${d.name ?: d.address}\n${d.address}" +
                                        if (isBonded) "" else "\n[tap to pair]",
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .clickable {
                                            if (isBonded) connectTo(d) else pairWith(d)
                                        }
                                        .padding(vertical = 10.dp),
                                )
                            }
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { showDeviceDialog = false }) { Text("Cancel") }
            },
        )
    }

    if (showCloudDialog) {
        AlertDialog(
            onDismissRequest = { showCloudDialog = false },
            title = { Text("Cloud sync") },
            text = {
                Column {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(
                            checked = cloudEnabled,
                            onCheckedChange = { cloudEnabled = it },
                        )
                        Text("Upload new readings")
                    }
                    OutlinedTextField(
                        value = cloudUrl,
                        onValueChange = { cloudUrl = it },
                        label = { Text("Ingest API URL") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = cloudToken,
                        onValueChange = { cloudToken = it },
                        label = { Text("API token") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Text(
                        "Device: ${CloudPrefs.defaultSourceId(context)}\n" +
                            "The token is a revocable ingest-API credential, not " +
                            "a database password -- the phone never reaches MySQL.",
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    CloudPrefs.save(
                        context,
                        cloudSettings.copy(
                            enabled = cloudEnabled,
                            apiUrl = cloudUrl,
                            token = cloudToken,
                        ),
                    )
                    cloudSettings = CloudPrefs.load(context)
                    showCloudDialog = false
                    status = if (cloudSettings.isUsable) {
                        "Cloud sync configured"
                    } else {
                        "Cloud settings saved (URL and token still needed)"
                    }
                }) { Text("Save") }
            },
            dismissButton = {
                TextButton(onClick = { showCloudDialog = false }) { Text("Cancel") }
            },
        )
    }
}

/**
 * Run a continuous collection session against [device]: connect once, identify
 * the vehicle, discover the ECUs, then poll every [pollIntervalMs] until the
 * coroutine is cancelled. The RFCOMM socket is closed in `finally` whether the
 * loop ends by cancellation or by an error, so a dead adapter cannot leak the
 * port or leave the next connect hanging on a stale socket.
 */
private suspend fun runContinuousCollection(
    spec: Spec,
    device: BluetoothDevice,
    repository: Repository,
    pollIntervalMs: Long,
    onVehicleId: (Long) -> Unit,
    onStatus: (String) -> Unit,
    onReadings: (List<Reading>) -> Unit,
) = withContext(Dispatchers.IO) {
    val port = BluetoothSpp.connect(device)
    try {
        val protocol = Elm327Protocol(port)
        protocol.init()
        val guard = ReadOnlyGuardImpl(spec.guard)
        val profile = spec.profiles["meb"] ?: error("no meb profile")

        val collector = Collector(spec, profile, protocol, guard, repository, 1L)
        collector.setSilentRetireThreshold(3)
        // Identify first: every row this pass writes must belong to the
        // right vehicle, and loadState() loads that vehicle's retired set.
        val vin = collector.identifyVehicle()
        onVehicleId(collector.vehicleId)
        onStatus(
            if (vin != null) "Vehicle identified: $vin" else
                "VIN not readable; using the stored vehicle",
        )
        Log.i(LOG_TAG, "Vehicle identified: $vin")
        collector.loadState()
        val ecus = collector.discover()
        onStatus("ECU discovery: $ecus")
        Log.i(LOG_TAG, "ECU discovery: $ecus")

        var pass = 0
        while (true) {
            try {
                val batch = collector.collectOnce()
                pass += 1
                val ok = batch.count { it.status == "ok" }
                onReadings(batch)
                onStatus(
                    "Pass $pass: $ok of ${batch.size} DIDs answered " +
                        "(next poll in ${pollIntervalMs / 1000}s)",
                )
                Log.i(LOG_TAG, "Pass $pass: $ok of ${batch.size} DIDs answered")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                pass += 1
                onStatus("Pass $pass failed: ${e.message} — retrying")
                Log.w(LOG_TAG, "Pass $pass failed: ${e.message}")
            }
            delay(pollIntervalMs)
        }
    } finally {
        port.close()
    }
}

/**
 * Fold one pass's readings into the running display.
 *
 * The newest value for a key wins, except a failed read does not clobber a
 * value that answered before: a transient NO DATA keeps the last good number on
 * screen rather than briefly showing "—". New keys are appended in arrival
 * order; the readout re-groups by module anyway.
 */
private fun mergeReadings(current: List<Reading>, batch: List<Reading>): List<Reading> {
    val byKey = LinkedHashMap<String, Reading>()
    for (r in current) byKey[r.key] = r
    for (b in batch) {
        val prev = byKey[b.key]
        if (prev == null || b.status == "ok" || prev.status != "ok") byKey[b.key] = b
    }
    return byKey.values.toList()
}
