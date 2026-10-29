package com.odbscanner.collect

import com.odbscanner.spec.Decode
import com.odbscanner.spec.EcuSpec
import com.odbscanner.spec.MeasurementSpec
import com.odbscanner.spec.ProfileSpec
import com.odbscanner.spec.ReadOnlyGuard
import com.odbscanner.spec.Spec
import com.odbscanner.transport.Elm327Port
import com.odbscanner.transport.Elm327Protocol
import com.odbscanner.uds.ReadOnlyGuardImpl
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** An ELM327Port that answers scripted DID reads and counts them. */
private class ReadingPort(private val answers: Map<String, List<String>>) : Elm327Port {
    val reads = mutableMapOf<String, Int>()

    override fun transact(line: String): List<String> {
        if (line.startsWith("22")) reads[line] = (reads[line] ?: 0) + 1
        return answers[line] ?: listOf("OK")
    }
}

/**
 * The collection loop's scheduling and retirement rules.
 *
 * These are the bugs a phone-only app can hide for a while because nobody
 * watches it run: every slow DID read on every fast pass, a cell voltage read
 * twice per rotation, and a DID retired because the adapter was unreachable.
 * The tests use a scripted port, a clock that only moves when they move it,
 * and no repository, so they stay on the JVM.
 */
class CollectorTest {

    private val vin = "WVWZZZE1ZMP000001"

    private fun ecu(key: String) = EcuSpec(
        key = key,
        name = key,
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
        allowedServices = mapOf(0x22 to "read data by identifier"),
        blockedServices = mapOf(0x2E to "write data by identifier"),
        readSubfunctions = emptyMap(),
        minRequestBytes = mapOf(0x22 to 3),
        maxRequestBytes = 7,
        nrcNames = emptyMap(),
    )

    private fun spec(measurements: List<MeasurementSpec>) = Spec(
        specVersion = "test",
        ecus = mapOf(
            "bat_mgmt" to ecu("bat_mgmt"),
            "veh_info" to ecu("veh_info"),
        ),
        guard = guard(),
        profiles = mapOf("meb" to ProfileSpec(measurements)),
    )

    private fun scalar(key: String, did: Int, slow: Boolean = false) = MeasurementSpec(
        key = key,
        name = key,
        unit = "",
        ecuKey = "bat_mgmt",
        did = did,
        slow = slow,
        decode = Decode.Scalar(0, 2, false, 0.001, 1.0, 0.0, null),
    )

    private fun cell(index: Int, did: Int) = MeasurementSpec(
        key = "cell_v_" + index.toString().padStart(3, '0'),
        name = "cell $index",
        unit = "V",
        ecuKey = "bat_mgmt",
        did = did,
        slow = true,
        decode = Decode.Special("cell_voltage", null),
    )

    private fun vinMeasurement() = MeasurementSpec(
        key = "vin",
        name = "VIN",
        unit = "",
        ecuKey = "veh_info",
        did = 0xF802,
        slow = true,
        decode = Decode.Special("vin", null),
    )

    /** One ISO-TP single frame, header off, for a request -> record pair. */
    private fun single(request: String, recordHex: String): String {
        val payload = "62" + request.substring(2) + recordHex
        val n = payload.length / 2
        check(n <= 7) { "single frame would need $n bytes" }
        return n.toString(16).uppercase().padStart(2, '0') + payload
    }

    /** ISO-TP multi-frame lines, header off, for an arbitrary payload hex. */
    private fun multi(payloadHex: String): List<String> {
        val bytes = payloadHex.chunked(2)
        val out = mutableListOf<String>()
        out.add(
            "10" + bytes.size.toString(16).uppercase().padStart(2, '0') +
                bytes.take(6).joinToString(""),
        )
        var index = 6
        var seq = 1
        while (index < bytes.size) {
            val chunk = bytes.drop(index).take(7)
            val header = (0x20 or (seq % 16)).toString(16).uppercase().padStart(2, '0')
            out.add(header + chunk.joinToString("") + "00".repeat(7 - chunk.size))
            index += 7
            seq++
        }
        return out
    }

    private fun asciiHex(text: String) =
        text.toByteArray(Charsets.US_ASCII).joinToString("") {
            (it.toInt() and 0xFF).toString(16).padStart(2, '0').uppercase()
        }

    private fun collector(
        measurements: List<MeasurementSpec>,
        port: ReadingPort,
        now: () -> Long = { 0L },
        slowIntervalMs: Long = 60_000L,
    ): Collector {
        val protocol = Elm327Protocol(port, ReadOnlyGuardImpl(guard()), resetDelayMs = 0)
        protocol.init()
        return Collector(
            spec = spec(measurements),
            profile = ProfileSpec(measurements),
            protocol = protocol,
            guard = ReadOnlyGuardImpl(guard()),
            repository = null,
            slowIntervalMs = slowIntervalMs,
            nowMs = now,
        )
    }

    @Test
    fun `slow DIDs run on their own cadence and cells are read once`() = runBlocking {
        var now = 1_000L
        val port = ReadingPort(
            mapOf(
                "221E3B" to listOf(single("221E3B", "0BB8")),
                "221E40" to listOf(single("221E40", "0C1C")),
            ),
        )
        val measurements = listOf(
            scalar("fast", 0x1E3B),
            cell(0, 0x1E40),
        )
        val c = collector(measurements, port, now = { now })

        val first = c.collectOnce()
        assertEquals(listOf("fast", "cell_v_000"), first.map { it.key })
        // 0x0C1C = 3100 -> 3.1 V + 1 V offset (cell_voltage decoder).
        assertEquals(4.1, first[1].value as Double, 1e-9)

        now += 1_000
        val second = c.collectOnce()
        assertEquals("a fast pass must not read the slow cell", listOf("fast"),
            second.map { it.key })
        assertEquals(1, port.reads["221E40"])
        assertEquals(2, port.reads["221E3B"])

        now += 60_000
        val third = c.collectOnce()
        assertEquals(listOf("fast", "cell_v_000"), third.map { it.key })
        assertEquals(2, port.reads["221E40"])
    }

    @Test
    fun `a carried value stops feeding derived values once it is too old`() = runBlocking {
        var now = 1_000L
        val port = ReadingPort(
            mapOf(
                "221E3B" to listOf(single("221E3B", "0BB8")),
                "221E40" to listOf(single("221E40", "0C1C")),
            ),
        )
        val c = collector(listOf(scalar("fast", 0x1E3B), cell(0, 0x1E40)), port,
            now = { now }, slowIntervalMs = 2_000_000L)
        c.collectOnce()
        assertEquals(setOf("fast", "cell_v_000"), c.freshSnapshot().keys)

        now += 900_000                                  // the slow DID is not re-read
        c.collectOnce()
        assertEquals(setOf("fast", "cell_v_000"), c.freshSnapshot().keys)

        now += 1
        c.collectOnce()
        assertEquals("a value read 900 s ago must no longer count",
            setOf("fast"), c.freshSnapshot().keys)
    }

    @Test
    fun `NRC 0x31 retires the DID from the next pass`() = runBlocking {
        val port = ReadingPort(mapOf("221E3B" to listOf("037F2231")))
        val c = collector(listOf(scalar("fast", 0x1E3B)), port)

        val first = c.collectOnce()
        assertEquals("nrc-0x31", first.single().status)

        val second = c.collectOnce()
        assertTrue("a retired DID was polled again: $second", second.isEmpty())
        assertEquals(1, port.reads["221E3B"])
    }

    @Test
    fun `a DID that never answers is retired after three silent passes`() = runBlocking {
        // Its module answers another DID in the same pass, so the module was
        // reached and this DID's silence is evidence it does not exist.
        val port = ReadingPort(mapOf(
            "221E3B" to listOf("NO DATA"),
            "221E3C" to listOf(single("221E3C", "0BB8")),
        ))
        val c = collector(listOf(scalar("fast", 0x1E3B), scalar("alive", 0x1E3C)), port)

        repeat(3) {
            val readings = c.collectOnce()
            assertEquals("no-response", readings.first { it.key == "fast" }.status)
        }
        assertEquals(3, port.reads["221E3B"])

        assertTrue("silent DID not retired at the threshold",
            c.collectOnce().none { it.key == "fast" })
        assertEquals(3, port.reads["221E3B"])
    }

    @Test
    fun `silence from a module that answered nothing never retires its DIDs`() = runBlocking {
        // The car asleep, or the bus unreachable: every DID of the module says
        // NO DATA. That is not evidence about any one DID. Without this guard
        // a phone that boots with the ignition off retired the whole map.
        val port = ReadingPort(mapOf(
            "221E3B" to listOf("NO DATA"),
            "221E3C" to listOf("NO DATA"),
        ))
        val c = collector(listOf(scalar("a", 0x1E3B), scalar("b", 0x1E3C)), port)

        repeat(6) { assertEquals(2, c.collectOnce().size) }
        assertEquals(6, port.reads["221E3B"])
    }

    @Test
    fun `adapter refusals never count as silence`() = runBlocking {
        val port = ReadingPort(mapOf(
            "221E3B" to listOf("?"),
            "221E3C" to listOf(single("221E3C", "0BB8")),
        ))
        val c = collector(listOf(scalar("refused", 0x1E3B), scalar("alive", 0x1E3C)), port)

        repeat(6) { c.collectOnce() }
        assertEquals("a refused DID was retired", 6, port.reads["221E3B"])
    }

    @Test
    fun `a DID that answered before is never retired for silence`() = runBlocking {
        // Answer once, then go quiet: that is a sleeping car, not a missing
        // DID, and retiring it would lose it until the app's data is cleared.
        val answers = mutableMapOf("221E3B" to listOf(single("221E3B", "0BB8")))
        val port = ReadingPort(answers)
        val c = collector(listOf(scalar("fast", 0x1E3B)), port)

        assertEquals("ok", c.collectOnce().single().status)
        answers["221E3B"] = listOf("NO DATA")
        repeat(5) { assertEquals("no-response", c.collectOnce().single().status) }
        assertEquals(6, port.reads["221E3B"])
    }

    @Test
    fun `collection waits for the VIN instead of using vehicle 1`() = runBlocking {
        // A car phone booting with the ignition off cannot read the VIN; the
        // runner used to collect anyway under fallback vehicle id 1.
        val answers = mutableMapOf("22F802" to listOf("NO DATA"))
        val port = ReadingPort(answers)
        val c = collector(listOf(vinMeasurement()), port)
        val waits = mutableListOf<String>()
        var reads = 0
        val result = com.odbscanner.service.awaitVehicle(c, 0L) { msg ->
            waits.add(msg)
            reads++
            if (reads == 2) answers["22F802"] = multi("62F802" + asciiHex(vin))
        }
        assertEquals(vin, result)
        assertEquals(2, waits.size)
        assertTrue(waits.all { "nothing is recorded" in it })
    }

    @Test
    fun `identifyVehicle decodes the VIN and rejects a malformed one`() = runBlocking {
        val good = ReadingPort(
            mapOf("22F802" to multi("62F802" + asciiHex(vin))),
        )
        val c = collector(listOf(vinMeasurement()), good)
        assertEquals(vin, c.identifyVehicle())
        assertEquals(vin, c.vin)
    }

    @Test
    fun `a short VIN is ignored rather than stored`() = runBlocking {
        val short = "WVWZZZE1ZMP00000"   // 16 characters
        val port = ReadingPort(
            mapOf("22F802" to multi("62F802" + asciiHex(short))),
        )
        val c = collector(listOf(vinMeasurement()), port)
        assertNull(c.identifyVehicle())
        assertNull(c.vin)
    }

    @Test
    fun `a numeric decode stays numeric and text goes to text_value`() {
        assertEquals(12.5 to null, measurementColumns(12.5))
        assertEquals(31.4 to null, measurementColumns("31.4"))
        assertEquals(null to "P", measurementColumns("P"))
        assertEquals(1.0 to null, measurementColumns(true))
        assertEquals(null to null, measurementColumns(null))
    }

    @Test
    fun `a structured decode is JSON with no numeric value`() {
        val pair = mapOf("value" to 12.34, "index" to 7)
        assertEquals(null to """{"value":12.34,"index":7}""", measurementColumns(pair))
    }

    @Test
    fun `non-finite numbers are preserved as text`() {
        assertEquals(null to "NaN", measurementColumns(Double.NaN))
    }

    @Test
    fun `derived values are computed from the snapshot`() {
        val snapshot = mapOf<String, Any?>(
            "pack_voltage" to 400.0,
            "pack_current" to 100.0,
            "cell_voltage_max" to 4.1,
            "cell_voltage_min" to 3.9,
            "aux_12v_voltage" to 13.0,
            "aux_12v_current" to -2.0,
            "cell_soc_max" to mapOf("value" to 55.0, "index" to 3),
            "cell_soc_min" to mapOf("value" to 50.0, "index" to 7),
            "bat_temp_max" to 30.0,
            "bat_temp_min" to 25.0,
            "hv_energy_max" to 55_100.0,
        )
        val derived = computeDerived(snapshot).associateBy { it.key }

        assertEquals(40.0, derived.getValue("pack_power_kw").value, 1e-9)
        assertEquals(200.0, derived.getValue("cell_delta_mv").value, 1e-9)
        assertEquals(-26.0, derived.getValue("aux_12v_power_w").value, 1e-9)
        assertEquals(5.0, derived.getValue("cell_soc_spread_pct").value, 1e-9)
        assertEquals(5.0, derived.getValue("battery_temp_spread_c").value, 1e-9)
        assertEquals(95.0, derived.getValue("soh_pct").value, 1e-6)
        assertEquals("estimated", derived.getValue("soh_pct").provenance)
        assertEquals("calculated", derived.getValue("pack_power_kw").provenance)
    }

    @Test
    fun `derived values omit what the snapshot lacks`() {
        val derived = computeDerived(mapOf("pack_voltage" to 400.0))
        assertTrue("pack power needs current", derived.none { it.key == "pack_power_kw" })
        assertTrue("SoH needs energy", derived.none { it.key == "soh_pct" })
    }
}
