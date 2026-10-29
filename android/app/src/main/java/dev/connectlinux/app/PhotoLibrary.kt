package dev.connectlinux.app

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
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

    val permission: String =
        if (Build.VERSION.SDK_INT >= 33) Manifest.permission.READ_MEDIA_IMAGES else Manifest.permission.READ_EXTERNAL_STORAGE

    fun allowed(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED

    fun uriFor(id: Long): Uri = ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id)

    class Page(val items: JSONArray, val next: Int, val more: Boolean)

    fun recent(context: Context, offset: Int, limit: Int): Page {
        val items = JSONArray()
        val wanted = limit.coerceIn(1, MAX_PAGE)
        val projection = arrayOf(
            MediaStore.Images.Media._ID,
            MediaStore.Images.Media.DISPLAY_NAME,
            MediaStore.Images.Media.SIZE,
            MediaStore.Images.Media.DATE_TAKEN,
            MediaStore.Images.Media.DATE_ADDED,
        )
        val order = "${MediaStore.Images.Media.DATE_ADDED} DESC"
        var more = false
        var next = offset
        context.contentResolver.query(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, projection, null, null, order)?.use { cursor ->
            if (offset > 0 && !cursor.moveToPosition(offset - 1)) return Page(items, offset, false)
            val idCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media._ID)
            val nameCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.DISPLAY_NAME)
            val sizeCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.SIZE)
            val takenCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.DATE_TAKEN)
            val addedCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.DATE_ADDED)
            while (items.length() < wanted && cursor.moveToNext()) {
                val id = cursor.getLong(idCol)
                val thumb = thumbnail(context, id) ?: continue
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
            more = next < cursor.count
        }
        return Page(items, next, more)
    }

    private fun thumbnail(context: Context, id: Long): String? {
        val bitmap = try {
            if (Build.VERSION.SDK_INT >= 29) {
                context.contentResolver.loadThumbnail(uriFor(id), Size(THUMB_PX, THUMB_PX), null)
            } else {
                @Suppress("DEPRECATION")
                MediaStore.Images.Thumbnails.getThumbnail(
                    context.contentResolver,
                    id,
                    MediaStore.Images.Thumbnails.MINI_KIND,
                    null,
                )
            }
        } catch (_: Exception) {
            null
        } ?: return null
        val out = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 72, out)
        bitmap.recycle()
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }
}
