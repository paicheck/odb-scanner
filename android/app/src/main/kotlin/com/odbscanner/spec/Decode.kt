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
                is Decode.Special -> decodeSpecial(recipe, record)
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

    private fun decodeSpecial(r: Decode.Special, record: ByteArray): Any? {
        return when (r.name) {
            "cell_voltage" -> {
                val v = u16(record, 0) ?: return null
                if (v == CELL_UNUSED) null else v / 1000.0 + 1.0
            }
            "cell_temp" -> {
                val v = u16(record, 0) ?: return null
                if (v > CELL_T_MAX) null else v / 8.0 - 40.0
            }
            // Bit field, as decoders/meb.py _charge_mode: bit 2 = charging,
            // plus bit 1 = DC; undocumented bits are unknown (null), never
            // "idle" -- idle closes a live charging session.
            "charge_mode" -> {
                val b = record[0].toInt() and 0xFF
                when {
                    b and 0x07.inv() != 0 -> null
                    b and 0x04 != 0 -> if (b and 0x02 != 0) 2 else 1
                    b == 0x00 || b == 0x01 -> 0
                    else -> null
                }
            }
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
                // Signed, as decoders/meb.py _coolant_temps: unsigned, a -1 C
                // coolant reading decoded as 1023 C.
                if (record.size < 4) return null
                mapOf(
                    "outlet_c" to (s16(record, 0) / 64.0),
                    "inlet_c" to (s16(record, 2) / 64.0),
                )
            }
            "pair" -> {
                // Two values in one DID: a scaled field at byte 0 and a raw
                // index at byte `width` (Python: _cs_pair_decoder). Both halves
                // are read from the same length check on purpose -- a truncated
                // answer must be a failed decode, not a partial one recorded as
                // complete. The field width comes from the spec, never a
                // constant here: 0x1E2C/0x1E2D are dl=2 with scale 0.01 today,
                // and a client that hard-coded those would silently mislabel
                // any future pair DID.
                val width = r.width
                if (width <= 0 || record.size < 2 * width) return null
                mapOf(
                    "value" to uint(record, 0, width) * r.scale / r.divisor + r.addend,
                    "index" to uint(record, width, width),
                )
            }
            "gear" -> gearCode(record)
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

    /** Big-endian signed 16-bit at [off]; caller bounds-checks. */
    private fun s16(b: ByteArray, off: Int): Int {
        val v = ((b[off].toInt() and 0xFF) shl 8) or (b[off + 1].toInt() and 0xFF)
        return if (v and 0x8000 != 0) v - 0x10000 else v
    }

    /** Big-endian unsigned integer of [len] bytes at [off]; caller bounds-checks. */
    private fun uint(b: ByteArray, off: Int, len: Int): Long {
        var v = 0L
        for (i in off until off + len) v = (v shl 8) or (b[i].toLong() and 0xFF)
        return v
    }

    /** Signed 32-bit big-endian at off. */
    private fun s32(b: ByteArray, off: Int): Long {
        val u = u32(b, off) ?: return 0
        return if (u and 0x8000_0000L != 0L) u - 0x1_0000_0000L else u
    }

    private fun gearCode(record: ByteArray): String {
        // Mirrors decoders/meb.py::_gear raw[1] exactly: data byte 1, read
        // unsigned (a Kotlin Byte is signed), and the whole record in the
        // fallback. The two maps were once different -- 0x04/0x10/0x20 here vs
        // 5/6/7/8/12 in Python -- so a parked car could report "raw 12" for B.
        val fallback = "raw " + record.joinToString("") {
            "%02X".format(it.toInt() and 0xFF)
        }
        if (record.size <= 1) return fallback
        return when (record[1].toInt() and 0xFF) {
            8 -> "P"
            7 -> "R"
            6 -> "N"
            5 -> "D"
            12 -> "B"
            else -> fallback
        }
    }
}
