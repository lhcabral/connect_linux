package dev.connectlinux.app

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import java.io.File
import java.io.OutputStream

interface FileSink {
    val label: String
    fun write(bytes: ByteArray)
    fun finish()
    fun abort()
}

class IncomingFiles(private val context: Context) {
    fun open(name: String, mime: String): FileSink {
        val safe = Protocol.sanitizeFilename(name)
        val type = mime.ifBlank { "application/octet-stream" }.take(100)
        return if (Build.VERSION.SDK_INT >= 29) openDownloads(safe, type) else openLegacy(safe)
    }

    private fun openDownloads(name: String, mime: String): FileSink {
        val resolver = context.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, mime)
            put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: error("não foi possível criar o arquivo")
        val stream = resolver.openOutputStream(uri) ?: error("não foi possível gravar o arquivo")
        return object : FileSink {
            override val label: String = "Downloads/$name"
            override fun write(bytes: ByteArray) {
                stream.write(bytes)
            }
            override fun finish() {
                stream.flush()
                stream.close()
                val done = ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }
                resolver.update(uri, done, null, null)
            }
            override fun abort() {
                try {
                    stream.close()
                } catch (_: Exception) {
                }
                resolver.delete(uri, null, null)
            }
        }
    }

    private fun openLegacy(name: String): FileSink {
        val directory = context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: context.filesDir
        directory.mkdirs()
        val target = uniqueFile(directory, name)
        val partial = File(directory, target.name + ".part")
        val stream: OutputStream = partial.outputStream()
        return object : FileSink {
            override val label: String = target.absolutePath
            override fun write(bytes: ByteArray) {
                stream.write(bytes)
            }
            override fun finish() {
                stream.flush()
                stream.close()
                if (target.exists()) target.delete()
                if (!partial.renameTo(target)) error("não foi possível concluir o arquivo")
            }
            override fun abort() {
                try {
                    stream.close()
                } catch (_: Exception) {
                }
                partial.delete()
            }
        }
    }

    private fun uniqueFile(directory: File, name: String): File {
        val clean = Protocol.sanitizeFilename(name)
        fun candidate(index: Int): File {
            val chosen = if (index == 0) {
                clean
            } else {
                val dot = clean.lastIndexOf('.')
                if (dot > 0) "${clean.substring(0, dot)} ($index)${clean.substring(dot)}"
                else "$clean ($index)"
            }
            return File(directory, chosen)
        }
        for (index in 0 until 1000) {
            val path = candidate(index)
            if (!path.exists()) return path
        }
        error("muitos arquivos com o mesmo nome")
    }
}
