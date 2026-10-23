package dev.connectlinux.app

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import java.io.File
import java.util.UUID

data class CachedFile(val file: File, val name: String, val mime: String)

/** Copia o que outro app compartilhou para o cache, enquanto a permissão de leitura vale. */
object Outbox {
    class TooLarge : Exception("Arquivo maior que 100 MB.")

    fun copy(context: Context, uri: Uri): CachedFile {
        val resolver = context.contentResolver
        var name = "arquivo"
        var declared = -1L
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) {
                val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
                if (nameIndex >= 0) name = cursor.getString(nameIndex) ?: name
                if (sizeIndex >= 0 && !cursor.isNull(sizeIndex)) declared = cursor.getLong(sizeIndex)
            }
        }
        if (declared > Protocol.MAX_FILE) throw TooLarge()
        val folder = File(context.cacheDir, "outbox").apply { mkdirs() }
        val dest = File(folder, UUID.randomUUID().toString())
        val input = resolver.openInputStream(uri) ?: error("Não foi possível ler o arquivo.")
        input.use { source ->
            dest.outputStream().use { output ->
                val buffer = ByteArray(64 * 1024)
                var total = 0L
                while (true) {
                    val read = source.read(buffer)
                    if (read < 0) break
                    total += read
                    if (total > Protocol.MAX_FILE) {
                        output.close()
                        dest.delete()
                        throw TooLarge()
                    }
                    output.write(buffer, 0, read)
                }
            }
        }
        return CachedFile(dest, name, resolver.getType(uri) ?: "application/octet-stream")
    }
}
