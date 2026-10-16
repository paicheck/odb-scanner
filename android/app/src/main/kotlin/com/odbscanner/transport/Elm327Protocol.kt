package com.odbscanner.transport

import com.odbscanner.spec.EcuSpec

/**
 * A raw ELM327 link: send one line, receive the response lines back (the
 * trailing ">" prompt is already stripped by the implementation).
 */
interface Elm327Port {
    fun transact(line: String): List<String>
}

/**
 * The ELM327 protocol and the per-ECU addressing quirks this clone needs,
 * ported from `diagnostic/elm327.py`.
 *
 * The quirks are empirical and must not be "simplified":
 *
 *   * 11-bit modules (energy/climate/gps) are read with auto-formatting ON and
 *     a bare request plus the ISO-TP flow-control preamble
 *     (ATFCSH/ATFCSD/ATFCSM 1). The 8-byte hand-framed ATCAF0 line is refused
 *     with '?' under ATSP6 (Track B).
 *   * 29-bit modules are hand-framed (ATCAF0) with the receive filter pinned
 *     (ATCRA) and flow control reset (ATFCSM 0), otherwise the next 29-bit read
 *     after an 11-bit one is refused.
 *   * functional reads use auto-formatting and the functional header DB33F1.
 *
 * [port] is injected so this class is unit-testable without Bluetooth.
 */
class Elm327Protocol(private val port: Elm327Port) {

    private var currentEcu: EcuSpec? = null
    private var protocol: String = "6"

    /** Initialize the adapter into a known state. */
    fun init(identity: String = "unknown") {
        for (cmd in listOf("ATE0", "ATL0", "ATS0", "ATH1", "ATAT1", "ATSP6", "ATCAF1")) {
            port.transact(cmd)
        }
        protocol = "6"
        currentEcu = null
    }

    /** Point the adapter at one ECU (or null for functional addressing). */
    fun setModule(ecu: EcuSpec?) {
        if (ecu == currentEcu) return
        if (ecu == null) {
            restoreFunctional()
            currentEcu = null
            return
        }
        when (ecu.addressing) {
            "11-bit" -> set11Bit(ecu)
            "29-bit" -> set29Bit(ecu)
            else -> { restoreFunctional(); currentEcu = ecu }
        }
        currentEcu = ecu
    }

    private fun set11Bit(ecu: EcuSpec) {
        require(ecu.tx29 != null && ecu.rx29 != null)
        port.transact("ATSP6"); protocol = "6"
        for (cmd in listOf(
            "ATSH ${threeHex(ecu.tx29)}",
            "ATFCSH ${threeHex(ecu.tx29)}",
            "ATFCSD 300000",
            "ATFCSM 1",
            "ATCRA ${threeHex(ecu.rx29)}",
            "ATCAF1",
        )) port.transact(cmd)
    }

    private fun set29Bit(ecu: EcuSpec) {
        require(ecu.tx29 != null)
        port.transact("ATSP7"); protocol = "7"
        for (cmd in listOf(
            "ATCP ${byteHex((ecu.tx29 shr 24) and 0xFF)}",
            "ATSH ${sixHex(ecu.tx29 and 0xFFFFFF)}",
        )) port.transact(cmd)
        port.transact("ATFCSM 0")           // reset the 11-bit flow control
        port.transact("ATCAF0")
        ecu.rx29?.let { port.transact("ATCRA ${eightHex(it)}") }
    }

    private fun restoreFunctional() {
        port.transact("ATSP6"); protocol = "6"
        for (cmd in listOf("ATCP 18", "ATSH DB33F1", "ATFCSM 0", "ATCAF1")) {
            port.transact(cmd)
        }
    }

    /**
     * Send one read request. [body] is the bare UDS request hex (e.g. "222AB2").
     * Returns the raw response lines (headers on, ISO-TP PCI included).
     */
    fun sendRead(body: String): List<String> {
        require(body.length % 2 == 0 && body.length / 2 <= 7)
        val ecu = currentEcu
        val wire = if (ecu != null && ecu.handFrame) handFrame(body) else body
        return port.transact(wire)
    }

    private fun handFrame(body: String): String {
        val len = body.length / 2
        val padded = String.format("%02X%s", len, body) + "55".repeat(8)
        return padded.take(16)
    }

    private fun threeHex(v: Int) = v.toString(16).uppercase().padStart(3, '0')
    private fun sixHex(v: Int) = v.toString(16).uppercase().padStart(6, '0')
    private fun eightHex(v: Int) = v.toString(16).uppercase().padStart(8, '0')
    private fun byteHex(v: Int) = v.toString(16).uppercase().padStart(2, '0')
}
