package dev.connectlinux.app

import android.app.Activity
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.ImageDecoder
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.TextView
import kotlin.concurrent.thread

/** Mostra em tela cheia a imagem que o computador mandou abrir. Toque para fechar. */
class ImageViewerActivity : Activity() {
    private lateinit var image: ImageView
    private lateinit var caption: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        image = ImageView(this).apply {
            scaleType = ImageView.ScaleType.FIT_CENTER
            setBackgroundColor(Color.BLACK)
        }
        caption = TextView(this).apply {
            setTextColor(Color.WHITE)
            setShadowLayer(4f, 0f, 0f, Color.BLACK)
            textSize = 14f
            setPadding(32, 32, 32, 48)
        }
        val root = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(image, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
            addView(
                caption,
                FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM),
            )
            setOnClickListener { finish() }
        }
        setContentView(root)
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
        show(intent)
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        show(intent)
    }

    private fun show(intent: android.content.Intent) {
        val uri = intent.data ?: return finish()
        val name = intent.getStringExtra(EXTRA_NAME).orEmpty()
        val from = intent.getStringExtra(EXTRA_FROM).orEmpty()
        caption.text = listOf(name, if (from.isBlank()) "" else "enviada por $from · salva em Downloads")
            .filter { it.isNotBlank() }
            .joinToString("\n")
        val metrics = resources.displayMetrics
        val limit = maxOf(metrics.widthPixels, metrics.heightPixels).coerceAtLeast(1080)
        thread(name = "connectlinux-image") {
            val bitmap = try {
                decode(uri, limit)
            } catch (_: Exception) {
                null
            }
            runOnUiThread {
                if (isFinishing) return@runOnUiThread
                if (bitmap != null) {
                    image.setImageBitmap(bitmap)
                } else {
                    caption.text = "Não foi possível mostrar $name. Ela está em Downloads."
                }
            }
        }
    }

    private fun decode(uri: Uri, limit: Int): Bitmap? {
        if (Build.VERSION.SDK_INT >= 28) {
            val source = ImageDecoder.createSource(contentResolver, uri)
            return ImageDecoder.decodeBitmap(source) { decoder, info, _ ->
                val largest = maxOf(info.size.width, info.size.height)
                if (largest > limit) {
                    val ratio = limit.toFloat() / largest
                    decoder.setTargetSize((info.size.width * ratio).toInt().coerceAtLeast(1), (info.size.height * ratio).toInt().coerceAtLeast(1))
                }
            }
        }
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= limit) sample *= 2
        val options = BitmapFactory.Options().apply { inSampleSize = sample }
        return contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, options) }
    }

    companion object {
        const val EXTRA_NAME = "dev.connectlinux.app.extra.NAME"
        const val EXTRA_FROM = "dev.connectlinux.app.extra.FROM"
    }
}
