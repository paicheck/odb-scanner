package com.odbscanner.transport

import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothSocket
import java.io.IOException
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.util.UUID

/**
 * A classic-Bluetooth SPP (RFCOMM) link to an ELM327 adapter, as an
 * [Elm327Port]. The ELM327 side is identical to the Windows COM-port path:
 * AT commands and data frames over a byte stream, terminated by the ">" prompt.
 */
class BluetoothSpp(private val socket: BluetoothSocket) : Elm327Port {

    private val writer = OutputStreamWriter(socket.outputStream, Charsets.US_ASCII)
    private val reader = InputStreamReader(socket.inputStream, Charsets.US_ASCII)

    override fun transact(line: String): List<String> {
        writer.write(line + "\r")
        writer.flush()
        val out = StringBuilder()
        try {
            while (true) {
                val ch = reader.read()
                if (ch == -1) break
                if (ch == '>'.code) break  // ELM327 prompt
                out.append(ch.toChar())
            }
        } catch (_: IOException) {
            // a dropped link surfaces here; the caller treats it as no response
        }
        return out.toString()
            .split('\r', '\n')
            .map { it.trim() }
            .filter { it.isNotEmpty() }
    }

    fun close() {
        try { socket.close() } catch (_: IOException) { /* already closed */ }
    }

    companion object {
        /** The classic Bluetooth Serial Port Profile UUID. */
        private val SPP_UUID: UUID =
            UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")

        /** Open an RFCOMM socket to [device] and wrap it. */
        @SuppressLint("MissingPermission")
        fun connect(device: BluetoothDevice, timeoutMs: Int = 5000): BluetoothSpp {
            val socket = device.createRfcommSocketToServiceRecord(SPP_UUID)
            socket.connect()
            return BluetoothSpp(socket)
        }
    }
}
