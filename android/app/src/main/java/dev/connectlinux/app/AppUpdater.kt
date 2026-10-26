package dev.connectlinux.app

import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

data class AppUpdate(
    val version: String,
    val apkUrl: String,
    val sumsUrl: String?,
)

object AppUpdater {
    private const val LATEST_URL = "https://api.github.com/repos/lhcabral/connect_linux/releases/latest"
    private const val APK_ASSET = "ConnectLinux.apk"
    private const val SUMS_ASSET = "SHA256SUMS.txt"
    private const val PREFS = "connectlinux_update"
    private const val KEY_DISMISSED = "dismissed_version"

    fun currentVersion(context: Context): String {
        val info = if (Build.VERSION.SDK_INT >= 33) {
            context.packageManager.getPackageInfo(context.packageName, android.content.pm.PackageManager.PackageInfoFlags.of(0))
        } else {
            @Suppress("DEPRECATION")
            context.packageManager.getPackageInfo(context.packageName, 0)
        }
        return info.versionName.orEmpty()
    }

    fun isNewer(remote: String, local: String): Boolean {
        val a = parseVersion(remote)
        val b = parseVersion(local)
        for (i in 0 until maxOf(a.size, b.size)) {
            val diff = a.getOrElse(i) { 0 } - b.getOrElse(i) { 0 }
            if (diff != 0) return diff > 0
        }
        return false
    }

    /** Devolve a versão nova ou null se o app já está em dia. Faz acesso à rede. */
    fun check(context: Context): AppUpdate? {
        val release = JSONObject(get(LATEST_URL, context).decodeToString())
        val version = release.optString("tag_name").trimStart('v', 'V')
        if (version.isBlank() || !isNewer(version, currentVersion(context))) return null
        val assets = release.optJSONArray("assets") ?: return null
        var apk: String? = null
        var sums: String? = null
        for (i in 0 until assets.length()) {
            val item = assets.optJSONObject(i) ?: continue
            when (item.optString("name")) {
                APK_ASSET -> apk = item.optString("browser_download_url")
                SUMS_ASSET -> sums = item.optString("browser_download_url")
            }
        }
        return apk?.takeIf { it.isNotBlank() }?.let { AppUpdate(version, it, sums) }
    }

    /** Baixa o APK, confere a soma SHA-256 e devolve o arquivo. Faz acesso à rede. */
    fun download(context: Context, update: AppUpdate, progress: (Int) -> Unit): File {
        val folder = File(context.cacheDir, "updates").apply { mkdirs() }
        folder.listFiles()?.forEach { it.delete() }
        val target = File(folder, "ConnectLinux-${update.version}.apk")
        val digest = MessageDigest.getInstance("SHA-256")
        val connection = open(update.apkUrl, context)
        try {
            val total = connection.contentLengthLong
            connection.inputStream.use { input ->
                target.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    var done = 0L
                    var last = -1
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        digest.update(buffer, 0, read)
                        done += read
                        if (total > 0) {
                            val percent = (done * 100 / total).toInt()
                            if (percent != last) {
                                last = percent
                                progress(percent)
                            }
                        }
                    }
                }
            }
        } finally {
            connection.disconnect()
        }
        val expected = update.sumsUrl?.let { expectedSum(it, context) }
        val actual = digest.digest().joinToString("") { "%02x".format(it) }
        if (expected != null && expected != actual) {
            target.delete()
            throw IllegalStateException("o arquivo baixado está corrompido; tente de novo")
        }
        return target
    }

    fun installIntent(context: Context, apk: File): Intent {
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", apk)
        return Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    fun wasDismissed(context: Context, version: String): Boolean =
        prefs(context).getString(KEY_DISMISSED, null) == version

    fun dismiss(context: Context, version: String) {
        prefs(context).edit().putString(KEY_DISMISSED, version).apply()
    }

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun parseVersion(text: String): List<Int> =
        Regex("\\d+").findAll(text).take(4).map { it.value.toInt() }.toList().ifEmpty { listOf(0) }

    private fun expectedSum(url: String, context: Context): String? {
        val text = get(url, context).decodeToString()
        for (line in text.lines()) {
            val parts = line.trim().split(Regex("\\s+"))
            if (parts.size >= 2 && parts.last().trimStart('*') == APK_ASSET) return parts[0].lowercase()
        }
        return null
    }

    private fun get(url: String, context: Context): ByteArray {
        val connection = open(url, context)
        try {
            return connection.inputStream.use { it.readBytes() }
        } finally {
            connection.disconnect()
        }
    }

    private fun open(url: String, context: Context): HttpURLConnection {
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.connectTimeout = 15_000
        connection.readTimeout = 30_000
        connection.instanceFollowRedirects = true
        connection.setRequestProperty("Accept", "application/vnd.github+json")
        connection.setRequestProperty("User-Agent", "connectlinux-android/${currentVersion(context)}")
        if (connection.responseCode !in 200..299) {
            val code = connection.responseCode
            connection.disconnect()
            throw IllegalStateException("o servidor respondeu $code")
        }
        return connection
    }
}
