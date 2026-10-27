package com.odbscanner.transport

import com.odbscanner.spec.EcuSpec
import com.odbscanner.uds.ReadOnlyGuardImpl

/**
 * A raw ELM327 link: send one line, receive the response lines back (the
 * trailing ">" prompt is already stripped by the implementation).
 */
interface Elm327Port {
    fun transact(line: String): List<String>
}

/**
 * The adapter answered "?" -- it did not accept the command. Separate from "no
 * response": the command never took effect, so the caller must not mistake it
 * for a silent ECU and start retiring DIDs.
 */
class Elm327RefusedError(val command: String) : Exception(
    "The ELM327 refused '$command' with '?'. The adapter does not support this " +
        "command, or the MEB addressing mode it belongs to."
)

/**
 * The ELM327 protocol and the per-ECU addressing quirks this clone needs,
 * ported from `diagnostic/elm327.py`.
 *
 * The quirks are empirical and must not be "simplified":
 *
 *   * every physical module (11-bit energy/climate/gps and 29-bit BMS/DC-DC
 *     alike) is read with auto-formatting ON and a bare request, plus the
 *     ISO-TP flow-control preamble (ATFCSH/ATFCSD/ATFCSM 1) and the receive
 *     filter pinned (ATCRA). This is Car Scanner's wire form, confirmed by a
 *     passive HCI capture on 2026-10-15. The 8-byte hand-framed ATCAF0 line
 *     is refused with '?' by this clone, under ATSP6 and ATSP7 alike.
 *   * functional reads use auto-formatting and the functional header DB33F1.
 *   * returning to functional addressing MUST clear the per-module receive
 *     filter. A filter left pinned to a module's response id makes every
 *     functional read answer nothing, and the failure looks like a sleeping
 *     car rather than a stale filter.
 *   * every command that decides how a request is framed is answered with
 *     "?" by an incompatible clone. Those refusals are surfaced, not ignored:
 *     a refused header would otherwise send a correctly-built request to the
 *     wrong place and report the silence as "no data".
 *
 * [port] is injected so this class is unit-testable without Bluetooth.
 *
 * THE TRANSMIT CHOKE POINT. [port] is private and this class is the only code
 * that calls [Elm327Port.transact]: AT commands are built here from constants
 * and numeric ids, and the one way to send a vehicle request is [sendRead],
 * which runs [guard] over the exact bytes it then transmits. Callers used to
 * validate one byte array and pass a separately typed hex string, so the two
 * could disagree. Anything that needs to reach the car must go through here.
 */
class Elm327Protocol(
    private val port: Elm327Port,
    private val guard: ReadOnlyGuardImpl,
    /** Pause after ATZ, mirroring the Python transport's 0.3 s settle. */
    private val resetDelayMs: Long = 300,
) {

    private var currentEcu: EcuSpec? = null

    /**
     * False after a refused or interrupted preamble: the adapter's addressing
     * is no longer what [currentEcu] claims, so the next setModule() must not
     * short-circuit on the cache. This is the state that made "ATSH suppressed"
     * bugs invisible -- the cache said 0x17FC007B while the adapter had reset.
     */
    private var stateKnown = true

    /** Reset the adapter and bring it into a known state. */
    fun init() {
        reset()
        baseInit()
    }

    /** Point the adapter at one ECU (or null for functional addressing). */
    fun setModule(ecu: EcuSpec?) {
        if (stateKnown && ecu == currentEcu) return
        try {
            applyAddressing(ecu)
        } catch (first: Elm327RefusedError) {
            // One clean retry: a stale header/filter survives the Bluetooth
            // connection on this clone, and ATZ is the only guaranteed reset.
            stateKnown = false
            currentEcu = null
            recover()
            try {
                applyAddressing(ecu)
            } catch (second: Elm327RefusedError) {
                stateKnown = false
                currentEcu = null
                throw second
            }
        }
        currentEcu = ecu
        stateKnown = true
    }

    private fun applyAddressing(ecu: EcuSpec?) {
        when {
            ecu == null -> restoreFunctional()
            ecu.addressing == "11-bit" -> set11Bit(ecu)
            ecu.addressing == "29-bit" -> set29Bit(ecu)
            else -> restoreFunctional()
        }
    }

    private fun set11Bit(ecu: EcuSpec) {
        require(ecu.tx29 != null && ecu.rx29 != null)
        at("ATSP6")
        // Car Scanner's captured 11-bit form: 3-digit headers, user-defined
        // flow control, a bare request (no ATCAF0).
        for (cmd in listOf(
            "ATSH ${threeHex(ecu.tx29)}",
            "ATFCSH ${threeHex(ecu.tx29)}",
            "ATFCSD 300000",
            "ATFCSM 1",
            "ATCRA ${threeHex(ecu.rx29)}",
            "ATCAF1",
        )) at(cmd)
    }

    private fun set29Bit(ecu: EcuSpec) {
        require(ecu.tx29 != null)
        at("ATSP7")
        at("ATCP ${byteHex((ecu.tx29 shr 24) and 0xFF)}")
        at("ATSH ${sixHex(ecu.tx29 and 0xFFFFFF)}")
        ecu.rx29?.let { at("ATCRA ${eightHex(it)}") }
        // Car Scanner's 29-bit preamble (2026-10-15 capture): the flow-control
        // header is the full 8-digit request id, and auto-formatting stays ON,
        // so the request goes out bare. No ATCAF0 and no ATFCSM 0.
        for (cmd in listOf(
            "ATFCSH ${eightHex(ecu.tx29)}",
            "ATFCSD 300000",
            "ATFCSM 1",
            "ATCAF1",
        )) at(cmd)
    }

    private fun restoreFunctional() {
        // Protocol 7 (29-bit), as diagnostic/elm327.py PINNED_PROTOCOL: the
        // functional id 18DB33F1 (ATCP 18 + ATSH DB33F1) only exists on the
        // 29-bit bus. Under ATSP6 the header was applied as 11 bits, so the
        // functional 03 / 19 02 8F DTC reads never reached 18DB33F1 and an
        // empty answer looked exactly like "no DTCs".
        at("ATSP7")
        at("ATCP 18")
        at("ATSH DB33F1")
        // Plain ATCRA restores the receive filters to their default. It is the
        // one command here whose refusal is not fatal by itself: some clones
        // reject the bare form, in which case a reset is the only guaranteed
        // way to drop the module filter -- and leaving it pinned would deafen
        // every functional read that follows.
        if (refused(port.transact("ATCRA"))) {
            recover()
            at("ATCP 18")
            at("ATSH DB33F1")
        }
        at("ATFCSM 0")
        at("ATCAF1")
    }

    /**
     * Send one read request: the bare UDS request bytes (e.g. 22 2A B2).
     * Returns the raw response lines (headers on, ISO-TP PCI included).
     *
     * [request] is validated by the read-only guard and then those same bytes
     * are hex-encoded and sent -- nothing else can reach the vehicle. The
     * request always goes out bare: the adapter is left in ATCAF1 and adds the
     * ISO-TP PCI byte, header and flow control itself.
     */
    fun sendRead(request: ByteArray): List<String> {
        guard.validate(request)
        val body = request.joinToString("") { "%02X".format(it.toInt() and 0xFF) }
        val lines = port.transact(body)
        if (refused(lines)) throw Elm327RefusedError(body)
        return lines
    }

    /** AT command that must be accepted: a refusal is an addressing failure. */
    private fun at(cmd: String) {
        if (refused(port.transact(cmd))) throw Elm327RefusedError(cmd)
    }

    private fun refused(lines: List<String>): Boolean =
        lines.any { it.trim() == "?" }

    private fun reset() {
        port.transact("ATZ")
        if (resetDelayMs > 0) Thread.sleep(resetDelayMs)
    }

    /**
     * The documented base init. Only the two commands that make a bare request
     * meaningful (protocol 6 and auto-formatting) are strict; the cosmetic ones
     * (echo, linefeeds, spaces, headers, adaptive timing) are tolerated as
     * refusals, exactly as the Python transport tolerates them.
     */
    private fun baseInit() {
        for (cmd in BASE_INIT) {
            val wasRefused = refused(port.transact(cmd))
            if (wasRefused && cmd in CRITICAL_INIT) throw Elm327RefusedError(cmd)
        }
        currentEcu = null
        stateKnown = true
    }

    private fun recover() {
        reset()
        baseInit()
    }

    private fun threeHex(v: Int) = v.toString(16).uppercase().padStart(3, '0')
    private fun sixHex(v: Int) = v.toString(16).uppercase().padStart(6, '0')
    private fun eightHex(v: Int) = v.toString(16).uppercase().padStart(8, '0')
    private fun byteHex(v: Int) = v.toString(16).uppercase().padStart(2, '0')

    private companion object {
        // ATSP7 as on the Python side (PINNED_PROTOCOL = "7"): the MEB
        // diagnostic bus answers on 29-bit ids. 11-bit modules switch to
        // ATSP6 themselves in setModule().
        val BASE_INIT = listOf("ATE0", "ATL0", "ATS0", "ATH1", "ATAT1", "ATSP7", "ATCAF1")
        val CRITICAL_INIT = setOf("ATSP7", "ATCAF1")
    }
}
