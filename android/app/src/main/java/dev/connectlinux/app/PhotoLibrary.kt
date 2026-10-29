package dev.connectlinux.app

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.util.Base64
import android.util.Size
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream

/** Fotos recentes da galeria, para o computador mostrar e pedir a foto inteira. */
object PhotoLibrary {
    private const val THUMB_PX = 320
    private const val MAX_PAGE = 60
    private const val USER_SELECTED = "android.permission.READ_MEDIA_VISUAL_USER_SELECTED"

    enum class Access { FULL, PARTIAL, NONE }

    class Folder(val id: String, val name: String, val count: Int)

    class Page(val items: JSONArray, val next: Int, val more: Boolean, val total: Int, val skipped: Int)

    /** Permissões a pedir de uma vez. No Android 14 ou mais novo, a pessoa pode liberar só algumas fotos. */
    val permissions: Array<String> = when {
        Build.VERSION.SDK_INT >= 34 -> arrayOf(Manifest.permission.READ_MEDIA_IMAGES, USER_SELECTED)
        Build.VERSION.SDK_INT >= 33 -> arrayOf(Manifest.permission.READ_MEDIA_IMAGES)
        else -> arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE)
    }

    val mainPermission: String = permissions.first()

    fun access(context: Context): Access {
        fun granted(name: String) = ContextCompat.checkSelfPermission(context, name) == PackageManager.PERMISSION_GRANTED
        return when {
            granted(mainPermission) -> Access.FULL
            Build.VERSION.SDK_INT >= 34 && granted(USER_SELECTED) -> Access.PARTIAL
            else -> Access.NONE
        }
    }

    fun allowed(context: Context): Boolean = access(context) != Access.NONE

    fun uriFor(id: Long): Uri = ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id)

    /** Pastas da galeria que têm fotos, das com mais fotos para as com menos. */
    fun folders(context: Context): List<Folder> {
        val counts = linkedMapOf<String, Pair<String, Int>>()
        val projection = arrayOf(MediaStore.Images.Media.BUCKET_ID, MediaStore.Images.Media.BUCKET_DISPLAY_NAME)
        context.contentResolver.query(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, projection, null, null, null)?.use { cursor ->
            val idCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.BUCKET_ID)
            val nameCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.BUCKET_DISPLAY_NAME)
            while (cursor.moveToNext()) {
                val id = cursor.getString(idCol) ?: continue
                val name = cursor.getString(nameCol)?.takeIf { it.isNotBlank() } ?: "Sem nome"
                val current = counts[id]
                counts[id] = name to ((current?.second ?: 0) + 1)
            }
        }
        return counts.map { (id, value) -> Folder(id, value.first, value.second) }.sortedByDescending { it.count }
    }

    fun recent(context: Context, offset: Int, limit: Int, folderId: String): Page {
        val items = JSONArray()
        val wanted = limit.coerceIn(1, MAX_PAGE)
        val projection = arrayOf(
            MediaStore.Images.Media._ID,
            MediaStore.Images.Media.DISPLAY_NAME,
            MediaStore.Images.Media.SIZE,
            MediaStore.Images.Media.DATE_TAKEN,
            MediaStore.Images.Media.DATE_ADDED,
        )
        val selection = if (folderId.isNotEmpty()) "${MediaStore.Images.Media.BUCKET_ID} = ?" else null
        val args = if (folderId.isNotEmpty()) arrayOf(folderId) else null
        val order = "${MediaStore.Images.Media.DATE_ADDED} DESC"
        var more = false
        var next = offset
        var total = 0
        var skipped = 0
        context.contentResolver.query(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, projection, selection, args, order)?.use { cursor ->
            total = cursor.count
            if (offset > 0 && !cursor.moveToPosition(offset - 1)) return Page(items, offset, false, total, 0)
            val idCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media._ID)
            val nameCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.DISPLAY_NAME)
            val sizeCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.SIZE)
            val takenCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.DATE_TAKEN)
            val addedCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.DATE_ADDED)
            while (items.length() < wanted && cursor.moveToNext()) {
                val id = cursor.getLong(idCol)
                val thumb = thumbnail(context, id)
                if (thumb == null) {
                    skipped += 1
                    continue
                }
                val taken = cursor.getLong(takenCol).takeIf { it > 0 } ?: (cursor.getLong(addedCol) * 1000)
                items.put(
                    JSONObject()
                        .put("id", id.toString())
                        .put("name", Protocol.sanitizeFilename(cursor.getString(nameCol) ?: "foto_$id.jpg"))
                        .put("size", cursor.getLong(sizeCol))
                        .put("taken", taken)
                        .put("thumb", thumb),
                )
            }
            next = cursor.position + 1
            more = next < total
        }
        return Page(items, next, more, total, skipped)
    }

    private fun thumbnail(context: Context, id: Long): String? {
        val bitmap = systemThumbnail(context, id) ?: decodeSmall(context, id) ?: return null
        val out = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 72, out)
        bitmap.recycle()
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }

    private fun systemThumbnail(context: Context, id: Long): Bitmap? = try {
        if (Build.VERSION.SDK_INT >= 29) {
            context.contentResolver.loadThumbnail(uriFor(id), Size(THUMB_PX, THUMB_PX), null)
        } else {
            @Suppress("DEPRECATION")
            MediaStore.Images.Thumbnails.getThumbnail(context.contentResolver, id, MediaStore.Images.Thumbnails.MINI_KIND, null)
        }
    } catch (_: Exception) {
        null
    }

    /** Quando o sistema não entrega a miniatura, reduz a própria foto. */
    private fun decodeSmall(context: Context, id: Long): Bitmap? = try {
        val resolver = context.contentResolver
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        resolver.openInputStream(uriFor(id))?.use { BitmapFactory.decodeStream(it, null, bounds) }
        var sample = 1
        while (bounds.outWidth / (sample * 2) >= THUMB_PX && bounds.outHeight / (sample * 2) >= THUMB_PX) sample *= 2
        val options = BitmapFactory.Options().apply { inSampleSize = sample }
        resolver.openInputStream(uriFor(id))?.use { BitmapFactory.decodeStream(it, null, options) }
    } catch (_: Exception) {
        null
    }
}
