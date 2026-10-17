package com.odbscanner.transport

import com.odbscanner.spec.EcuSpec
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** An ELM327Port that records every line it is asked to send. */
private class RecordingPort : Elm327Port {
    val sent = mutableListOf<String>()
    override fun transact(line: String): List<String> {
        sent.add(line)
        return listOf("OK")
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

    @Test
    fun `29-bit preamble matches the captured wire form`() {
        val port = RecordingPort()
        Elm327Protocol(port).apply {
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
        Elm327Protocol(port).apply {
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
        Elm327Protocol(port).apply {
            init()
            setModule(spec29Bit())
            sendRead("221E3B")
        }
        assertEquals("221E3B", port.sent.last())
        assertFalse(
            "a hand-framed 8-byte line was sent: ${port.sent}",
            port.sent.any { it.length == 16 && it.startsWith("03") },
        )
    }
}
