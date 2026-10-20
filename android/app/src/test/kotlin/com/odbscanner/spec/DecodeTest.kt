package com.odbscanner.spec

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Parity pins for the Android decoder against `decoders/meb.py` and
 * `tests/fixtures/meb_real_car_vectors.py`. The named special decoders added
 * for the float32 electric-drive family, the four-field energy counters, the
 * ASCII serial and the charge-port bits must agree with the Python side or the
 * phone would re-report the exact wrong values the desktop just fixed.
 */
class DecodeTest {

    private fun hex(s: String): ByteArray {
        val out = ByteArray(s.length / 2)
        for (i in out.indices) {
            out[i] = s.substring(i * 2, i * 2 + 2).toInt(16).toByte()
        }
        return out
    }

    private fun decode(name: String, raw: String): Any? =
        Decoder.decode(Decode.Special(name, null), hex(raw))

    @Test
    fun float32MatchesPython() {
        // 0x3E93 43D897DE -> 433.186 V; 0x4FAE 41816D8E -> 16.178 degC
        assertEquals(433.186, decode("float32", "43D897DE") as Double, 0.001)
        assertEquals(16.178, decode("float32", "41816D8E") as Double, 0.001)
        // 0x3EE7 44164000 -> 601 A; 0x3EE8 C3260000 -> -166 A
        assertEquals(601.0, decode("float32", "44164000") as Double, 0.001)
        assertEquals(-166.0, decode("float32", "C3260000") as Double, 0.001)
    }

    @Test
    fun float32RejectsShortAndNonFinite() {
        assertNull(decode("float32", "43D897"))            // short record
        assertNull(decode("float32", "7F800000"))          // +Inf
        assertNull(decode("float32", "FF800000"))          // -Inf
    }

    @Test
    fun energyCountersMatchesPython() {
        // 0x1E32 sample -> charge/discharge Ah and kWh (four fields)
        val out = decode("energy_counters", "01E7CC17FE1B74E70C8071E0F3FF868C") as Map<*, *>
        assertEquals(58196.47, out["charged_ah"] as Double, 0.01)
        assertEquals(57808.26, out["discharged_ah"] as Double, 0.01)
        assertEquals(24436.98, out["charged_kwh"] as Double, 0.01)
        assertEquals(23459.86, out["discharged_kwh"] as Double, 0.01)
        assertEquals(out["discharged_kwh"], out["used_kwh"])
    }

    @Test
    fun energyCountersRejectsShort() {
        assertNull(decode("energy_counters", "01E7CC17FE1B74E7"))
    }

    @Test
    fun asciiTrims() {
        val raw = "32383820565741524D464632363520"  // "288 VWARMFF265 "
        assertEquals("288 VWARMFF265", decode("ascii", raw))
    }

    @Test
    fun chargePortBits() {
        assertEquals(1, decode("bit2", "04"))
        assertEquals(0, decode("bit2", "00"))
        assertEquals(1, decode("bit3", "08"))
        assertEquals(0, decode("bit3", "00"))
    }
}
