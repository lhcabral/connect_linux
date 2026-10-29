package dev.connectlinux.app

import android.graphics.Color
import android.graphics.Rect
import android.view.View
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding

/** Desenha atrás das barras em todas as versões, para o teclado ser tratado só por [applySystemInsets]. */
fun ComponentActivity.drawBehindSystemBars() {
    enableEdgeToEdge(
        statusBarStyle = SystemBarStyle.dark(Color.TRANSPARENT),
        navigationBarStyle = SystemBarStyle.light(Color.TRANSPARENT, Color.TRANSPARENT),
    )
}

/**
 * Afasta a barra de título da barra de status e o conteúdo do teclado (Android 15 desenha atrás das barras).
 * A barra de abas some com o teclado aberto: ela soma a altura do teclado ao próprio espaçamento.
 */
fun applySystemInsets(root: View, header: View, bottomNav: View? = null) {
    val headerTop = header.paddingTop
    ViewCompat.setOnApplyWindowInsetsListener(root) { _, insets ->
        val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
        val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
        val imeVisible = insets.isVisible(WindowInsetsCompat.Type.ime()) && ime.bottom > 0
        header.updatePadding(top = headerTop + bars.top)
        if (bottomNav != null) {
            bottomNav.visibility = if (imeVisible) View.GONE else View.VISIBLE
            root.updatePadding(bottom = if (imeVisible) ime.bottom else 0)
        } else {
            root.updatePadding(bottom = maxOf(ime.bottom, bars.bottom))
        }
        if (imeVisible) {
            root.findFocus()?.let { focused ->
                focused.post { focused.requestRectangleOnScreen(Rect(0, 0, focused.width, focused.height), false) }
            }
        }
        insets
    }
}
