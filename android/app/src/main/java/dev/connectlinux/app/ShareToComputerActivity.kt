package dev.connectlinux.app

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.core.content.ContextCompat
import kotlin.concurrent.thread

/**
 * Alvo "Abrir no computador" do menu Compartilhar: copia as imagens, entrega ao
 * serviço e fecha. O computador abre a imagem assim que ela chega.
 */
class ShareToComputerActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val uris = sharedUris(intent)
        if (uris.isEmpty()) {
            Toast.makeText(this, "Nada para enviar.", Toast.LENGTH_SHORT).show()
            finish()
            return
        }
        Toast.makeText(this, "Enviando para o computador…", Toast.LENGTH_SHORT).show()
        val appContext = applicationContext
        thread(name = "connectlinux-share") {
            val copied = mutableListOf<CachedFile>()
            var problem: String? = null
            for (uri in uris.take(MAX_ITEMS)) {
                try {
                    copied += Outbox.copy(this, uri)
                } catch (exc: Exception) {
                    problem = exc.message ?: "Não foi possível ler a imagem."
                }
            }
            runOnUiThread {
                if (copied.isNotEmpty()) {
                    val service = Intent(appContext, ConnectService::class.java)
                        .setAction(ConnectService.ACTION_SEND_FILES)
                        .putExtra(ConnectService.EXTRA_PATHS, copied.map { it.file.absolutePath }.toTypedArray())
                        .putExtra(ConnectService.EXTRA_NAMES, copied.map { it.name }.toTypedArray())
                        .putExtra(ConnectService.EXTRA_MIMES, copied.map { it.mime }.toTypedArray())
                        .putExtra(ConnectService.EXTRA_OPEN, true)
                    ContextCompat.startForegroundService(appContext, service)
                }
                problem?.let { Toast.makeText(appContext, it, Toast.LENGTH_LONG).show() }
                finish()
            }
        }
    }

    private fun sharedUris(intent: Intent?): List<Uri> {
        intent ?: return emptyList()
        return when (intent.action) {
            Intent.ACTION_SEND -> listOfNotNull(streamExtra(intent))
            Intent.ACTION_SEND_MULTIPLE -> streamList(intent)
            else -> emptyList()
        }
    }

    private fun streamExtra(intent: Intent): Uri? =
        if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(Intent.EXTRA_STREAM)
        }

    private fun streamList(intent: Intent): List<Uri> =
        if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java).orEmpty()
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM).orEmpty()
        }

    companion object {
        private const val MAX_ITEMS = 20
    }
}
