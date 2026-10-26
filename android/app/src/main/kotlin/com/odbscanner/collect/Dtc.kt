package com.odbscanner.collect

/**
 * DTC decoding, ported from `diagnostic/obd2.py` and `analysis/dtc.py`.
 *
 * The bit layouts are documented in ISO 14229 / SAE J2012-DA: two bits select
 * the system letter (P/C/B/U), two bits the first digit, four bits the second
 * digit, then the third/fourth digits, then (for UDS) the failure-type byte.
 * Getting these shifts wrong invents faults that were never set and drops the
 * one that was, so they are pinned here with tests.
 */

private val DTC_LETTERS = arrayOf("P", "C", "B", "U")

/** 2-byte OBD-II code -> e.g. "P0143". */
fun decodeObd2Dtc(b1: Int, b2: Int): String {
    val letter = DTC_LETTERS[(b1 shr 6) and 0b11]
    val d1 = (b1 shr 4) and 0b11
    val d2 = (b1 and 0xF).toString(16).uppercase()
    val d3 = (b2 shr 4).toString(16).uppercase()
    val d4 = (b2 and 0xF).toString(16).uppercase()
    return "$letter$d1$d2$d3$d4"
}

/** 3-byte UDS DTC -> e.g. "U112300". */
fun decodeVagDtc(b1: Int, b2: Int, b3: Int): String {
    val letter = DTC_LETTERS[(b1 shr 6) and 0b11]
    val d1 = (b1 shr 4) and 0b11
    val d2 = (b1 and 0xF).toString(16).uppercase()
    val d3 = (b2 shr 4).toString(16).uppercase()
    val d4 = (b2 and 0xF).toString(16).uppercase()
    val d5 = (b3 and 0xFF).toString(16).uppercase().padStart(2, '0')
    return "$letter$d1$d2$d3$d4$d5"
}

/** One short word for an ISO 14229 status byte, most-alerting bit first. */
fun dtcStatusLabel(statusByte: Int?): String = when {
    statusByte == null -> "unknown"
    statusByte and 0x80 != 0 -> "warning"
    statusByte and 0x01 != 0 -> "active"
    statusByte and 0x04 != 0 -> "pending"
    statusByte and 0x08 != 0 -> "confirmed"
    else -> "stored"
}

/**
 * Parse a UDS 0x19 0x02 positive response (after the 59 02 status-mask byte):
 * repeated 3-byte DTC + 1-byte status. Returns (code, statusByte) pairs.
 */
fun parseUdsDtcResponse(payload: ByteArray): List<Pair<String, Int>> {
    if (payload.size < 3 ||
        (payload[0].toInt() and 0xFF) != 0x59 ||
        (payload[1].toInt() and 0xFF) != 0x02
    ) return emptyList()
    val body = payload.copyOfRange(3, payload.size)
    val out = mutableListOf<Pair<String, Int>>()
    var i = 0
    while (i + 4 <= body.size) {
        val b1 = body[i].toInt() and 0xFF
        val b2 = body[i + 1].toInt() and 0xFF
        val b3 = body[i + 2].toInt() and 0xFF
        val status = body[i + 3].toInt() and 0xFF
        if (b1 or b2 or b3 != 0) out.add(decodeVagDtc(b1, b2, b3) to status)
        i += 4
    }
    return out
}

/**
 * Parse an OBD mode 03 positive response: `43 <count> [(b1,b2) x count]`.
 * [payload] must start with the 0x43 marker (as reassembled from the wire).
 */
fun parseObd03DtcResponse(payload: ByteArray): List<String> {
    if (payload.isEmpty() || (payload[0].toInt() and 0xFF) != 0x43) return emptyList()
    val body = payload.copyOfRange(1, payload.size)
    if (body.isEmpty()) return emptyList()
    val count = body[0].toInt() and 0xFF
    if (count == 0) return emptyList()
    val data = body.copyOfRange(1, body.size)
    val pairCount = minOf(data.size / 2, count)
    val out = mutableListOf<String>()
    for (i in 0 until pairCount) {
        val b1 = data[i * 2].toInt() and 0xFF
        val b2 = data[i * 2 + 1].toInt() and 0xFF
        if (b1 or b2 != 0) out.add(decodeObd2Dtc(b1, b2))
    }
    return out
}
