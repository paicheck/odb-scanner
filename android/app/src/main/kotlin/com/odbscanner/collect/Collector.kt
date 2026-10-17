package com.odbscanner.collect

import com.odbscanner.db.Repository
import com.odbscanner.spec.CellVoltages
import com.odbscanner.spec.Decode
import com.odbscanner.spec.Decoder
import com.odbscanner.spec.EcuSpec
import com.odbscanner.spec.MeasurementSpec
import com.odbscanner.spec.ProfileSpec
import com.odbscanner.spec.Spec
import com.odbscanner.transport.Elm327Protocol
import com.odbscanner.uds.NegativeResponseError
import com.odbscanner.uds.NoResponseError
import com.odbscanner.uds.ReadOnlyGuardImpl
import com.odbscanner.uds.Uds
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
 * data survives an app restart, and the retired set + ever-succeeded set are
 * loaded from it on [loadState].
 */
class Collector(
    private val spec: Spec,
    private val profile: ProfileSpec,
    private val protocol: Elm327Protocol,
    private val guard: ReadOnlyGuardImpl,
    private val repository: Repository? = null,
    private val vehicleId: Long = 1L,
) {
    private val retired = mutableSetOf<Pair<String, Int>>()
    private val everSucceeded = mutableSetOf<Pair<String, Int>>()
    private val silentCount = mutableMapOf<Pair<String, Int>, Int>()
    private var silentRetireThreshold = 3
    private var tsNow: String = ""

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

    /** Probe each ECU with its first DID; returns key -> status. */
    fun discover(): Map<String, String> {
        val results = mutableMapOf<String, String>()
        for ((key, ecu) in spec.ecus) {
            val first = profile.measurements.firstOrNull { it.ecuKey == key && it.did != null }
                ?: continue
            results[key] = try {
                Uds.readDid(protocol, guard, ecu, first.did!!)
                "responder"
            } catch (e: NegativeResponseError) {
                "nrc-0x" + e.nrc.toString(16).padStart(2, '0')
            } catch (_: NoResponseError) {
                "no-response"
            }
        }
        return results
    }

    /** Read and decode every measurement in one pass; persists when a repo is set. */
    suspend fun collectOnce(): List<Reading> {
        tsNow = Instant.now().toString()
        val readings = mutableListOf<Reading>()
        for (m in profile.measurements) {
            val did = m.did ?: continue
            if (retired.contains(m.ecuKey to did)) continue
            val ecu = spec.ecu(m.ecuKey)
            val reading = poll(m, ecu, did)
            readings.add(reading)
        }
        profile.cellVoltages?.let { cv ->
            readings.addAll(sweepCells(cv))
        }
        // Readings were persisted per-DID inside poll() (and retired DIDs inside
        // retire()), so the data survives an app restart.
        return readings
    }

    private suspend fun poll(m: MeasurementSpec, ecu: EcuSpec, did: Int): Reading {
        return try {
            val record = Uds.readDid(protocol, guard, ecu, did)
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
            _recordFailure(m, "No/undecodable response to DID 0x${did.toString(16).padStart(4, '0')}", tsNow)
            noteSilent(m.ecuKey, did, tsNow)
            Reading(m.key, null, "", "no-response")
        }
    }

    private suspend fun _recordSuccess(m: MeasurementSpec, record: ByteArray, ts: String) {
        repository?.recordMeasurement(
            vehicleId = vehicleId, ts = ts, key = m.key, ecu = m.ecuKey,
            service = "UDS-0x22", pid = m.did?.let { "0x${it.toString(16).uppercase().padStart(4, '0')}" } ?: "0x0000",
            unit = m.unit, provenance = "reported",
            decodingMethod = if (m.decode != null) "builtin" else "raw-only",
            docStatus = null, rawResponse = toHex(record), value = (Decoder.decode(m.decode, record) as? Number)?.toDouble(),
            textValue = null, success = true, error = null,
        )
    }

    private suspend fun _recordFailure(m: MeasurementSpec, error: String, ts: String) {
        repository?.recordMeasurement(
            vehicleId = vehicleId, ts = ts, key = m.key, ecu = m.ecuKey,
            service = "UDS-0x22", pid = m.did?.let { "0x${it.toString(16).uppercase().padStart(4, '0')}" } ?: "0x0000",
            unit = m.unit, provenance = "reported",
            decodingMethod = if (m.decode != null) "builtin" else "raw-only",
            docStatus = null, rawResponse = "", value = null, textValue = null,
            success = false, error = error,
        )
    }

    private suspend fun sweepCells(cv: CellVoltages): List<Reading> {
        val ecu = spec.ecu(cv.ecuKey)
        val out = mutableListOf<Reading>()
        for (i in 0 until cv.slots) {
            val did = cv.didBase + i
            if (retired.contains(cv.ecuKey to did)) continue
            val key = "cell_v_${i.toString().padStart(3, '0')}"
            val reading = try {
                val record = Uds.readDid(protocol, guard, ecu, did)
                val value = Decoder.decode(cv.decode, record)
                val unused = record.size >= 2 && ((record[0].toInt() and 0xFF) shl 8) or (record[1].toInt() and 0xFF) == cv.unusedMarker
                if (unused) Reading(key, null, toHex(record), "unused")
                else Reading(key, value, toHex(record), "ok")
            } catch (e: NegativeResponseError) {
                if (e.nrc == 0x31) retire(cv.ecuKey, did, tsNow)
                Reading(key, null, "", "nrc")
            } catch (_: NoResponseError) {
                Reading(key, null, "", "no-response")
            }
            out.add(reading)
        }
        return out
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

    private fun toHex(bytes: ByteArray): String =
        bytes.joinToString("") { (it.toInt() and 0xFF).toString(16).padStart(2, '0').uppercase() }
}
