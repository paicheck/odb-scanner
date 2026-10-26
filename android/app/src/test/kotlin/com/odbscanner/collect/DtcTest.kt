package com.odbscanner.collect

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * DTC decode pins against `diagnostic/obd2.py` and `analysis/dtc.py`. A wrong
 * bit shift here invents faults that were never set and drops the one that was,
 * so each of the three layouts (2-byte OBD, 3-byte UDS, status byte) is pinned
 * to a known code.
 */
class DtcTest {

    private fun hex(s: String): ByteArray {
        val clean = s.replace(" ", "")
        val out = ByteArray(clean.length / 2)
        for (i in out.indices) {
            out[i] = clean.substring(i * 2, i * 2 + 2).toInt(16).toByte()
        }
        return out
    }

    @Test
    fun `2-byte OBD code decodes`() {
        assertEquals("P0143", decodeObd2Dtc(0x01, 0x43))
        assertEquals("P0101", decodeObd2Dtc(0x01, 0x01))
        assertEquals("U0100", decodeObd2Dtc(0xC1, 0x00))
    }

    @Test
    fun `3-byte UDS code decodes`() {
        assertEquals("U112300", decodeVagDtc(0xD1, 0x23, 0x00))
        assertEquals("P014300", decodeVagDtc(0x01, 0x43, 0x00))
    }

    @Test
    fun `status byte label picks the most alerting bit`() {
        assertEquals("unknown", dtcStatusLabel(null))
        assertEquals("warning", dtcStatusLabel(0x80))
        assertEquals("active", dtcStatusLabel(0x01))
        assertEquals("pending", dtcStatusLabel(0x04))
        assertEquals("confirmed", dtcStatusLabel(0x08))
        assertEquals("stored", dtcStatusLabel(0x20))
        // warning outranks an active test failure
        assertEquals("warning", dtcStatusLabel(0x81))
    }

    @Test
    fun `UDS 0x19 0x02 response parses`() {
        val dtcs = parseUdsDtcResponse(hex("59 02 FF 01 43 00 08 D1 23 00 01"))
        assertEquals(2, dtcs.size)
        assertEquals("P014300" to 0x08, dtcs[0])
        assertEquals("U112300" to 0x01, dtcs[1])
    }

    @Test
    fun `UDS 0x19 0x02 skips zero padding and wrong service`() {
        assertEquals(emptyList<Pair<String, Int>>(), parseUdsDtcResponse(hex("59 02 FF 00 00 00 00")))
        assertEquals(emptyList<Pair<String, Int>>(), parseUdsDtcResponse(hex("7F 19 31")))
        assertEquals(emptyList<Pair<String, Int>>(), parseUdsDtcResponse(hex("59 04 FF")))
    }

    @Test
    fun `OBD mode 03 response parses`() {
        assertEquals(listOf("P0143"), parseObd03DtcResponse(hex("43 01 01 43")))
        assertEquals(listOf("P0143", "P0101"), parseObd03DtcResponse(hex("43 02 01 43 01 01")))
        assertEquals(emptyList<String>(), parseObd03DtcResponse(hex("43 00")))
        assertEquals(emptyList<String>(), parseObd03DtcResponse(hex("7F 03 11")))
    }
}
