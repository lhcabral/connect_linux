package dev.connectlinux.app

import android.content.Context
import android.net.Uri
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.EOFException
import java.io.File
import java.io.InputStream
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

class LinkClient(
    private val context: Context,
    private val target: Target,
    private val deviceId: String,
    private val deviceName: String,
    private val forcePair: Boolean,
    private val tokenFor: (String) -> String?,
    private val listener: Listener,
) {
    interface Listener {
        fun onReady(desktopId: String, desktopName: String, token: String?)
        fun onPairRequired(name: String)
        fun onPairFailed(message: String)
        fun onPairRejected(message: String)
        fun onAuthFailed(desktopId: String, message: String)
        fun onClipboard(text: String)
        fun onText(text: String)
        fun onOpenUrl(url: String)
        fun onFind()
        fun onBattery(level: Int, charging: Boolean)
        fun onLatency(ms: Int)
        fun onUnpair()
        fun onPhotosRequest(offset: Int, limit: Int)
        fun onPhotoRequest(id: Long)
        fun onEvent(text: String)
        fun openDownload(name: String, mime: String): FileSink
        fun onFileSaved(name: String, mime: String, uri: Uri, open: Boolean)
        fun onClosed(reason: String)
    }

    private val writeLock = Any()
    private val gates = ConcurrentHashMap<String, CompletableFuture<JSONObject>>()
    @Volatile private var transport: Transport? = null
    private var output: java.io.OutputStream? = null
    private var incoming: Incoming? = null
    @Volatile private var closed = false
    @Volatile private var finished = false
    @Volatile private var lastPingTs = 0L
    @Volatile private var lastRx = System.nanoTime()
    @Volatile private var idleLimitMs = 20_000L
    @Volatile private var idleMessage = "O computador não respondeu."

    fun submitCode(code: String) {
        send(JSONObject().put("type", "pair_submit").put("code", code.trim()))
    }

    fun sendText(text: String) {
        send(JSONObject().put("type", "text").put("text", text.take(Protocol.MAX_CLIPBOARD)))
    }

    fun sendUrl(url: String) {
        send(JSONObject().put("type", "open_url").put("url", url.trim()))
    }

    fun sendClipboard(text: String) {
        if (text.isEmpty() || text.length > Protocol.MAX_CLIPBOARD) return
        send(JSONObject().put("type", "clipboard").put("text", text).put("ts", System.currentTimeMillis()))
    }

    fun sendNotification(id: String, app: String, title: String, body: String) {
        send(
            JSONObject()
                .put("type", "notification")
                .put("id", id.take(80))
                .put("app", app.take(80))
                .put("title", title.take(180))
                .put("body", body.take(600))
                .put("ts", System.currentTimeMillis()),
        )
    }

    fun sendBattery(level: Int, charging: Boolean) {
        send(JSONObject().put("type", "battery").put("level", level).put("charging", charging))
    }

    fun sendPhotos(offset: Int, page: PhotoLibrary.Page, partial: Boolean, folder: String) {
        send(
            JSONObject()
                .put("type", "photos")
                .put("offset", offset)
                .put("next", page.next)
                .put("more", page.more)
                .put("total", page.total)
                .put("skipped", page.skipped)
                .put("partial", partial)
                .put("folder", folder)
                .put("items", page.items),
        )
    }

    fun sendPhotosError(offset: Int, error: String) {
        send(JSONObject().put("type", "photos").put("offset", offset).put("error", error).put("items", JSONArray()))
    }

    fun sendPing() {
        val now = System.currentTimeMillis()
        lastPingTs = now
        send(JSONObject().put("type", "ping").put("ts", now))
    }

    fun findComputer() {
        send(JSONObject().put("type", "find"))
    }

    fun unpair() {
        send(JSONObject().put("type", "unpair"))
    }

    fun sendFile(file: File, displayName: String, mime: String, open: Boolean = false) {
        val size = file.length()
        if (size > Protocol.MAX_FILE) {
            listener.onEvent("Arquivo maior que 100 MB.")
            return
        }
        val name = Protocol.sanitizeFilename(displayName)
        val digest = Protocol.sha256(file)
        val id = UUID.randomUUID().toString().replace("-", "")
        val decision = CompletableFuture<JSONObject>()
        gates["$id:accept"] = decision
        try {
            send(
                JSONObject()
                    .put("type", "file_meta")
                    .put("id", id)
                    .put("name", name)
                    .put("size", size)
                    .put("mime", mime.ifBlank { "application/octet-stream" })
                    .put("sha256", digest)
                    .put("open", open),
            )
            val answer = decision.get(120, TimeUnit.SECONDS)
            if (answer.optString("type") != "file_accept") {
                listener.onEvent(answer.optString("message", "O computador recusou $name."))
                return
            }
            val done = CompletableFuture<JSONObject>()
            gates["$id:done"] = done
            listener.onEvent("Enviando $name…")
            file.inputStream().use { input ->
                var sent = 0L
                var sequence = 0
                var shown = -10
                val buffer = ByteArray(Protocol.CHUNK)
                if (size == 0L) {
                    send(chunkMessage(id, 0, true, ByteArray(0)))
                }
                while (sent < size) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    sent += read
                    val last = sent >= size
                    val slice = if (read == buffer.size) buffer else buffer.copyOf(read)
                    send(chunkMessage(id, sequence, last, slice))
                    sequence += 1
                    val percent = if (size == 0L) 100 else ((sent * 100) / size).toInt()
                    if (percent >= shown + 25 || last) {
                        shown = percent
                        listener.onEvent("Enviando $name: $percent%")
                    }
                }
            }
            val result = done.get(120, TimeUnit.SECONDS)
            if (result.optString("type") == "file_complete" && result.optBoolean("ok")) {
                listener.onEvent("Arquivo enviado: $name")
            } else {
                listener.onEvent(result.optString("message", "O computador não concluiu $name."))
            }
        } catch (_: TimeoutException) {
            listener.onEvent("O computador não respondeu ao arquivo $name.")
        } finally {
            gates.remove("$id:accept")
            gates.remove("$id:done")
        }
    }

    fun close() {
        closed = true
        try {
            transport?.close()
        } catch (_: Exception) {
        }
    }

    private fun startWatchdog() {
        Thread({
            while (!closed && !finished) {
                try {
                    Thread.sleep(2000)
                } catch (_: InterruptedException) {
                    return@Thread
                }
                val idle = (System.nanoTime() - lastRx) / 1_000_000
                if (idle > idleLimitMs && !closed) {
                    finish(idleMessage)
                    close()
                    return@Thread
                }
            }
        }, "connectlinux-watchdog").apply { isDaemon = true }.start()
    }

    private fun expect(limitMs: Long, message: String) {
        lastRx = System.nanoTime()
        idleLimitMs = limitMs
        idleMessage = message
    }

    private fun receive(input: InputStream): JSONObject {
        val message = readMessage(input)
        lastRx = System.nanoTime()
        return message
    }

    fun connectAndRun() {
        try {
            val opened = target.open(context)
            transport = opened
            if (closed) {
                opened.close()
                return
            }
            output = opened.output
            val input = opened.input
            expect(20_000, "O computador não respondeu.")
            startWatchdog()
            send(
                JSONObject()
                    .put("type", "hello")
                    .put("v", Protocol.VERSION)
                    .put("id", deviceId)
                    .put("name", Protocol.sanitizeName(deviceName))
                    .put("platform", "android"),
            )
            val ack = receive(input)
            if (ack.optString("type") == "error") {
                finish(ack.optString("message", "O computador recusou a conexão."))
                return
            }
            if (ack.optString("type") != "hello_ack" || ack.optInt("v") != Protocol.VERSION) {
                finish("Versão incompatível do Connect Linux.")
                return
            }
            val desktopId = ack.optString("id")
            val desktopName = Protocol.sanitizeName(ack.optString("name", "Computador"))
            if (!Protocol.isDeviceId(desktopId)) {
                finish("O computador enviou um identificador inválido.")
                return
            }
            val token = if (forcePair) null else tokenFor(desktopId)
            if (token.isNullOrBlank()) {
                send(JSONObject().put("type", "pair_request"))
            } else {
                send(JSONObject().put("type", "auth").put("token", token))
            }
            expect(120_000, "Tempo esgotado ao parear.")
            var ready = false
            while (!closed) {
                val message = receive(input)
                val type = message.optString("type")
                if (type == "ping") {
                    val ts = message.optLong("ts", System.currentTimeMillis())
                    send(JSONObject().put("type", "pong").put("ts", ts))
                    continue
                }
                if (!ready) {
                    when (type) {
                        "auth_ok" -> {
                            ready = true
                            expect(50_000, "O computador parou de responder.")
                            listener.onReady(desktopId, desktopName, token)
                        }
                        "auth_fail" -> {
                            listener.onAuthFailed(desktopId, message.optString("message", "Pareie novamente."))
                            finish(message.optString("message", "Pareie novamente."))
                            return
                        }
                        "pair_required" -> listener.onPairRequired(message.optString("name", desktopName))
                        "pair_ok" -> {
                            ready = true
                            expect(50_000, "O computador parou de responder.")
                            listener.onReady(desktopId, desktopName, message.optString("token"))
                        }
                        "pair_fail" -> listener.onPairFailed(message.optString("message", "Código incorreto."))
                        "pair_rejected" -> {
                            listener.onPairRejected(message.optString("message", "Pareamento recusado."))
                            finish(message.optString("message", "Pareamento recusado."))
                            return
                        }
                        "error" -> {
                            finish(message.optString("message", "Erro na conexão."))
                            return
                        }
                    }
                    continue
                }
                dispatch(message)
            }
            finish("Desconectado.")
        } catch (_: EOFException) {
            finish("O computador encerrou a conexão.")
        } catch (exc: Exception) {
            if (!closed) finish(friendlyError(exc))
            else finish("Desconectado.")
        } finally {
            abortIncoming()
            failGates()
            close()
        }
    }

    private fun dispatch(message: JSONObject) {
        when (message.optString("type")) {
            "pong" -> {
                val ts = message.optLong("ts", -1)
                if (ts == lastPingTs && ts > 0) {
                    val delay = System.currentTimeMillis() - ts
                    if (delay in 0..60_000) listener.onLatency(delay.toInt())
                }
            }
            "clipboard" -> {
                val text = message.optString("text")
                if (text.isNotEmpty() && text.length <= Protocol.MAX_CLIPBOARD) listener.onClipboard(text)
            }
            "text" -> listener.onText(message.optString("text"))
            "open_url" -> listener.onOpenUrl(message.optString("url"))
            "find" -> listener.onFind()
            "battery" -> {
                val level = message.optInt("level", -1)
                if (level in 0..100) listener.onBattery(level, message.optBoolean("charging"))
            }
            "unpair" -> {
                listener.onUnpair()
                close()
            }
            "photos_list" -> listener.onPhotosRequest(
                message.optInt("offset", 0).coerceAtLeast(0),
                message.optInt("limit", 30),
            )
            "photo_get" -> message.optString("id").toLongOrNull()?.let { listener.onPhotoRequest(it) }
            "notification" -> listener.onText(message.optString("title") + "\n" + message.optString("body"))
            "file_meta" -> handleMeta(message)
            "file_chunk" -> handleChunk(message)
            "file_accept", "file_reject", "file_complete", "file_error" -> openGate(message)
        }
    }

    private fun handleMeta(message: JSONObject) {
        val id = message.optString("id")
        val name = Protocol.sanitizeFilename(message.optString("name"))
        val size = message.optLong("size", -1)
        val sha = message.optString("sha256").lowercase()
        val mime = message.optString("mime").ifBlank { "application/octet-stream" }.take(100)
        if (incoming != null) {
            send(JSONObject().put("type", "file_reject").put("id", id).put("message", "Já existe um arquivo em recebimento."))
            return
        }
        if (id.isBlank() || size < 0 || size > Protocol.MAX_FILE || sha.length != 64) {
            send(JSONObject().put("type", "file_reject").put("id", id).put("message", "Arquivo recusado."))
            return
        }
        val sink = try {
            listener.openDownload(name, mime)
        } catch (_: Exception) {
            send(JSONObject().put("type", "file_reject").put("id", id).put("message", "Não foi possível criar o arquivo."))
            return
        }
        incoming = Incoming(id, name, size, sha, sink, MessageDigest.getInstance("SHA-256"), mime, message.optBoolean("open"))
        send(JSONObject().put("type", "file_accept").put("id", id))
        listener.onEvent("Recebendo $name…")
    }

    private fun handleChunk(message: JSONObject) {
        val current = incoming ?: return
        val id = message.optString("id")
        if (id != current.id) return
        val sequence = message.optInt("seq", -1)
        val raw = try {
            Base64.decode(message.optString("data"), Base64.DEFAULT)
        } catch (_: IllegalArgumentException) {
            failIncoming("Trecho do arquivo inválido.")
            return
        }
        if (sequence != current.seq || raw.size > Protocol.MAX_CHUNK) {
            failIncoming("Ordem do arquivo inválida.")
            return
        }
        val last = message.optBoolean("last")
        if (current.got + raw.size > current.size) {
            failIncoming("Arquivo maior do que o anunciado.")
            return
        }
        current.got += raw.size
        if ((last && current.got != current.size) || (!last && current.got == current.size)) {
            failIncoming("Arquivo incompleto.")
            return
        }
        try {
            current.sink.write(raw)
        } catch (_: Exception) {
            failIncoming("Não foi possível gravar o arquivo.")
            return
        }
        current.digest.update(raw)
        current.seq += 1
        val percent = if (current.size == 0L) 100 else ((current.got * 100) / current.size).toInt()
        if (percent >= current.shown + 25 || last) {
            current.shown = percent
            listener.onEvent("Recebendo ${current.name}: $percent%")
        }
        if (!last) return
        val actual = current.digest.digest().toHex()
        if (!actual.equals(current.sha, ignoreCase = true)) {
            current.sink.abort()
            incoming = null
            send(JSONObject().put("type", "file_error").put("id", id).put("message", "O arquivo chegou corrompido."))
            listener.onEvent("Arquivo corrompido: ${current.name}")
            return
        }
        val label = current.sink.label
        current.sink.finish()
        incoming = null
        send(JSONObject().put("type", "file_complete").put("id", id).put("ok", true).put("sha256", actual))
        listener.onEvent("Arquivo salvo: ${current.name} ($label)")
        listener.onFileSaved(current.name, current.mime, current.sink.uri, current.open)
    }

    private fun failIncoming(reason: String) {
        val current = incoming ?: return
        incoming = null
        try {
            current.sink.abort()
        } catch (_: Exception) {
        }
        send(JSONObject().put("type", "file_error").put("id", current.id).put("message", reason))
        listener.onEvent("Falha ao receber ${current.name}: $reason")
    }

    private fun failGates() {
        val error = JSONObject().put("type", "file_error").put("message", "Conexão encerrada.")
        gates.forEach { (_, future) -> future.complete(error) }
        gates.clear()
    }

    private fun abortIncoming() {
        val current = incoming ?: return
        incoming = null
        try {
            current.sink.abort()
        } catch (_: Exception) {
        }
    }

    private fun openGate(message: JSONObject) {
        val id = message.optString("id")
        val suffix = when (message.optString("type")) {
            "file_accept", "file_reject" -> "accept"
            else -> "done"
        }
        gates.remove("$id:$suffix")?.complete(message)
    }

    private fun chunkMessage(id: String, sequence: Int, last: Boolean, raw: ByteArray): JSONObject {
        return JSONObject()
            .put("type", "file_chunk")
            .put("id", id)
            .put("seq", sequence)
            .put("last", last)
            .put("data", Base64.encodeToString(raw, Base64.NO_WRAP))
    }

    private fun send(message: JSONObject) {
        val bytes = encode(message)
        synchronized(writeLock) {
            val out = output ?: error("sem conexão")
            out.write(bytes)
            out.flush()
        }
    }

    private fun friendlyError(exc: Exception): String {
        if (target is Target.Bluetooth) return exc.message ?: "Conexão Bluetooth encerrada."
        val text = exc.message.orEmpty()
        return when {
            target is Target.Network && target.host == Target.USB_HOST ->
                "Sem conexão pelo cabo USB. Ligue a depuração USB, conecte o cabo e deixe o Connect Linux aberto no computador."
            text.contains("ECONNREFUSED") || text.contains("refused", ignoreCase = true) ->
                "O computador recusou a conexão. Confira se o Connect Linux está aberto nele."
            text.contains("EHOSTUNREACH") || text.contains("ENETUNREACH") || text.contains("timed out", ignoreCase = true) || text.contains("timeout", ignoreCase = true) ->
                "O computador não está acessível por esta rede. Tente Bluetooth, cabo USB ou Tailscale."
            else -> text.ifBlank { "Conexão encerrada." }
        }
    }

    private fun finish(reason: String) {
        if (finished) return
        finished = true
        listener.onClosed(reason)
    }

    private class Incoming(
        val id: String,
        val name: String,
        val size: Long,
        val sha: String,
        val sink: FileSink,
        val digest: MessageDigest,
        val mime: String,
        val open: Boolean,
        var got: Long = 0,
        var seq: Int = 0,
        var shown: Int = -10,
    )
}

private fun encode(message: JSONObject): ByteArray {
    val payload = message.toString().toByteArray(Charsets.UTF_8)
    if (payload.size > Protocol.MAX_FRAME) error("mensagem grande demais")
    val frame = ByteArray(4 + payload.size)
    val length = payload.size
    frame[0] = (length ushr 24).toByte()
    frame[1] = (length ushr 16).toByte()
    frame[2] = (length ushr 8).toByte()
    frame[3] = length.toByte()
    System.arraycopy(payload, 0, frame, 4, payload.size)
    return frame
}

private fun readMessage(input: InputStream): JSONObject {
    val header = readExact(input, 4)
    val length = ((header[0].toInt() and 0xFF) shl 24) or
        ((header[1].toInt() and 0xFF) shl 16) or
        ((header[2].toInt() and 0xFF) shl 8) or
        (header[3].toInt() and 0xFF)
    if (length <= 0 || length > Protocol.MAX_FRAME) error("tamanho inválido")
    val payload = readExact(input, length)
    val message = JSONObject(String(payload, Charsets.UTF_8))
    if (!message.has("type")) error("mensagem inválida")
    return message
}

private fun readExact(input: InputStream, size: Int): ByteArray {
    val buffer = ByteArray(size)
    var offset = 0
    while (offset < size) {
        val read = input.read(buffer, offset, size - offset)
        if (read < 0) throw EOFException()
        offset += read
    }
    return buffer
}
