package com.odbscanner.uds

import com.odbscanner.spec.ReadOnlyGuard

/**
 * The read-only choke point, ported from `diagnostic/uds.py`.
 *
 * SAFETY: every request that can reach the vehicle's diagnostic port MUST pass
 * through [validate] first. The rules are the same as the Python side and are
 * loaded from the shared spec, so the two platforms cannot drift:
 *
 *   * the service must be on the allow-list and not on the blocked list;
 *   * the length must be within [minRequestBytes, maxRequestBytes];
 *   * a service with a sub-function list may only send an allow-listed
 *     sub-function (bit 7, suppressPosRspMsgIndicationBit, is masked).
 *
 * Where it is enforced: [com.odbscanner.transport.Elm327Protocol.sendRead] is
 * the only way a vehicle request reaches the Bluetooth link, and it calls
 * [validate] on the exact bytes it then sends. The protocol holds the port
 * privately and builds every AT command itself. So the guarantee holds as long
 * as nothing else calls Elm327Port.transact -- keep it that way.
 */
class ReadOnlyViolation(message: String) : IllegalStateException(message)

class ReadOnlyGuardImpl(private val guard: ReadOnlyGuard) {

    /** Validate a whole outgoing request; returns the service id. */
    fun validate(payload: ByteArray): Int {
        if (payload.isEmpty()) {
            throw ReadOnlyViolation(
                "Refusing to transmit an empty request: this system is strictly READ-ONLY.")
        }
        val service = payload[0].toInt() and 0xFF
        validateService(service)

        if (payload.size > guard.maxRequestBytes) {
            throw ReadOnlyViolation(
                "Refusing to transmit a ${payload.size}-byte request: the " +
                    "${guard.maxRequestBytes}-byte read-request maximum was exceeded.")
        }
        guard.minRequestBytes[service]?.let { min ->
            if (payload.size < min) {
                throw ReadOnlyViolation(
                    "Refusing to transmit: service 0x${hex(service)} needs at least " +
                        "$min bytes, got ${payload.size}.")
            }
        }
        validateSubfunction(service, payload)
        return service
    }

    /** Build the read-only request for one DID: 22 <hi> <lo>. */
    fun buildReadDidRequest(did: Int): ByteArray {
        if (did !in 0..0xFFFF) throw IllegalArgumentException("DID out of range: $did")
        val req = byteArrayOf(0x22, (did shr 8).toByte(), (did and 0xFF).toByte())
        validate(req)
        return req
    }

    private fun validateService(service: Int) {
        if (guard.blockedServices.containsKey(service)) {
            throw ReadOnlyViolation(
                "Refusing service 0x${hex(service)} " +
                    "(${guard.blockedServices[service]}): this system is strictly READ-ONLY.")
        }
        if (!guard.allowedServices.containsKey(service)) {
            throw ReadOnlyViolation(
                "Refusing service 0x${hex(service)}: not on the read-only allow-list.")
        }
    }

    private fun validateSubfunction(service: Int, payload: ByteArray) {
        val allowed = guard.readSubfunctions[service] ?: return
        if (payload.size < 2) {
            throw ReadOnlyViolation(
                "Refusing: service 0x${hex(service)} requires a sub-function byte.")
        }
        // Bit 7 is suppressPosRspMsgIndicationBit (silences the positive
        // response); mask it so a DID whose high bit is set still validates.
        val sub = (payload[1].toInt() and 0xFF) and 0x7F
        if (sub !in allowed) {
            throw ReadOnlyViolation(
                "Refusing sub-function 0x${hex(sub)} of service 0x${hex(service)}: " +
                    "not a read. Allowed: ${allowed.joinToString { hex(it) }}.")
        }
    }

    private fun hex(v: Int): String = v.toString(16).uppercase()
}
