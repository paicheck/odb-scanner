package com.odbscanner.service

import android.bluetooth.BluetoothDevice
import com.odbscanner.collect.Collector
import com.odbscanner.db.Repository
import com.odbscanner.spec.Spec
import com.odbscanner.transport.BluetoothSpp
import com.odbscanner.transport.Elm327Protocol
import com.odbscanner.uds.ReadOnlyGuardImpl
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.io.IOException

/**
 * Run a continuous collection session against [device]: connect once, identify
 * the vehicle, discover the ECUs, then poll every [pollIntervalMs] until the
 * coroutine is cancelled or the link dies.
 *
 * A dropped Bluetooth link surfaces here as an [IOException], which is allowed
 * to propagate so the caller (the foreground service) can reconnect with
 * backoff. A per-DID timeout or refusal never reaches here: the collector folds
 * those into "no response" and keeps going. The RFCOMM socket is closed in
 * `finally` whichever way the loop ends.
 */
internal suspend fun runContinuousCollection(
    spec: Spec,
    device: BluetoothDevice,
    repository: Repository,
    pollIntervalMs: Long,
    onVehicleId: (Long) -> Unit,
    onStatus: (String) -> Unit,
) = withContext(Dispatchers.IO) {
    val port = BluetoothSpp.connect(device)
    try {
        val guard = ReadOnlyGuardImpl(spec.guard)
        val protocol = Elm327Protocol(port, guard)
        protocol.init()
        val profile = spec.profiles["meb"] ?: error("no meb profile")

        val collector = Collector(spec, profile, protocol, guard, repository, 1L)
        collector.setSilentRetireThreshold(3)
        val vin = awaitVehicle(collector, pollIntervalMs.coerceAtLeast(10_000L), onStatus)
        onVehicleId(collector.vehicleId)
        onStatus("Vehicle identified: $vin")
        collector.loadState()
        val ecus = collector.discover()
        onStatus("ECU discovery: $ecus")

        var pass = 0
        while (true) {
            try {
                val batch = collector.collectOnce()
                pass += 1
                val ok = batch.count { it.status == "ok" }
                onStatus(
                    "Pass $pass: $ok of ${batch.size} DIDs answered " +
                        "(next poll in ${pollIntervalMs / 1000}s)",
                )
            } catch (e: CancellationException) {
                throw e
            } catch (e: IOException) {
                // The socket died mid-pass: not a transient read, so let the
                // caller reconnect rather than spin on a broken pipe.
                throw e
            } catch (e: Exception) {
                pass += 1
                onStatus("Pass $pass failed: ${e.message} — retrying")
            }
            delay(pollIntervalMs)
        }
    } finally {
        port.close()
    }
}

/**
 * Read the VIN until the car answers; nothing is collected before that.
 *
 * Collection used to go ahead under the fallback vehicle id 1 when the VIN
 * could not be read -- the usual case for a car phone booting with the
 * ignition off. Those rows (and DID retirements and sessions) then belonged to
 * whichever car owns row 1, or, on an empty database, to whichever VIN was read
 * first. Waiting costs nothing: an asleep car has nothing to record anyway.
 */
internal suspend fun awaitVehicle(
    collector: Collector,
    retryDelayMs: Long,
    onStatus: (String) -> Unit,
): String {
    while (true) {
        collector.identifyVehicle()?.let { return it }
        onStatus(
            "VIN not readable yet (car asleep or out of reach); nothing is " +
                "recorded until the vehicle is identified. Retrying.",
        )
        delay(retryDelayMs)
    }
}
