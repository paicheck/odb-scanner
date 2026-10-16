package com.odbscanner.uds

import com.odbscanner.spec.EcuSpec
import com.odbscanner.transport.Elm327Protocol

class NegativeResponseError(val service: Int, val nrc: Int) :
    Exception("UDS negative response: service 0x" + service.toString(16) +
        ", NRC 0x" + nrc.toString(16).padStart(2, '0'))

class NoResponseError(val did: Int) :
    Exception("No decodable response to DID 0x" + did.toString(16).padStart(4, '0'))

/**
 * UDS read path over the ELM327 transport, ported from `diagnostic/uds.py`
 * (parsing) and `diagnostic/connection.py` (read_did).
 */
object Uds {

    /** Read one DID and return its data record (after the DID echo). */
    fun readDid(
        protocol: Elm327Protocol,
        guard: ReadOnlyGuardImpl,
        ecu: EcuSpec,
        did: Int,
    ): ByteArray {
        val request = guard.buildReadDidRequest(did)
        protocol.setModule(ecu)
        val lines = protocol.sendRead(toHex(request))
        val payload = parsePayload(lines) ?: throw NoResponseError(did)

        return when (payload[0].toInt() and 0xFF) {
            0x62 -> {
                if (payload.size >= 3 &&
                    (((payload[1].toInt() and 0xFF) shl 8) or (payload[2].toInt() and 0xFF)) == did
                ) {
                    payload.copyOfRange(3, payload.size)
                } else {
                    throw NoResponseError(did)  // DID echo mismatch
                }
            }
            0x7F -> {
                val nrc = if (payload.size > 2) payload[2].toInt() and 0xFF else 0
                throw NegativeResponseError(0x22, nrc)
            }
            else -> throw NoResponseError(did)
        }
    }

    /**
     * Reassemble ELM327 response lines into one ISO-TP payload, or null when
     * the lines carry no decodable frame. Handles header-on (11-bit and 29-bit)
     * and header-off formats, single and multi-frame.
     */
    fun parsePayload(lines: List<String>): ByteArray? {
        val frames = mutableListOf<ByteArray>()
        for (line in lines) {
            val frame = lineToFrame(line) ?: continue
            frames.add(frame)
        }
        return reassemble(frames)
    }

    /** One ELM327 line -> an ISO-TP frame (PCI byte included), or null. */
    private fun lineToFrame(line: String): ByteArray? {
        val h = line.filter { it.isDigit() || it in "abcdefABCDEF" }.uppercase()
        if (h.length < 4) return null
        val body = stripHeader(h)
        val raw = try {
            body.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
        } catch (_: NumberFormatException) {
            return null
        }
        return if (validFrame(raw)) raw else null
    }

    /** Split a cleaned hex line into frame hex, dropping the CAN-id prefix. */
    private fun stripHeader(h: String): String {
        // 11-bit id: 3 hex digits + an even frame length -> odd total length.
        if (h.length % 2 == 1 && h.length > 3) return h.substring(3)
        // 29-bit id: 8 hex digits ('18'/'17' priority) + an even frame length.
        if (h.length % 2 == 0 && h.length > 8 &&
            (h.startsWith("18") || h.startsWith("17"))
        ) return h.substring(8)
        return h  // header-off
    }

    private fun validFrame(raw: ByteArray): Boolean {
        if (raw.isEmpty()) return false
        val pci = (raw[0].toInt() and 0xF0) shr 4
        return when (pci) {
            0 -> (raw[0].toInt() and 0x0F) in 1..7 && raw.size >= 1 + (raw[0].toInt() and 0x0F)
            1, 2, 3 -> raw.size >= if (pci == 1) 2 else 1
            else -> false
        }
    }

    /** Reassemble ISO-TP frames into one payload, or null on a truncated flow. */
    private fun reassemble(frames: List<ByteArray>): ByteArray? {
        if (frames.isEmpty()) return null
        val first = frames[0]
        val pci = (first[0].toInt() and 0xF0) shr 4
        if (pci == 0) {
            val n = first[0].toInt() and 0x0F
            return first.copyOfRange(1, 1 + n)
        }
        if (pci != 1) return null
        val total = ((first[0].toInt() and 0x0F) shl 8) or (first[1].toInt() and 0xFF)
        val data = first.copyOfRange(2, first.size).toMutableList()
        var seq = 1
        for (f in frames.drop(1)) {
            if ((f[0].toInt() and 0xF0) shr 4 != 2 || (f[0].toInt() and 0x0F) != seq % 16) return null
            data.addAll(f.copyOfRange(1, f.size).toList())
            seq++
            if (data.size >= total) break
        }
        if (data.size < total) return null
        return data.take(total).toByteArray()
    }

    private fun toHex(bytes: ByteArray): String =
        bytes.joinToString("") { (it.toInt() and 0xFF).toString(16).padStart(2, '0').uppercase() }
}
