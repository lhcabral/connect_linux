package dev.connectlinux.app

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import java.io.File
import java.io.OutputStream

interface FileSink {
    val label: String
    val uri: Uri
    fun write(bytes: ByteArray)
    fun finish()
    fun abort()
}

class IncomingFiles(private val context: Context) {
    /** Com [gallery], a imagem vai para o álbum "Connect Linux" da galeria em vez de Downloads. */
    fun open(name: String, mime: String, gallery: Boolean = false): FileSink {
        val safe = Protocol.sanitizeFilename(name)
        val type = mime.ifBlank { "application/octet-stream" }.take(100)
        val image = type.startsWith("image/") || Protocol.isImageName(safe)
        return when {
            Build.VERSION.SDK_INT < 29 -> openLegacy(safe)
            gallery && image -> openMedia(safe, type, MediaStore.Images.Media.EXTERNAL_CONTENT_URI, GALLERY_FOLDER)
            else -> openMedia(safe, type, MediaStore.Downloads.EXTERNAL_CONTENT_URI, Environment.DIRECTORY_DOWNLOADS)
        }
    }

    private fun openMedia(name: String, mime: String, collection: Uri, folder: String): FileSink {
        val resolver = context.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, name)
            put(MediaStore.MediaColumns.MIME_TYPE, mime)
            put(MediaStore.MediaColumns.RELATIVE_PATH, folder)
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri = resolver.insert(collection, values)
            ?: error("não foi possível criar o arquivo")
        val stream = resolver.openOutputStream(uri) ?: error("não foi possível gravar o arquivo")
        return object : FileSink {
            override val label: String = "$folder/$name"
            override val uri: Uri = uri
            override fun write(bytes: ByteArray) {
                stream.write(bytes)
            }
            override fun finish() {
                stream.flush()
                stream.close()
                val done = ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }
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
            override val uri: Uri = Uri.fromFile(target)
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

    private companion object {
        val GALLERY_FOLDER = "${Environment.DIRECTORY_PICTURES}/Connect Linux"
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
