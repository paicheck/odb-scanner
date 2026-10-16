package com.odbscanner.collect

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
 */
class Collector(
    private val spec: Spec,
    private val profile: ProfileSpec,
    private val protocol: Elm327Protocol,
    private val guard: ReadOnlyGuardImpl,
) {
    private val retired = mutableSetOf<Pair<String, Int>>()
    private val everSucceeded = mutableSetOf<Pair<String, Int>>()
    private val silentCount = mutableMapOf<Pair<String, Int>, Int>()
    private var silentRetireThreshold = 3

    fun setSilentRetireThreshold(n: Int) { silentRetireThreshold = n }

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

    /** Read and decode every measurement in one pass. */
    fun collectOnce(): List<Reading> {
        val readings = mutableListOf<Reading>()
        for (m in profile.measurements) {
            val did = m.did ?: continue
            if (retired.contains(m.ecuKey to did)) continue
            val ecu = spec.ecu(m.ecuKey)
            val reading = poll(m, ecu, did)
            readings.add(reading)
        }
        // per-cell sweep
        profile.cellVoltages?.let { cv ->
            readings.addAll(sweepCells(cv))
        }
        return readings
    }

    private fun poll(m: MeasurementSpec, ecu: EcuSpec, did: Int): Reading {
        return try {
            val record = Uds.readDid(protocol, guard, ecu, did)
            everSucceeded.add(m.ecuKey to did)
            silentCount.remove(m.ecuKey to did)
            val value = Decoder.decode(m.decode, record)
            Reading(m.key, value, toHex(record), "ok")
        } catch (e: NegativeResponseError) {
            if (e.nrc == 0x31) retire(m.ecuKey, did)
            Reading(m.key, null, "", "nrc-0x" + e.nrc.toString(16).padStart(2, '0'))
        } catch (_: NoResponseError) {
            noteSilent(m.ecuKey, did)
            Reading(m.key, null, "", "no-response")
        }
    }

    private fun sweepCells(cv: com.odbscanner.spec.CellVoltages): List<Reading> {
        val ecu = spec.ecu(cv.ecuKey)
        val out = mutableListOf<Reading>()
        for (i in 0 until cv.slots) {
            val did = cv.didBase + i
            if (retired.contains(cv.ecuKey to did)) continue
            val reading = try {
                val record = Uds.readDid(protocol, guard, ecu, did)
                val value = Decoder.decode(cv.decode, record)
                if (value == null && isUnused(record, cv.unusedMarker)) {
                    Reading("cell_v_${i.toString().padStart(3, '0')}", null, toHex(record), "unused")
                } else {
                    Reading("cell_v_${i.toString().padStart(3, '0')}", value, toHex(record), "ok")
                }
            } catch (e: NegativeResponseError) {
                if (e.nrc == 0x31) retire(cv.ecuKey, did)
                Reading("cell_v_${i}", null, "", "nrc")
            } catch (_: NoResponseError) {
                Reading("cell_v_${i}", null, "", "no-response")
            }
            out.add(reading)
        }
        return out
    }

    private fun isUnused(record: ByteArray, marker: Int): Boolean =
        record.size >= 2 &&
            ((record[0].toInt() and 0xFF) shl 8) or (record[1].toInt() and 0xFF) == marker

    private fun retire(ecuKey: String, did: Int) {
        retired.add(ecuKey to did)
    }

    private fun noteSilent(ecuKey: String, did: Int) {
        if (everSucceeded.contains(ecuKey to did)) return  // answered before -> car asleep
        val count = (silentCount[ecuKey to did] ?: 0) + 1
        silentCount[ecuKey to did] = count
        if (count >= silentRetireThreshold) {
            retire(ecuKey, did)
            silentCount.remove(ecuKey to did)
        }
    }

    private fun toHex(bytes: ByteArray): String =
        bytes.joinToString("") { (it.toInt() and 0xFF).toString(16).padStart(2, '0').uppercase() }
}
