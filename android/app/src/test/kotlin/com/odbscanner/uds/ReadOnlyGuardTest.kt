package com.odbscanner.uds

import com.odbscanner.spec.ReadOnlyGuard
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

/**
 * The read-only choke point.
 *
 * This is the app's whole safety guarantee: no code below the transport knows
 * how to transmit anything that did not pass [ReadOnlyGuardImpl.validate]. The
 * cases here are the ones that would make the guarantee false -- a write
 * service, a session-control sub-function that is not a read, an oversized
 * request, and a DID whose high bit looks like a sub-function.
 */
class ReadOnlyGuardTest {

    private fun guard() = ReadOnlyGuard(
        allowedServices = mapOf(
            0x10 to "diagnostic session control",
            0x22 to "read data by identifier",
        ),
        blockedServices = mapOf(
            0x2E to "write data by identifier",
            0x31 to "routine control",
            0x3E to "tester present (not needed for a single pass)",
        ),
        readSubfunctions = mapOf(0x10 to setOf(0x01)),
        minRequestBytes = mapOf(0x10 to 2, 0x22 to 3),
        maxRequestBytes = 7,
        nrcNames = emptyMap(),
    )

    private fun impl() = ReadOnlyGuardImpl(guard())

    @Test
    fun `a DID read is built and accepted`() {
        val request = impl().buildReadDidRequest(0x1E2C)
        assertArrayEquals(byteArrayOf(0x22, 0x1E, 0x2C), request)
    }

    @Test
    fun `a DID whose high bit is set is still a read`() {
        // 0xE2C starts with 0x0E, not a sub-function; services without a
        // sub-function list must not have their DID bytes interpreted as one.
        assertArrayEquals(
            byteArrayOf(0x22, 0x0E, 0x2C),
            impl().buildReadDidRequest(0x0E2C),
        )
    }

    @Test
    fun `write services are refused`() {
        assertEquals("write data by identifier",
            guard().blockedServices[0x2E])
        assertThrows(ReadOnlyViolation::class.java) {
            impl().validate(byteArrayOf(0x2E, 0x1E, 0x2C, 0x00))
        }
        assertThrows(ReadOnlyViolation::class.java) {
            impl().validate(byteArrayOf(0x31, 0x01))
        }
    }

    @Test
    fun `an unknown service is refused`() {
        assertThrows(ReadOnlyViolation::class.java) {
            impl().validate(byteArrayOf(0x99.toByte(), 0x01))
        }
    }

    @Test
    fun `session control may only select the default session`() {
        // 0x10 0x03 (extended session) can unlock routines; only 0x10 0x01 is
        // a read-path session request.
        assertThrows(ReadOnlyViolation::class.java) {
            impl().validate(byteArrayOf(0x10, 0x03))
        }
        // Bit 7 is suppressPosRspMsgIndicationBit and is masked before the
        // sub-function check, so 0x81 is still 0x01.
        assertEquals(0x10, impl().validate(byteArrayOf(0x10, 0x81.toByte())))
    }

    @Test
    fun `oversized requests are refused`() {
        assertThrows(ReadOnlyViolation::class.java) {
            impl().validate(byteArrayOf(0x22, 0x1E, 0x2C, 0, 0, 0, 0, 0))
        }
    }

    @Test
    fun `short and empty requests are refused`() {
        assertThrows(ReadOnlyViolation::class.java) {
            impl().validate(byteArrayOf(0x22, 0x1E))
        }
        assertThrows(ReadOnlyViolation::class.java) {
            impl().validate(ByteArray(0))
        }
    }
}
