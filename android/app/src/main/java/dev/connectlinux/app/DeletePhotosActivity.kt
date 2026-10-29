package dev.connectlinux.app

import android.app.Activity
import android.app.NotificationManager
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

/** Mostra a confirmação do Android para excluir as fotos que o computador pediu. */
class DeletePhotosActivity : AppCompatActivity() {
    private var ids: List<Long> = emptyList()
    private var answered = false

    private val confirm = registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { result ->
        finishWith(result.resultCode == Activity.RESULT_OK)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        getSystemService(NotificationManager::class.java)?.cancel(ConnectService.DELETE_NOTIFICATION)
        ids = intent.getLongArrayExtra(EXTRA_IDS)?.toList().orEmpty()
        if (ids.isEmpty() || Build.VERSION.SDK_INT < 30) {
            finish()
            return
        }
        if (savedInstanceState != null) return
        try {
            val request = MediaStore.createDeleteRequest(contentResolver, ids.map { PhotoLibrary.uriFor(it) })
            confirm.launch(IntentSenderRequest.Builder(request.intentSender).build())
        } catch (_: Exception) {
            finishWith(false)
        }
    }

    private fun finishWith(ok: Boolean) {
        if (answered) return
        answered = true
        ConnectService.instance?.finishPhotoDelete(ids, ok)
        finish()
    }

    companion object {
        const val EXTRA_IDS = "dev.connectlinux.app.extra.PHOTO_IDS"
    }
}
