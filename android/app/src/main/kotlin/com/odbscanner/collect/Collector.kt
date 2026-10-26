package com.odbscanner.collect

import android.util.Log
import com.odbscanner.db.Repository
import com.odbscanner.spec.Decoder
import com.odbscanner.spec.MeasurementSpec
import com.odbscanner.spec.ProfileSpec
import com.odbscanner.spec.Spec
import com.odbscanner.transport.Elm327Protocol
import com.odbscanner.transport.Elm327RefusedError
import com.odbscanner.transport.Elm327TimeoutError
import com.odbscanner.uds.NegativeResponseError
import com.odbscanner.uds.NoResponseError
import com.odbscanner.uds.ReadOnlyGuardImpl
import com.odbscanner.uds.Uds
import com.odbscanner.uds.UnexpectedResponseError
import com.odbscanner.uds.UnreachableError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import java.io.IOException
import java.time.Instant
import kotlin.math.pow
import kotlin.math.round

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
    // Reachability guard for silent-DID retirement, ported from collector.py
    // (_phase_ok_ecus / _pending_silent): silent reads are buffered for the
    // pass and only counted for modules that answered something in it. On
    // 2026-10-17 an unreachable bus retired 72 DIDs in one pass on the desktop;
    // a car-phone that boots with the ignition off would do the same here.
    private val phaseOkEcus = mutableSetOf<String>()
    private val pendingSilent = mutableMapOf<Pair<String, Int>, Int>()
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

    /** Latest decoded value per key, carried across passes for derived values. */
    private val snapshot = mutableMapOf<String, Any?>()

    /** The charging session currently open, if any (opened/closed by charge_mode). */
    private var activeSessionId: Long? = null

    fun setSilentRetireThreshold(n: Int) { silentRetireThreshold = n }

    /** Load retired + ever-succeeded DIDs and adopt any session left open. */
    suspend fun loadState() {
        repository?.let {
            retired.clear()
            retired.addAll(it.unsupportedKeys(vehicleId))
            everSucceeded.clear()
            everSucceeded.addAll(it.everSucceededKeys(vehicleId))
            // A previous run may have been killed mid-charge; adopting its open
            // session means this run closes it rather than opening a second one.
            activeSessionId = it.openSessionId(vehicleId)
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
        repository?.let {
            vehicleId = it.ensureVehicle(text)
            it.addEvent(vehicleId, tsNow, "adapter", "VIN read = $text")
        }
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
        phaseOkEcus.clear()
        pendingSilent.clear()
        // The spec lists DIDs alphabetically, so reading it as-is makes almost
        // every DID belong to a different module than the one before it -- the
        // ELM327 would be told to switch addressing ~287 times a pass, and this
        // clone stalls on the repeated ATCP/ATSH/ATCRA churn (that is how the
        // whole 29-bit tail after one stalled DID went unread). Group by module
        // so the preamble is sent once per module instead of once per DID.
        val byEcu = profile.measurements.groupBy { it.ecuKey }
        for (ecuKey in spec.ecus.keys) {
            val measurements = byEcu[ecuKey] ?: continue
            for (m in measurements) {
                currentCoroutineContext().ensureActive()
                val did = m.did ?: continue
                if (m.slow && !slowDue) continue
                if (retired.contains(m.ecuKey to did)) continue
                readings.add(poll(m, did))
            }
        }
        endSilencePhase(tsNow)
        // Computed values and charging state are derived from what this pass
        // just read (and from carried-forward values), every pass; DTCs are
        // re-read once per slow rotation.
        recordDerived(tsNow)
        trackCharging(tsNow)
        if (slowDue) readDtcs()
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
            if (value != null) snapshot[m.key] = value
            _recordSuccess(m, record, tsNow)
            everSucceeded.add(m.ecuKey to did)
            silentCount.remove(m.ecuKey to did)
            phaseOkEcus.add(m.ecuKey)
            Reading(m.key, value, toHex(record), "ok")
        } catch (e: NegativeResponseError) {
            _recordFailure(m, e.toString(), tsNow)
            if (e.nrc == 0x31) retire(m.ecuKey, did, tsNow)
            Reading(m.key, null, "", "nrc-0x" + e.nrc.toString(16).padStart(2, '0'))
        } catch (e: NoResponseError) {
            _recordFailure(m, e.message ?: "No/undecodable response to DID 0x${hex4(did)}", tsNow)
            // Only genuine NO DATA is evidence about the DID. A refusal, a
            // timeout or an unexpected reply means the module was not reached
            // or answered something else -- never a reason to retire.
            if (e !is UnreachableError && e !is UnexpectedResponseError) {
                noteSilent(m.ecuKey, did)
            }
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
        } catch (e: CancellationException) {
            throw e
        } catch (e: Elm327TimeoutError) {
            // Adapter alive but this one command did not answer in time: the DID
            // is unreadable, not the link.
            Log.w("odbscanner", "DID 0x${hex4(did)} (${m.key}) timeout: $e")
            logTx("RX", m.ecuKey, e.toString(), "response")
            throw UnreachableError(did, e)
        } catch (e: Elm327RefusedError) {
            Log.w("odbscanner", "DID 0x${hex4(did)} (${m.key}) refused: $e")
            logTx("RX", m.ecuKey, e.toString(), "response")
            throw UnreachableError(did, e)
        } catch (e: IOException) {
            // The Bluetooth link is dead; propagate so the caller reconnects
            // instead of spinning through the whole DID map on a broken pipe.
            logTx("RX", m.ecuKey, e.toString(), "response")
            throw e
        } catch (e: Exception) {
            Log.w("odbscanner", "DID 0x${hex4(did)} (${m.key}) unreadable: $e")
            logTx("RX", m.ecuKey, e.toString(), "response")
            throw UnreachableError(did, e)
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

    /** Buffer one NO DATA read for [endSilencePhase] to judge. */
    private fun noteSilent(ecuKey: String, did: Int) {
        val key = ecuKey to did
        if (everSucceeded.contains(key)) return  // answered before -> car asleep
        pendingSilent[key] = (pendingSilent[key] ?: 0) + 1
    }

    /**
     * Count this pass's silent reads -- but only for modules that produced at
     * least one successful read in the same pass. A module that said nothing
     * at all was unreachable, and "we never reached it" is not evidence about
     * what it implements.
     */
    private suspend fun endSilencePhase(ts: String) {
        for ((key, silent) in pendingSilent) {
            if (key.first !in phaseOkEcus) continue
            val count = (silentCount[key] ?: 0) + silent
            if (count >= silentRetireThreshold) {
                retire(key.first, key.second, ts)
                silentCount.remove(key)
            } else {
                silentCount[key] = count
            }
        }
        pendingSilent.clear()
    }

    /** Compute the derived values (power, spreads, SoH) and persist them. */
    private suspend fun recordDerived(ts: String) {
        repository ?: return
        for (d in computeDerived(snapshot)) {
            repository.recordMeasurement(
                vehicleId = vehicleId, ts = ts, key = d.key, ecu = d.ecu,
                service = "calc", pid = "-", unit = d.unit, provenance = d.provenance,
                decodingMethod = d.decodingMethod, docStatus = "calculated",
                rawResponse = "", value = d.value, textValue = null,
                success = true, error = null,
            )
        }
    }

    /** Open/close a charging session from the charge_mode DID. */
    private suspend fun trackCharging(ts: String) {
        repository ?: return
        val mode = (snapshot["charge_mode"] as? Number)?.toInt()
        // Unknown is not the same as "not charging": a missed read must not
        // close a live session on no evidence.
        if (mode == null) return
        val charging = mode in 1..3
        when {
            charging && activeSessionId == null -> {
                val type = if (mode == 2) "DC" else "AC"
                activeSessionId = repository.openSession(
                    vehicleId, ts, type, (snapshot["soc_normal"] as? Number)?.toDouble(),
                )
                repository.addEvent(
                    vehicleId, ts, "charge-start", "$type charging detected (mode=$mode)",
                )
            }
            !charging && activeSessionId != null -> {
                repository.closeSession(
                    activeSessionId!!, ts, (snapshot["soc_normal"] as? Number)?.toDouble(),
                )
                repository.addEvent(vehicleId, ts, "charge-end", "Charging ended")
                activeSessionId = null
            }
        }
    }

    /**
     * Read legislated OBD-II DTCs (mode 03) and UDS DTCs (0x19 0x02, functional
     * with status mask 0x8F = warning+active+pending+confirmed), and upsert
     * them. Both are read-only and validated by the guard before anything is
     * sent. Returns how many DTCs were seen.
     */
    suspend fun readDtcs(): Int {
        repository ?: return 0
        tsNow = Instant.now().toString()
        var count = 0

        // Legislated OBD-II DTCs.
        try {
            // The bytes logged are the bytes sendRead validates and sends.
            val request = byteArrayOf(0x03)
            logTx("TX", null, toHex(request), "OBD mode 03 - read emissions DTCs (read-only)")
            protocol.setModule(null)
            val lines = protocol.sendRead(request)
            logTx("RX", null, lines.joinToString(" | "), "response")
            for (payload in Uds.parsePayloadsBySource(lines).values) {
                payload ?: continue
                for (code in parseObd03DtcResponse(payload)) {
                    repository.upsertDtc(vehicleId, tsNow, "OBD-II", code, "stored")
                    count++
                }
            }
        } catch (_: Exception) {
            Log.w("odbscanner", "OBD-II DTC read failed")
        }

        // UDS DTCs: one functional read, attributed per responding ECU.
        try {
            val request = byteArrayOf(0x19, 0x02, 0x8F.toByte())
            logTx("TX", null, toHex(request),
                "UDS 0x19 0x02 ReadDTCInformation (mask 0x8F) - functional, read-only")
            protocol.setModule(null)
            val lines = protocol.sendRead(request)
            logTx("RX", null, lines.joinToString(" | "), "response")
            for ((src, payload) in Uds.parsePayloadsBySource(lines)) {
                payload ?: continue
                val label = src?.let { ecuLabelForSource(it) } ?: "uds"
                for ((code, statusByte) in parseUdsDtcResponse(payload)) {
                    repository.upsertDtc(vehicleId, tsNow, label, code, dtcStatusLabel(statusByte))
                    count++
                }
            }
        } catch (_: Exception) {
            Log.w("odbscanner", "UDS DTC read failed")
        }
        return count
    }

    private fun ecuLabelForSource(src: Int): String {
        for ((key, ecu) in spec.ecus) {
            if (ecu.rx == src || ecu.rx29 == src) return key
        }
        return "uds-" + src.toString(16).uppercase()
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

/** One computed value to persist as a `calc` measurement. */
internal data class DerivedValue(
    val key: String,
    val ecu: String,
    val unit: String,
    val provenance: String,
    val decodingMethod: String,
    val formula: String,
    val value: Double,
)

/** Marketed HV energy of the ID.3 58 kWh pack (Wh), for the estimated SoH. */
private const val NOMINAL_ENERGY_WH = 58_000.0

private data class PowerFormula(
    val key: String, val voltageKey: String, val currentKey: String,
    val ecu: String, val unit: String, val scale: Double,
)

private fun roundTo(value: Double, decimals: Int): Double {
    val factor = 10.0.pow(decimals)
    return round(value * factor) / factor
}

/**
 * Derive the calculated/estimated values from the latest decoded snapshot,
 * mirroring `collector.py`'s computed block + `_record_lv_derived` +
 * `_record_spreads` + the SoH fallback. Pure, so it is testable without a
 * repository or a car.
 */
internal fun computeDerived(snapshot: Map<String, Any?>): List<DerivedValue> {
    val out = mutableListOf<DerivedValue>()
    fun num(key: String): Double? = (snapshot[key] as? Number)?.toDouble()
    fun pairValue(key: String): Double? =
        (snapshot[key] as? Map<*, *>)?.get("value")?.let { (it as? Number)?.toDouble() }

    num("pack_voltage")?.let { v ->
        num("pack_current")?.let { c ->
            out += DerivedValue("pack_power_kw", "bat_mgmt", "kW", "calculated",
                "calculated", "voltage * current / 1000", roundTo(v * c / 1000.0, 3))
        }
    }
    num("cell_voltage_max")?.let { max ->
        num("cell_voltage_min")?.let { min ->
            out += DerivedValue("cell_delta_mv", "bat_mgmt", "mV", "calculated",
                "calculated", "(max - min) * 1000", roundTo((max - min) * 1000.0, 1))
        }
    }

    // Low-voltage power (voltage x current), labelled calculated.
    listOf(
        PowerFormula("aux_12v_power_w", "aux_12v_voltage", "aux_12v_current",
            "bat_mgmt", "W", 1.0),
        PowerFormula("dcdc_power_kw", "dcdc_voltage", "dcdc_current",
            "dcdc", "kW", 0.001),
        PowerFormula("dcdc_gw_power_kw", "dcdc_gw_voltage", "dcdc_gw_current",
            "energy", "kW", 0.001),
    ).forEach { f ->
        num(f.voltageKey)?.let { v ->
            num(f.currentKey)?.let { c ->
                out += DerivedValue(f.key, f.ecu, f.unit, "calculated", "calculated",
                    "voltage * current", roundTo(v * c * f.scale, 3))
            }
        }
    }

    val socMax = pairValue("cell_soc_max")
    val socMin = pairValue("cell_soc_min")
    if (socMax != null && socMin != null) {
        out += DerivedValue("cell_soc_spread_pct", "bat_mgmt", "%", "calculated",
            "calculated", "max cell SoC - min cell SoC", roundTo(socMax - socMin, 3))
    }
    num("bat_temp_max")?.let { max ->
        num("bat_temp_min")?.let { min ->
            out += DerivedValue("battery_temp_spread_c", "bat_mgmt", "degC", "calculated",
                "calculated", "max - min", roundTo(max - min, 3))
        }
    }
    // MEB packs expose no capacity (CAC) DID; fall back to rated-vs-marketed
    // energy, labelled estimated (decoders/bms.soh_pct_from_energy).
    num("hv_energy_max")?.takeIf { it > 0 }?.let { maxWh ->
        out += DerivedValue("soh_pct", "bat_mgmt", "%", "estimated", "estimated",
            "hv_energy_max / 58000 * 100", roundTo(100.0 * maxWh / NOMINAL_ENERGY_WH, 1))
    }
    return out
}
