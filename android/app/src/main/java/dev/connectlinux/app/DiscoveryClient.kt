package dev.connectlinux.app

import android.content.Context
import android.net.wifi.WifiManager
import android.os.SystemClock
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.MulticastSocket
import java.net.SocketAddress
import java.net.SocketTimeoutException

class DiscoveryClient(
    context: Context,
    private val deviceId: String,
    private val nameProvider: () -> String,
    private val onAnnounce: (id: String, name: String, host: String, port: Int) -> Unit,
    private val onFailure: (String) -> Unit,
) {
    private val wifi = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
    @Volatile private var running = false
    private var thread: Thread? = null
    private var socket: DatagramSocket? = null

    fun start() {
        if (thread?.isAlive == true) return
        running = true
        thread = Thread({ runLoop() }, "connectlinux-discovery").also { it.start() }
    }

    fun stop() {
        running = false
        try {
            socket?.close()
        } catch (_: Exception) {
        }
        thread?.join(1000)
        thread = null
    }

    private fun runLoop() {
        val lock = try {
            wifi.createMulticastLock("connectlinux").apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (_: Exception) {
            null
        }
        try {
            val opened = MulticastSocket(null as SocketAddress?).apply {
                reuseAddress = true
                broadcast = true
                soTimeout = 2000
                bind(InetSocketAddress(Protocol.DISCOVERY_PORT))
            }
            socket = opened
            try {
                opened.joinGroup(InetAddress.getByName(Protocol.MULTICAST))
            } catch (_: Exception) {
            }
            var nextSend = 0L
            val buffer = ByteArray(2048)
            while (running) {
                val now = SystemClock.elapsedRealtime()
                if (now >= nextSend) {
                    broadcast(opened, announce())
                    broadcast(opened, discover())
                    nextSend = now + 2500
                }
                val packet = DatagramPacket(buffer, buffer.size)
                try {
                    opened.receive(packet)
                } catch (_: SocketTimeoutException) {
                    continue
                } catch (_: Exception) {
                    if (!running) break
                    continue
                }
                handle(opened, packet)
            }
        } catch (exc: Exception) {
            if (running) {
                onFailure("Busca automática indisponível. Conecte pelo IP do computador.")
            }
        } finally {
            try {
                socket?.close()
            } catch (_: Exception) {
            }
            socket = null
            try {
                lock?.release()
            } catch (_: Exception) {
            }
        }
    }

    private fun handle(opened: DatagramSocket, packet: DatagramPacket) {
        val text = String(packet.data, packet.offset, packet.length, Charsets.UTF_8)
        val message = try {
            JSONObject(text)
        } catch (_: Exception) {
            return
        }
        if (message.optString("app") != Protocol.APP || message.optInt("v") != Protocol.VERSION) return
        if (message.optString("id") == deviceId) return
        when (message.optString("type")) {
            "discover" -> sendTo(opened, announce(), packet.address.hostAddress ?: return, Protocol.DISCOVERY_PORT)
            "announce" -> {
                if (message.optString("platform") != "linux") return
                val id = message.optString("id")
                val port = message.optInt("port", 0)
                val host = packet.address.hostAddress ?: return
                if (!Protocol.isDeviceId(id) || port !in 1..65535) return
                onAnnounce(id, Protocol.sanitizeName(message.optString("name")), host, port)
            }
        }
    }

    private fun announce(): ByteArray {
        return JSONObject()
            .put("app", Protocol.APP)
            .put("v", Protocol.VERSION)
            .put("type", "announce")
            .put("id", deviceId)
            .put("name", Protocol.sanitizeName(nameProvider()))
            .put("port", Protocol.LINK_PORT)
            .put("platform", "android")
            .toString()
            .toByteArray(Charsets.UTF_8)
    }

    private fun discover(): ByteArray {
        return JSONObject()
            .put("app", Protocol.APP)
            .put("v", Protocol.VERSION)
            .put("type", "discover")
            .put("id", deviceId)
            .toString()
            .toByteArray(Charsets.UTF_8)
    }

    private fun broadcast(opened: DatagramSocket, payload: ByteArray) {
        sendTo(opened, payload, "255.255.255.255", Protocol.DISCOVERY_PORT)
        sendTo(opened, payload, Protocol.MULTICAST, Protocol.DISCOVERY_PORT)
    }

    private fun sendTo(opened: DatagramSocket, payload: ByteArray, host: String, port: Int) {
        try {
            val address = InetAddress.getByName(host)
            opened.send(DatagramPacket(payload, payload.size, address, port))
        } catch (_: Exception) {
        }
    }
}
