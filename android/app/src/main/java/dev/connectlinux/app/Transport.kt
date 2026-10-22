package dev.connectlinux.app

import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothSocket
import android.content.Context
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.util.UUID

interface Transport {
    val input: InputStream
    val output: OutputStream
    fun close()
}

sealed class Target {
    abstract val label: String
    abstract fun open(context: Context): Transport

    data class Network(val host: String, val port: Int) : Target() {
        override val label: String
            get() = if (host == USB_HOST) "cabo USB" else host

        override fun open(context: Context): Transport {
            val socket = Socket()
            try {
                socket.tcpNoDelay = true
                socket.keepAlive = true
                socket.connect(InetSocketAddress(host, port), 8000)
            } catch (exc: Exception) {
                socket.close()
                throw exc
            }
            return object : Transport {
                override val input: InputStream = socket.getInputStream()
                override val output: OutputStream = socket.getOutputStream()
                override fun close() = socket.close()
            }
        }
    }

    data class Bluetooth(val address: String, val name: String) : Target() {
        override val label: String
            get() = "Bluetooth ($name)"

        @SuppressLint("MissingPermission")
        override fun open(context: Context): Transport {
            val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter
                ?: error("Este celular não tem Bluetooth.")
            if (!adapter.isEnabled) error("Ligue o Bluetooth do celular.")
            adapter.cancelDiscovery()
            val device = adapter.getRemoteDevice(address)
            val socket: BluetoothSocket = device.createRfcommSocketToServiceRecord(UUID.fromString(Protocol.BLUETOOTH_UUID))
            try {
                socket.connect()
            } catch (exc: Exception) {
                socket.close()
                throw IllegalStateException(
                    "Não foi possível conectar por Bluetooth. Abra o Connect Linux no computador e confira se os aparelhos estão pareados.",
                    exc,
                )
            }
            return object : Transport {
                override val input: InputStream = socket.inputStream
                override val output: OutputStream = socket.outputStream
                override fun close() = socket.close()
            }
        }
    }

    companion object {
        const val USB_HOST = "127.0.0.1"
    }
}
