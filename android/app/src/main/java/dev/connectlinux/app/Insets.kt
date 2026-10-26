package dev.connectlinux.app

import android.view.View
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding

/** Afasta a barra de título da barra de status e o conteúdo do teclado (Android 15 desenha atrás das barras). */
fun applySystemInsets(root: View, header: View) {
    val headerTop = header.paddingTop
    ViewCompat.setOnApplyWindowInsetsListener(root) { _, insets ->
        val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
        val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
        header.updatePadding(top = headerTop + bars.top)
        root.updatePadding(bottom = ime.bottom)
        insets
    }
}
