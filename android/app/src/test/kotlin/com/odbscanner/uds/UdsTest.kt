package com.odbscanner.uds

import com.odbscanner.spec.EcuSpec
import com.odbscanner.spec.ReadOnlyGuard
import com.odbscanner.transport.Elm327Port
import com.odbscanner.transport.Elm327Protocol
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test

/** An ELM327Port that answers scripted DID reads and "OK" to AT commands. */
private class ScriptedPort(private val answers: Map<String, List<String>>) : Elm327Port {
    override fun transact(line: String): List<String> = answers[line] ?: listOf("OK")
}

/**
 * The UDS answer parser, pinned against the wire forms an ELM327 actually
 * prints.
 *
 * `uds/uds.py` and the Python tests already cover the protocol; this class
 * covers the Kotlin copy. The cases are the ones that have produced wrong
 * numbers in the field: a header left on the line being parsed as data, a
 * multi-frame answer truncated by a dropped sequence number, a negative
 * response read as silence (and therefore retired), and a positive response
 * carrying a different DID than the one asked for.
 */
class UdsTest {

    private fun ecu() = EcuSpec(
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

    private fun guard() = ReadOnlyGuard(
        allowedServices = mapOf(
            0x10 to "diagnostic session control",
            0x22 to "read data by identifier",
        ),
        blockedServices = mapOf(
            0x2E to "write data by identifier",
            0x31 to "routine control",
        ),
        readSubfunctions = mapOf(0x10 to setOf(0x01)),
        minRequestBytes = mapOf(0x10 to 2, 0x22 to 3),
        maxRequestBytes = 7,
        nrcNames = emptyMap(),
    )

    private fun read(did: Int, lines: List<String>): ByteArray {
        val request = "22" + did.toString(16).uppercase().padStart(4, '0')
        val port = ScriptedPort(mapOf(request to lines))
        val protocol = Elm327Protocol(port, resetDelayMs = 0)
        protocol.init()
        return Uds.readDid(protocol, ReadOnlyGuardImpl(guard()), ecu(), did)
    }

    @Test
    fun `11-bit header is stripped`() {
        val record = read(0x1E2C, listOf("77A 05 62 1E 2C 0B B8"))
        assertArrayEquals(byteArrayOf(0x0B, 0xB8.toByte()), record)
    }

    @Test
    fun `29-bit header is stripped`() {
        val record = read(0x1E2C, listOf("18DAF17B 05 62 1E 2C 0B B8"))
        assertArrayEquals(byteArrayOf(0x0B, 0xB8.toByte()), record)
    }

    @Test
    fun `header-off line is parsed`() {
        val record = read(0x1E2C, listOf("05 62 1E 2C 0B B8"))
        assertArrayEquals(byteArrayOf(0x0B, 0xB8.toByte()), record)
    }

    @Test
    fun `negative response surfaces the NRC`() {
        val e = assertThrows(NegativeResponseError::class.java) {
            read(0x1E2C, listOf("03 7F 22 31"))
        }
        assertEquals(0x22, e.service)
        assertEquals(0x31, e.nrc)
    }

    @Test
    fun `a positive response for a different DID is not accepted`() {
        // The echo is the only check that the answer belongs to this request.
        // Without it a shifted frame stores one DID's bytes under another key.
        assertThrows(NoResponseError::class.java) {
            read(0x1E2C, listOf("04 62 1E 2D 0B B8"))
        }
    }

    @Test
    fun `multi-frame response is reassembled in order`() {
        val record = read(
            0x1E32,
            listOf(
                "10 13 62 1E 32 01 02 03",
                "21 04 05 06 07 08 09 0A",
                "22 0B 0C 0D 0E 0F 10",
            ),
        )
        assertArrayEquals(
            byteArrayOf(
                1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
            ),
            record,
        )
    }

    @Test
    fun `a broken multi-frame sequence is rejected`() {
        // Out-of-order consecutive frames are a truncated/reordered answer, not
        // a short one: accepting it would store half a record as complete.
        assertNull(
            Uds.parsePayload(
                listOf(
                    "10 13 62 1E 32 01 02 03",
                    "22 04 05 06 07 08 09 0A",   // should be 21
                ),
            ),
        )
    }

    @Test
    fun `a truncated multi-frame is rejected`() {
        assertNull(
            Uds.parsePayload(
                listOf(
                    "10 13 62 1E 32 01 02 03",
                    "21 04 05 06 07 08 09 0A",
                ),
            ),
        )
    }

    @Test
    fun `functional responses are grouped by source`() {
        // Two ECUs answer one functional request with distinct 29-bit headers;
        // their frames must not be reassembled into one corrupted payload.
        val bySource = Uds.parsePayloadsBySource(
            listOf(
                "18DAF17B 05 62 1E 2C 0B B8",
                "17FE007B 05 62 1E 2C 0A A0",
            ),
        )
        assertEquals(2, bySource.size)
        val a = bySource[0x18DAF17B]!!
        val b = bySource[0x17FE007B]!!
        assertArrayEquals(byteArrayOf(0x62, 0x1E, 0x2C, 0x0B, 0xB8.toByte()), a)
        assertArrayEquals(byteArrayOf(0x62, 0x1E, 0x2C, 0x0A, 0xA0.toByte()), b)
    }
}
