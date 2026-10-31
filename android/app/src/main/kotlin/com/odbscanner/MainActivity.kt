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
import androidx.compose.runtime.collectAsState
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
import com.odbscanner.ai.WakeWordService
import com.odbscanner.collect.Reading
import com.odbscanner.db.AppDatabase
import com.odbscanner.db.MeasurementEntity
import com.odbscanner.db.Repository
import com.odbscanner.net.CloudPrefs
import com.odbscanner.net.CloudSync
import com.odbscanner.net.CloudSyncException
import com.odbscanner.service.CollectorPrefs
import com.odbscanner.service.CollectorService
import com.odbscanner.service.CollectorStatusBus
import com.odbscanner.spec.Spec
import com.odbscanner.spec.SpecLoader
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // The wake word survives a reboot or a killed process: back on when the
        // app is next opened (a microphone service may only start from here).
        WakeWordService.resumeIfOn(this)
        // A trip that was being driven when the app was stopped: planned again
        // from here and watched (route/TripStore.kt).
        com.odbscanner.route.TripMonitor.restoreInBackground(this)
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
    // Devices found by the discovery scan, plus a flag so the dialog can show a
    // "searching" line. Bonded devices are read separately and merged below.
    var discovered by remember { mutableStateOf(listOf<BluetoothDevice>()) }
    var discovering by remember { mutableStateOf(false) }

    // The foreground service owns the collection loop now; the activity observes
    // its state and reads results back out of the database.
    val serviceRunning by CollectorStatusBus.running.collectAsState()
    val serviceMessage by CollectorStatusBus.message.collectAsState()

    // Cloud upload settings live in SharedPreferences so the endpoint and token
    // can be changed on the device without rebuilding the app.
    var cloudSettings by remember { mutableStateOf(CloudPrefs.load(context)) }
    var showCloudDialog by remember { mutableStateOf(false) }
    var showRouteDialog by remember { mutableStateOf(false) }
    var showAssistant by remember { mutableStateOf(false) }
    var cloudEnabled by remember { mutableStateOf(false) }
    var cloudUrl by remember { mutableStateOf("") }
    var cloudToken by remember { mutableStateOf("") }
    // Whether to start collecting on boot (a dedicated car phone).
    var autoStart by remember { mutableStateOf(CollectorPrefs.autoStart(context)) }

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

    suspend fun loadLatest() {
        try {
            vehicleId = repository.firstVehicleId() ?: 1L
            val latest = repository.latestMeasurements(vehicleId)
            readings = latest.map { it.toReading() }
        } catch (_: Exception) {
        }
    }

    fun reloadStored() {
        // Reload from the persisted store without re-collecting.
        scope.launch {
            loadLatest()
            status = "Loaded ${readings.size} stored readings"
        }
    }

    // While the foreground service is collecting, refresh the display from the
    // database it is writing to. This is the activity's only link to the data:
    // the service owns the socket and the collection coroutine.
    LaunchedEffect(serviceRunning) {
        if (serviceRunning) {
            while (true) {
                loadLatest()
                delay(3_000)
            }
        } else {
            loadLatest()
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

    val notificationLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { /* the foreground service runs regardless; this only controls the drawer */ }

    fun startServiceAndNotify() {
        // The notification is how Android lets a foreground service advertise
        // that it is running; ask for it but never block collection on it.
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
                PackageManager.PERMISSION_GRANTED
        ) {
            notificationLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        CollectorService.start(context)
    }

    fun startConnectFlow() {
        if (!hasBluetoothPermission(context)) {
            permissionLauncher.launch(BLUETOOTH_PERMISSIONS)
            return
        }
        if (CollectorPrefs.adapterAddress(context) != null) {
            startServiceAndNotify()
        } else {
            devices = pairedDevices()
            discovered = emptyList()
            showDeviceDialog = true
        }
    }

    /** Remember this adapter and hand collection to the foreground service. */
    fun connectTo(device: BluetoothDevice) {
        showDeviceDialog = false
        CollectorPrefs.setAdapter(context, device.address)
        startServiceAndNotify()
    }

    /** Stop the foreground service and push anything pending. */
    fun stopCollection() {
        CollectorService.stop(context)
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

    val displayStatus = if (serviceRunning) serviceMessage else status

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 12.dp, vertical = 8.dp),
    ) {
        Text(
            "ODB Scanner (read-only) — MEB ${spec.specVersion}",
            style = MaterialTheme.typography.titleMedium,
        )

        // Navigation running behind the main screen: one tap back to it.
        NavigationReturnBar(onOpen = { com.odbscanner.route.Navigator.showOnPhone.value = true })

        Row(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            modifier = Modifier.padding(vertical = 6.dp),
        ) {
            Button(
                onClick = { startConnectFlow() },
                enabled = !serviceRunning,
                modifier = Modifier.weight(1f),
            ) { Text("Connect") }
            Button(
                onClick = { stopCollection() },
                enabled = serviceRunning,
                modifier = Modifier.weight(1f),
            ) { Text("Stop") }
            Button(
                onClick = { reloadStored() },
                enabled = !serviceRunning,
                modifier = Modifier.weight(1f),
            ) { Text("Reload") }
            Button(
                onClick = { readings = emptyList(); status = "Idle" },
                enabled = !serviceRunning,
                modifier = Modifier.weight(1f),
            ) { Text("Clear") }
        }

        Row(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            modifier = Modifier.padding(bottom = 4.dp),
        ) {
            Button(
                onClick = { syncCloud() },
                enabled = !serviceRunning,
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
            Button(
                onClick = { showRouteDialog = true },
                modifier = Modifier.weight(1f),
            ) { Text("Route") }
            Button(
                onClick = { showAssistant = true },
                modifier = Modifier.weight(1f),
            ) { Text("Ask AI", maxLines = 1) }
        }

        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(bottom = 2.dp),
        ) {
            Checkbox(
                checked = autoStart,
                onCheckedChange = {
                    autoStart = it
                    CollectorPrefs.setAutoStart(context, it)
                },
            )
            Text("Start on boot (car phone)", style = MaterialTheme.typography.bodySmall)
        }

        Text(
            "$displayStatus · ${readings.count { it.status == "ok" }}/${readings.size} ok",
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

    if (showAssistant) {
        AssistantDialog(onDismiss = { showAssistant = false })
    }
    if (showRouteDialog) {
        RouteDialog(onDismiss = { showRouteDialog = false })
    }
    val navigating by com.odbscanner.route.Navigator.state.collectAsState()
    val showNav by com.odbscanner.route.Navigator.showOnPhone.collectAsState()
    if (navigating != null && showNav) {
        NavigationScreen(onClose = { com.odbscanner.route.Navigator.showOnPhone.value = false })
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
