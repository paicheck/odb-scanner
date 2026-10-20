package com.odbscanner.spec

/**
 * Decodes a DID data record using a [Decode] recipe from the spec.
 *
 * The simple-arithmetic path (offset/length/signed/scale/divisor/addend) covers
 * most of the MEB profile. The named special decoders mirror `decoders/meb.py`;
 * their constants (the cell-unused marker, the cell-temperature guard) are the
 * same values the Python side uses.
 */
object Decoder {
    private const val CELL_UNUSED = 0x0FFE   // decoders/meb.py CELL_UNUSED
    private const val CELL_T_MAX = 1020       // decoders/meb.py CELL_T_MAX
    private const val ENERGY_AH_PER_COUNT = 0.001820444117116
    private const val ENERGY_KWH_PER_COUNT = 116508.398 / 1_000_000_000.0

    /** Decode a DID data record; null when there is nothing to decode. */
    fun decode(recipe: Decode?, record: ByteArray): Any? {
        if (recipe == null || record.isEmpty()) return null
        return try {
            when (recipe) {
                is Decode.Scalar -> decodeScalar(recipe, record)
                is Decode.Special -> decodeSpecial(recipe.name, record)
            }
        } catch (_: IndexOutOfBoundsException) {
            null  // short record: preserve raw, decode as unknown
        }
    }

    private fun decodeScalar(r: Decode.Scalar, record: ByteArray): Double? {
        val end = r.offset + r.length
        if (r.offset < 0 || end > record.size) return null
        var v = 0L
        for (i in r.offset until end) v = (v shl 8) or (record[i].toLong() and 0xFF)
        if (r.signed) {
            val bits = r.length * 8
            val signBit = 1L shl (bits - 1)
            if (v and signBit != 0L) v -= 1L shl bits
        }
        return v * r.scale / r.divisor + r.addend
    }

    private fun decodeSpecial(name: String, record: ByteArray): Any? {
        return when (name) {
            "cell_voltage" -> {
                val v = u16(record, 0) ?: return null
                if (v == CELL_UNUSED) null else v / 1000.0 + 1.0
            }
            "cell_temp" -> {
                val v = u16(record, 0) ?: return null
                if (v > CELL_T_MAX) null else v / 8.0 - 40.0
            }
            "charge_mode" -> mapOf(4 to 1, 6 to 2)[record[0].toInt() and 0xFF] ?: 0
            "float32" -> {
                if (record.size < 4) return null
                val bits = u32(record, 0) ?: return null
                val value = Float.fromBits(bits.toInt()).toDouble()
                if (value.isFinite()) value else null
            }
            "ascii" ->
                record.toString(Charsets.US_ASCII).trim('\u0000', ' ')
            "bit2" -> ((record[0].toInt() and 0xFF) ushr 2) and 1
            "bit3" -> ((record[0].toInt() and 0xFF) ushr 3) and 1
            "energy_counters" -> {
                if (record.size < 16) return null
                val chargedAh = s32(record, 0) * ENERGY_AH_PER_COUNT
                val dischargedAh = kotlin.math.abs(s32(record, 4) * ENERGY_AH_PER_COUNT)
                val chargedKwh = s32(record, 8) * ENERGY_KWH_PER_COUNT
                val dischargedKwh = kotlin.math.abs(s32(record, 12) * ENERGY_KWH_PER_COUNT)
                mapOf(
                    "charged_ah" to chargedAh,
                    "discharged_ah" to dischargedAh,
                    "charged_kwh" to chargedKwh,
                    "discharged_kwh" to dischargedKwh,
                    "used_kwh" to dischargedKwh,
                )
            }
            "coolant_temps" -> {
                if (record.size < 4) return null
                mapOf(
                    "outlet_c" to (u16(record, 0)!! / 64.0),
                    "inlet_c" to (u16(record, 2)!! / 64.0),
                )
            }
            "pair" -> {
                // scaled value at byte 0, raw index at byte `dl` (see _cs_pair_decoder)
                null  // reserved: the two-field decode is not yet needed on the car
            }
            "gear" -> record.getOrNull(1)?.toInt()?.let { gearCode(it) }
            "vin" -> record.toString(Charsets.US_ASCII).trim('\u0000', ' ')
            else -> null
        }
    }

    private fun u16(b: ByteArray, off: Int): Int? =
        if (off + 1 < b.size) ((b[off].toInt() and 0xFF) shl 8) or (b[off + 1].toInt() and 0xFF)
        else null

    private fun u32(b: ByteArray, off: Int): Long? =
        if (off + 3 < b.size)
            ((b[off].toLong() and 0xFF) shl 24) or
            ((b[off + 1].toLong() and 0xFF) shl 16) or
            ((b[off + 2].toLong() and 0xFF) shl 8) or
            (b[off + 3].toLong() and 0xFF)
        else null

    /** Signed 32-bit big-endian at off. */
    private fun s32(b: ByteArray, off: Int): Long {
        val u = u32(b, off) ?: return 0
        return if (u and 0x8000_0000L != 0L) u - 0x1_0000_0000L else u
    }

    private fun gearCode(code: Int): String = when (code) {
        0x08 -> "P"
        0x04 -> "R"
        0x10 -> "N"
        0x20 -> "D"
        else -> "raw $code"
    }
}
