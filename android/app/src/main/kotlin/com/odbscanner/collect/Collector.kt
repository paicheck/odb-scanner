package com.odbscanner.collect

import com.odbscanner.db.Repository
import com.odbscanner.spec.Decoder
import com.odbscanner.spec.MeasurementSpec
import com.odbscanner.spec.ProfileSpec
import com.odbscanner.spec.Spec
import com.odbscanner.transport.Elm327Protocol
import com.odbscanner.uds.NegativeResponseError
import com.odbscanner.uds.NoResponseError
import com.odbscanner.uds.ReadOnlyGuardImpl
import com.odbscanner.uds.Uds
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import java.time.Instant

/** One decoded reading, with the raw bytes preserved. */
data class Reading(
    val key: String,
    val value: Any?,
    val raw: String,          // uppercase hex of the DID data record
    val status: String,       // "ok" | "nrc-0x31" | "no-response" | "retired"
)

/**
 * The read-only collection loop, ported from `collector.py`.
 *
 * Discovery probes each ECU's first DID; polling reads every DID in the profile
 * and decodes it against the spec. DIDs that answer NRC 0x31 (definitively
 * unsupported) or that stay silent without ever succeeding are retired so they
 * are not re-polled every cycle.
 *
 * When a [repository] is given, readings and retired DIDs are persisted so the
 * data survives an app restart, every transmitted request is written to
 * `tx_log` (the safety manifest), and the retired set + ever-succeeded set are
 * loaded from it on [loadState].
 */
class Collector(
    private val spec: Spec,
    private val profile: ProfileSpec,
    private val protocol: Elm327Protocol,
    private val guard: ReadOnlyGuardImpl,
    private val repository: Repository? = null,
    vehicleId: Long = 1L,
    /**
     * `collector.slow_poll_interval` in Python. The cells and the other slow
     * DIDs are read on their own cadence: reading 108 cell DIDs on every fast
     * pass would multiply a ~1 s cycle by a factor of ten to learn a voltage
     * that moves in millivolts per minute.
     */
    private val slowIntervalMs: Long = 60_000L,
    /** Injectable clock so the cadence is testable without sleeping. */
    private val nowMs: () -> Long = System::currentTimeMillis,
) {
    private val retired = mutableSetOf<Pair<String, Int>>()
    private val everSucceeded = mutableSetOf<Pair<String, Int>>()
    private val silentCount = mutableMapOf<Pair<String, Int>, Int>()
    private var silentRetireThreshold = 3
    private var tsNow: String = ""

    /**
     * The vehicle these rows belong to. Starts at the caller's fallback and is
     * replaced by the real row when [identifyVehicle] reads the VIN. The old
     * hard-coded 1 filed every car's history under the first vehicle row.
     */
    var vehicleId: Long = vehicleId
        private set

    /** The VIN read at connect, when the car answered. */
    var vin: String? = null
        private set

    private var lastSlowMs: Long? = null

    fun setSilentRetireThreshold(n: Int) { silentRetireThreshold = n }

    /** Load retired + ever-succeeded DIDs from the repository (call before collecting). */
    suspend fun loadState() {
        repository?.let {
            retired.clear()
            retired.addAll(it.unsupportedKeys(vehicleId))
            everSucceeded.clear()
            everSucceeded.addAll(it.everSucceededKeys(vehicleId))
        }
    }

    /**
     * Read the VIN once at connect and point [vehicleId] at its row.
     *
     * The VIN is a spec measurement (`vin`, `22F802` on the vehicle-info
     * module), not a hard-coded OBD mode 09 request: the gateway that answers
     * on this car is the one the spec documents. A malformed answer is ignored
     * rather than written down -- a partial VIN would create a second vehicle
     * row and split the history.
     */
    suspend fun identifyVehicle(): String? {
        val measurement = profile.measurements.firstOrNull { it.key == "vin" }
            ?: profile.measurements.firstOrNull { it.did == 0xF802 }
            ?: return null
        val did = measurement.did ?: return null
        tsNow = Instant.now().toString()
        val record = try {
            readRaw(measurement, did)
        } catch (_: NegativeResponseError) {
            return null
        } catch (_: NoResponseError) {
            return null
        }
        val text = (Decoder.decode(measurement.decode, record) as? String)?.trim()
        // A VIN is exactly 17 alphanumeric characters (ISO 3779, excluding
        // I/O/Q). Anything else is a framing or addressing error, not a car.
        if (text == null || text.length != 17 || !text.all { it.isLetterOrDigit() }) {
            return null
        }
        vin = text
        repository?.let { vehicleId = it.ensureVehicle(text) }
        return text
    }

    /** Probe each ECU with its first non-cell DID; returns key -> status. */
    suspend fun discover(): Map<String, String> {
        tsNow = Instant.now().toString()
        val results = mutableMapOf<String, String>()
        for ((key, ecu) in spec.ecus) {
            val first = profile.measurements.firstOrNull {
                it.ecuKey == key && it.did != null && !it.key.startsWith("cell_v_")
            } ?: continue
            currentCoroutineContext().ensureActive()
            results[key] = try {
                readRaw(first, first.did!!)
                "responder"
            } catch (e: NegativeResponseError) {
                "nrc-0x" + e.nrc.toString(16).padStart(2, '0')
            } catch (_: NoResponseError) {
                "no-response"
            }
        }
        return results
    }

    /**
     * Read and decode every measurement due in this pass; persists when a repo
     * is set.
     *
     * The fast DIDs run every pass. The slow ones -- including all 108 cell
     * voltages, which are ordinary spec measurements with the `cell_voltage`
     * decoder -- run only when [slowPhaseDue]. They are never read twice in the
     * same rotation: the old separate cell sweep re-read exactly the DIDs the
     * slow pass was about to read anyway, so half the answers were thrown away
     * and the car was asked twice.
     */
    suspend fun collectOnce(): List<Reading> {
        tsNow = Instant.now().toString()
        val slowDue = slowPhaseDue()
        val readings = mutableListOf<Reading>()
        for (m in profile.measurements) {
            currentCoroutineContext().ensureActive()
            val did = m.did ?: continue
            if (m.slow && !slowDue) continue
            if (retired.contains(m.ecuKey to did)) continue
            readings.add(poll(m, did))
        }
        // Readings were persisted per-DID inside poll() (and retired DIDs inside
        // retire()), so the data survives an app restart.
        return readings
    }

    /** True when the slow rotation is due; always true on the first pass. */
    internal fun slowPhaseDue(): Boolean {
        val now = nowMs()
        val last = lastSlowMs
        if (last != null && now - last < slowIntervalMs) return false
        lastSlowMs = now
        return true
    }

    private suspend fun poll(m: MeasurementSpec, did: Int): Reading {
        return try {
            val record = readRaw(m, did)
            val value = Decoder.decode(m.decode, record)
            _recordSuccess(m, record, tsNow)
            everSucceeded.add(m.ecuKey to did)
            silentCount.remove(m.ecuKey to did)
            Reading(m.key, value, toHex(record), "ok")
        } catch (e: NegativeResponseError) {
            _recordFailure(m, e.toString(), tsNow)
            if (e.nrc == 0x31) retire(m.ecuKey, did, tsNow)
            Reading(m.key, null, "", "nrc-0x" + e.nrc.toString(16).padStart(2, '0'))
        } catch (_: NoResponseError) {
            _recordFailure(m, "No/undecodable response to DID 0x${hex4(did)}", tsNow)
            noteSilent(m.ecuKey, did, tsNow)
            Reading(m.key, null, "", "no-response")
        }
    }

    /**
     * Send one read and mirror both directions into `tx_log`.
     *
     * This is the safety manifest, not debug output: it records the request
     * that actually went out, so "the collector is read-only" is auditable
     * after the fact rather than a claim about the source. The record is the
     * DID data only (the DID echo is stripped) -- the same shape the Python
     * connection logs.
     */
    private suspend fun readRaw(m: MeasurementSpec, did: Int): ByteArray {
        val request = toHex(guard.buildReadDidRequest(did))
        logTx("TX", m.ecuKey, request,
            "UDS 0x22 DID 0x${hex4(did)} (${m.key}, ${m.ecuKey}) - read-only")
        return try {
            val record = Uds.readDid(protocol, guard, spec.ecu(m.ecuKey), did)
            logTx("RX", m.ecuKey, toHex(record), "response")
            record
        } catch (e: NegativeResponseError) {
            logTx("RX", m.ecuKey, e.toString(), "response")
            throw e
        } catch (e: NoResponseError) {
            logTx("RX", m.ecuKey, e.toString(), "response")
            throw e
        }
    }

    private suspend fun _recordSuccess(m: MeasurementSpec, record: ByteArray, ts: String) {
        val (value, text) = measurementColumns(Decoder.decode(m.decode, record))
        repository?.recordMeasurement(
            vehicleId = vehicleId, ts = ts, key = m.key, ecu = m.ecuKey,
            service = "UDS-0x22", pid = m.did?.let { "0x${hex4(it)}" } ?: "0x0000",
            unit = m.unit, provenance = m.provenance,
            decodingMethod = if (m.decode != null) "builtin" else "raw-only",
            docStatus = m.docStatus, rawResponse = toHex(record),
            value = value, textValue = text, success = true, error = null,
        )
    }

    private suspend fun _recordFailure(m: MeasurementSpec, error: String, ts: String) {
        repository?.recordMeasurement(
            vehicleId = vehicleId, ts = ts, key = m.key, ecu = m.ecuKey,
            service = "UDS-0x22", pid = m.did?.let { "0x${hex4(it)}" } ?: "0x0000",
            unit = m.unit, provenance = m.provenance,
            decodingMethod = if (m.decode != null) "builtin" else "raw-only",
            docStatus = m.docStatus, rawResponse = "", value = null, textValue = null,
            success = false, error = error,
        )
    }

    private suspend fun logTx(direction: String, ecuKey: String?, payload: String, purpose: String) {
        repository?.recordTx(
            ts = tsNow, direction = direction, ecu = ecuKey,
            payload = payload, purpose = purpose,
        )
    }

    private suspend fun retire(ecuKey: String, did: Int, ts: String) {
        retired.add(ecuKey to did)
        repository?.markUnsupported(vehicleId, ecuKey, did, ts)
    }

    private suspend fun noteSilent(ecuKey: String, did: Int, ts: String) {
        val key = ecuKey to did
        if (everSucceeded.contains(key)) return  // answered before -> car asleep
        val count = (silentCount[key] ?: 0) + 1
        silentCount[key] = count
        if (count >= silentRetireThreshold) {
            retire(ecuKey, did, ts)
            silentCount.remove(key)
        }
    }

    private fun hex4(value: Int) = value.toString(16).uppercase().padStart(4, '0')

    private fun toHex(bytes: ByteArray): String =
        bytes.joinToString("") { (it.toInt() and 0xFF).toString(16).padStart(2, '0').uppercase() }
}

/**
 * Split one decoded value into the store's two value columns.
 *
 * The Python row keeps text in `measurements.value` (SQLite is dynamically
 * typed) and the cloud splitter moves it to `text_value` at upload time. Room's
 * `value` is a DOUBLE, so the phone has to make that split at write time or the
 * value is lost. Structured decodes (`pair`, `energy_counters`, ...) become
 * JSON in `text_value` with a NULL number, exactly like `collector.py`'s
 * `_record`; booleans stay 0/1 numbers, as `cloud/mapping.split_value` does.
 */
internal fun measurementColumns(value: Any?): Pair<Double?, String?> = when (value) {
    null -> null to null
    is Boolean -> (if (value) 1.0 else 0.0) to null
    is Number -> {
        val number = value.toDouble()
        if (number.isFinite()) number to null else null to number.toString()
    }
    is String -> {
        // A string that reads as a number is a number: "31.4" means 31.4.
        // Anything else is preserved verbatim in text_value, never coerced.
        val number = value.trim().toDoubleOrNull()
        if (number != null && number.isFinite()) number to null else null to value
    }
    is Map<*, *> -> null to jsonValue(value)
    else -> null to value.toString()
}

/** Deterministic, dependency-free JSON for a structured decode. */
private fun jsonValue(value: Any?): String = when (value) {
    null -> "null"
    is Boolean, is Number -> value.toString()
    is Map<*, *> -> value.entries.joinToString(",", "{", "}") { (k, v) ->
        jsonValue(k?.toString()) + ":" + jsonValue(v)
    }
    else -> "\"" + value.toString().escapeJson() + "\""
}

private fun String.escapeJson(): String = buildString {
    for (c in this@escapeJson) when (c) {
        '"' -> append("\\\"")
        '\\' -> append("\\\\")
        '\n' -> append("\\n")
        '\r' -> append("\\r")
        '\t' -> append("\\t")
        else -> if (c < ' ') append("\\u%04x".format(c.code)) else append(c)
    }
}
