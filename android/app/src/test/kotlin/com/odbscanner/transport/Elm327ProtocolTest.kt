package com.odbscanner.transport

import com.odbscanner.spec.EcuSpec
import com.odbscanner.spec.ReadOnlyGuard
import com.odbscanner.uds.ReadOnlyGuardImpl
import com.odbscanner.uds.ReadOnlyViolation
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * An ELM327Port that records every line it is asked to send.
 *
 * [answers] is consulted first so a test can script a response; a missing
 * command falls back to "OK". [refuseOnce] answers "?" for the first
 * occurrence of each listed command, which is how a clone behaves when a stale
 * header survives a Bluetooth reconnect.
 */
private class RecordingPort(
    private val answers: Map<String, List<String>> = emptyMap(),
    private val refuseOnce: Set<String> = emptySet(),
    private val alwaysRefuse: Set<String> = emptySet(),
) : Elm327Port {
    val sent = mutableListOf<String>()
    private val alreadyRefused = mutableSetOf<String>()

    override fun transact(line: String): List<String> {
        sent.add(line)
        if (line in alwaysRefuse) return listOf("?")
        if (line in refuseOnce && alreadyRefused.add(line)) return listOf("?")
        return answers[line] ?: listOf("OK")
    }
}

/**
 * Mirrors `tests/test_transport_wire_contract.py` and the captured wire form in
 * `tests/data/car_scanner_wire.json` (a 2026-10-15 passive HCI capture of Car
 * Scanner).
 *
 * The 29-bit modules once went silent because the transport hand-framed them
 * (ATCAF0) and the clone refuses that with '?'. The Android transport must obey
 * the same captured form as the Python one, or the phone would reconnect the
 * bug the desktop just fixed.
 */
class Elm327ProtocolTest {

    private fun spec29Bit() = EcuSpec(
        key = "bat_mgmt",
        name = "HV battery management (BMS)",
        tx = 0x7E5,
        rx = 0x7ED,
        tx29 = 0x17FC007B,
        rx29 = 0x17FE007B,
        addressing = "29-bit",
        autoFormatting = true,
        flowControl = true,
        receiveFilter = true,
    )

    private fun spec11Bit() = EcuSpec(
        key = "energy",
        name = "Gateway energy information",
        tx = 0x710,
        rx = 0x77A,
        tx29 = 0x00000710,
        rx29 = 0x0000077A,
        addressing = "11-bit",
        autoFormatting = true,
        flowControl = true,
        receiveFilter = true,
    )

    private fun guard() = ReadOnlyGuardImpl(ReadOnlyGuard(
        allowedServices = mapOf(0x22 to "read data by identifier", 0x03 to "mode 03"),
        blockedServices = mapOf(0x2E to "write data by identifier", 0x14 to "clear DTCs"),
        readSubfunctions = emptyMap(),
        minRequestBytes = mapOf(0x22 to 3),
        maxRequestBytes = 7,
        nrcNames = emptyMap(),
    ))

    /** No settle after ATZ: unit tests must not sleep. */
    private fun protocol(port: Elm327Port) = Elm327Protocol(port, guard(), resetDelayMs = 0)

    private val read1E3B = byteArrayOf(0x22, 0x1E, 0x3B)

    @Test
    fun `sendRead validates the bytes it sends and transmits nothing it refuses`() {
        // The protocol is the choke point: callers used to validate one byte
        // array and hand sendRead a separately typed string.
        val port = RecordingPort()
        val p = protocol(port)
        p.init()
        val before = port.sent.size
        for (write in listOf(
            byteArrayOf(0x2E, 0xF1.toByte(), 0x90.toByte(), 0x00),
            byteArrayOf(0x14, 0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte()),
            byteArrayOf(0x22, 0x1E),                       // too short
            ByteArray(8) { 0x22 },                          // too long
        )) {
            assertThrows(ReadOnlyViolation::class.java) { p.sendRead(write) }
        }
        assertEquals("nothing may be transmitted", before, port.sent.size)
        p.sendRead(read1E3B)
        assertEquals("221E3B", port.sent.last())
    }

    @Test
    fun `29-bit preamble matches the captured wire form`() {
        val port = RecordingPort()
        protocol(port).apply {
            init()
            setModule(spec29Bit())
        }
        val expected = listOf(
            "ATSP7", "ATCP 17", "ATSH FC007B", "ATCRA 17FE007B",
            "ATFCSH 17FC007B", "ATFCSD 300000", "ATFCSM 1", "ATCAF1",
        )
        for (cmd in expected) {
            assertTrue("captured command '$cmd' not sent: ${port.sent}", cmd in port.sent)
        }
        for (cmd in listOf("ATCAF0", "ATFCSM 0")) {
            assertFalse("forbidden command '$cmd' sent: ${port.sent}", cmd in port.sent)
        }
    }

    @Test
    fun `11-bit preamble matches the captured wire form`() {
        val port = RecordingPort()
        protocol(port).apply {
            init()
            setModule(spec11Bit())
        }
        val expected = listOf(
            "ATSP6", "ATSH 710", "ATFCSH 710", "ATFCSD 300000",
            "ATFCSM 1", "ATCRA 77A", "ATCAF1",
        )
        for (cmd in expected) {
            assertTrue("captured command '$cmd' not sent: ${port.sent}", cmd in port.sent)
        }
        for (cmd in listOf("ATCAF0", "ATFCSM 0")) {
            assertFalse("forbidden command '$cmd' sent: ${port.sent}", cmd in port.sent)
        }
    }

    @Test
    fun `read request goes out bare, never hand-framed`() {
        val port = RecordingPort()
        protocol(port).apply {
            init()
            setModule(spec29Bit())
            sendRead(read1E3B)
        }
        assertEquals("221E3B", port.sent.last())
        assertFalse(
            "a hand-framed 8-byte line was sent: ${port.sent}",
            port.sent.any { it.length == 16 && it.startsWith("03") },
        )
    }

    @Test
    fun `returning to functional addressing clears the module filter`() {
        // A pinned ATCRA is invisible while reading one module physically: the
        // filter matches its answers. It only breaks the NEXT functional read,
        // which then looks like a sleeping car instead of a stale filter. The
        // bare ATCRA is the documented way to drop it.
        val port = RecordingPort()
        val p = protocol(port)
        p.init()
        p.setModule(spec29Bit())
        port.sent.clear()

        p.setModule(null)

        assertTrue("bare ATCRA not sent: ${port.sent}", "ATCRA" in port.sent)
        assertTrue("functional header not restored: ${port.sent}", "ATSH DB33F1" in port.sent)
        assertFalse(
            "a module-specific ATCRA survived: ${port.sent}",
            port.sent.any { it.startsWith("ATCRA ") },
        )
    }

    @Test
    fun `a refused module preamble resets the adapter and retries once`() {
        val port = RecordingPort(refuseOnce = setOf("ATSH FC007B"))
        val p = protocol(port)

        p.init()
        p.setModule(spec29Bit())

        assertEquals(
            "one ATZ at init and one recovery ATZ expected: ${port.sent}",
            2, port.sent.count { it == "ATZ" },
        )
        assertTrue("the preamble was not retried: ${port.sent}",
            port.sent.count { it == "ATSH FC007B" } == 2)
    }

    @Test
    fun `a preamble refused twice surfaces the refusal`() {
        val port = RecordingPort(alwaysRefuse = setOf("ATSH FC007B"))
        val p = protocol(port)
        p.init()

        assertThrows(Elm327RefusedError::class.java) { p.setModule(spec29Bit()) }
        assertEquals("init ATZ + one recovery ATZ", 2,
            port.sent.count { it == "ATZ" })
    }

    @Test
    fun `a refused read is an error, not silence`() {
        val port = RecordingPort(answers = mapOf("221E3B" to listOf("?")))
        val p = protocol(port)
        p.init()

        // "?" means the adapter rejected the request, so the DID was never
        // tried. Treating it as NO DATA would retire a working DID.
        assertThrows(Elm327RefusedError::class.java) { p.sendRead(read1E3B) }
    }

    @Test
    fun `a refused critical init command is fatal`() {
        val port = RecordingPort(alwaysRefuse = setOf("ATCAF1"))
        assertThrows(Elm327RefusedError::class.java) { protocol(port).init() }
    }
}
