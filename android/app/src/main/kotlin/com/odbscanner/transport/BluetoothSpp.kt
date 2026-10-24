package com.odbscanner.transport

import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothSocket
import java.io.IOException
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.util.UUID
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** The adapter did not send its ">" prompt in time. */
class Elm327TimeoutError(val command: String, val timeoutMs: Int) : IOException(
    "No prompt from the ELM327 within ${timeoutMs} ms after '${command.take(40)}'"
)

/**
 * A classic-Bluetooth SPP (RFCOMM) link to an ELM327 adapter, as an
 * [Elm327Port]. The ELM327 side is identical to the Windows COM-port path:
 * AT commands and data frames over a byte stream, terminated by the ">" prompt.
 *
 * Reading is done on a background thread that feeds a queue, because
 * `BluetoothSocket` has no read timeout (unlike a TCP socket). Without one, a
 * single unanswered command blocks the collector for ever: the old version sat
 * in `InputStreamReader.read()` with no way out, could not be cancelled, and
 * never reached the `finally` that closes the socket -- a dead adapter looked
 * like a frozen app. Here every exchange has a deadline, and [close] wakes
 * anything waiting.
 */
class BluetoothSpp private constructor(
    private val socket: BluetoothSocket,
    private val readTimeoutMs: Int,
) : Elm327Port {

    private val writer = OutputStreamWriter(socket.outputStream, Charsets.US_ASCII)
    private val reader = InputStreamReader(socket.inputStream, Charsets.US_ASCII)

    /** Characters received but not yet consumed by a [transact] call. */
    private val chars = LinkedBlockingQueue<Int>()

    @Volatile private var closed = false

    private val pump = Thread({ pumpLoop() }, "elm327-reader").apply {
        isDaemon = true
        start()
    }

    private fun pumpLoop() {
        try {
            while (!closed) {
                val ch = reader.read()
                if (ch == -1) break
                chars.put(ch)
            }
        } catch (_: IOException) {
            // A dropped link surfaces here; the EOF below wakes the reader.
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        } finally {
            chars.offer(EOF)
        }
    }

    override fun transact(line: String): List<String> {
        if (closed) throw IOException("Bluetooth link is closed")
        // Anything already queued belongs to an earlier exchange (a timed-out
        // answer, an unsolicited line). Reading it as this command's response
        // would shift every subsequent frame by one.
        chars.clear()
        try {
            writer.write(line + "\r")
            writer.flush()
        } catch (e: IOException) {
            throw IOException("Bluetooth write failed: ${e.message}", e)
        }

        val out = StringBuilder()
        while (true) {
            val ch = try {
                chars.poll(readTimeoutMs.toLong(), TimeUnit.MILLISECONDS)
            } catch (e: InterruptedException) {
                Thread.currentThread().interrupt()
                throw IOException("Interrupted while waiting for the ELM327", e)
            }
            when {
                ch == null -> {
                    // Leave nothing behind for the next command to misread.
                    chars.clear()
                    throw Elm327TimeoutError(line, readTimeoutMs)
                }
                ch == EOF -> break
                ch == '>'.code -> break
                else -> out.append(ch.toChar())
            }
        }
        return out.toString()
            .split('\r', '\n')
            .map { it.trim() }
            .filter { it.isNotEmpty() }
    }

    fun close() {
        if (closed) return
        closed = true
        try {
            socket.close()
        } catch (_: IOException) {
            // already closed
        }
        // Wake any transact() waiting on the queue.
        chars.offer(EOF)
    }

    companion object {
        /** The classic Bluetooth Serial Port Profile UUID. */
        private val SPP_UUID: UUID =
            UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")

        /** An AT command or a DID read answers well inside this on a live bus. */
        const val DEFAULT_READ_TIMEOUT_MS = 5_000

        /** Queue sentinel: the reader thread ended or the socket was closed. */
        private const val EOF = -2

        /** Open an RFCOMM socket to [device] and wrap it. */
        @SuppressLint("MissingPermission")
        fun connect(
            device: BluetoothDevice,
            readTimeoutMs: Int = DEFAULT_READ_TIMEOUT_MS,
        ): BluetoothSpp {
            val socket = device.createRfcommSocketToServiceRecord(SPP_UUID)
            socket.connect()
            return BluetoothSpp(socket, readTimeoutMs)
        }
    }
}
