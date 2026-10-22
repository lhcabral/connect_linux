package dev.connectlinux.app

import android.net.Uri
import java.io.File
import java.io.InputStream
import java.security.MessageDigest

/**
 * Constantes iguais às de desktop/connectlinux/protocol.py.
 * Se mudar um número aqui, mude lá também.
 */
object Protocol {
    const val APP = "connectlinux"
    const val VERSION = 1
    const val DISCOVERY_PORT = 17170
    const val LINK_PORT = 17171
    const val MULTICAST = "239.255.171.70"
    const val BLUETOOTH_UUID = "6f1c0a4e-6b9d-4c55-9a8e-2b1d7c1e5a71"
    const val MAX_FRAME = 8 * 1024 * 1024
    const val MAX_FILE = 100L * 1024 * 1024
    const val CHUNK = 32 * 1024
    const val MAX_CHUNK = 256 * 1024
    const val MAX_CLIPBOARD = 250_000

    private val deviceIdPattern = Regex(
        "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    private val invalidFileChars = Regex("[^0-9A-Za-z\\u00C0-\\u00FF._ ()-]+")

    fun isDeviceId(value: String): Boolean = deviceIdPattern.matches(value)

    fun sanitizeName(raw: String?): String {
        val cleaned = (raw ?: "")
            .filter { !it.isISOControl() }
            .replace(Regex("\\s+"), " ")
            .trim()
            .take(60)
        return cleaned.ifEmpty { "Aparelho" }
    }

    fun sanitizeFilename(raw: String?): String {
        val base = (raw ?: "")
            .replace('\\', '/')
            .substringAfterLast('/')
            .replace("\u0000", "")
        val cleaned = invalidFileChars.replace(base, "_").trim(' ', '.')
        if (cleaned.isEmpty() || cleaned == "." || cleaned == "..") return "arquivo"
        return cleaned.take(120)
    }

    fun isHttpUrl(url: String): Boolean {
        val text = url.trim()
        if (text.isEmpty() || text.any { it.isWhitespace() || it == '\u0000' }) return false
        val parsed = Uri.parse(text)
        val scheme = parsed.scheme?.lowercase()
        return (scheme == "http" || scheme == "https") && !parsed.host.isNullOrBlank()
    }

    fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input -> digest.updateFrom(input) }
        return digest.digest().toHex()
    }

    private fun MessageDigest.updateFrom(input: InputStream) {
        val buffer = ByteArray(1024 * 1024)
        while (true) {
            val read = input.read(buffer)
            if (read < 0) break
            update(buffer, 0, read)
        }
    }
}

fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }
