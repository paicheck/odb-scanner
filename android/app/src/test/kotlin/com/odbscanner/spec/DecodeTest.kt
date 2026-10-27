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

    @Test
    fun pairMatchesPython() {
        // _cs_pair_decoder: value = u16be[0:2] * 0.01, index = u16be[2:4].
        // 0x04D2 = 1234 -> 12.34 %; index 0x0007.
        val out = Decoder.decode(
            Decode.Special("pair", null, width = 2, scale = 0.01),
            hex("04D20007"),
        ) as Map<*, *>
        assertEquals(12.34, out["value"] as Double, 1e-9)
        assertEquals(7L, out["index"])
    }

    @Test
    fun pairWithoutWidthIsNotGuessed() {
        // The spec must carry the field layout; an old asset without it cannot
        // be decoded by assuming a two-byte field.
        assertNull(Decoder.decode(Decode.Special("pair", null), hex("04D20007")))
        assertNull(
            Decoder.decode(
                Decode.Special("pair", null, width = 2, scale = 0.01),
                hex("04D2"),
            ),
        )
    }

    @Test
    fun cellVoltageUnusedMarkerIsNull() {
        // 0x0FFE means "sensor absent", not 5.094 V.
        assertNull(decode("cell_voltage", "0FFE"))
        assertEquals(3.7, decode("cell_voltage", "0A8C") as Double, 1e-9)
    }

    @Test
    fun gearMatchesPython() {
        // decoders/meb.py::_gear reads data byte 1 and maps
        // 8=P, 7=R, 6=N, 5=D, 12=B. The old Kotlin map (0x04/0x10/0x20) was
        // incompatible: captured 000C (B) decoded as "raw 12".
        assertEquals("P", decode("gear", "0008"))
        assertEquals("R", decode("gear", "0007"))
        assertEquals("N", decode("gear", "0006"))
        assertEquals("D", decode("gear", "0005"))
        assertEquals("B", decode("gear", "000C"))
        // byte 0 is ignored, and byte 1 is unsigned even when its top bit is set
        assertEquals("P", decode("gear", "FF08"))
        assertEquals("raw 1234FF09", decode("gear", "1234FF09"))
        assertEquals("raw 09", decode("gear", "09"))
    }

    @Test
    fun scalarArithmeticMatchesTheRecipe() {
        val recipe = Decode.Scalar(
            offset = 0, length = 2, signed = false,
            scale = 1.0, divisor = 10.0, addend = -40.0, formula = null,
        )
        assertEquals(30.0, Decoder.decode(recipe, hex("02BC")) as Double, 1e-9)
    }

    @Test
    fun subZeroTemperaturesAreSigned() {
        // Parity with tests/test_signed_temperatures.py: decoded unsigned,
        // -1 C coolant read as 1023 C and -5 C pack max as 1019 C.
        @Suppress("UNCHECKED_CAST")
        val coolant = decode("coolant_temps", "FFC0FF80") as Map<String, Double>
        assertEquals(-1.0, coolant["outlet_c"]!!, 1e-9)
        assertEquals(-2.0, coolant["inlet_c"]!!, 1e-9)
        val batTempMax = Decode.Scalar(
            offset = 0, length = 2, signed = true,
            scale = 1.0 / 64, divisor = 1.0, addend = 0.0, formula = null,
        )
        assertEquals(-5.0, Decoder.decode(batTempMax, hex("FEC00003")) as Double, 1e-9)
    }
}
