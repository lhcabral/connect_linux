package dev.connectlinux.app

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.content.ClipData
import android.content.ClipboardManager
import android.content.ComponentName
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.provider.Settings
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import dev.connectlinux.app.databinding.ActivityMainBinding
import dev.connectlinux.app.databinding.ItemDeviceBinding

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private var service: ConnectService? = null
    private var deviceSignature = ""
    private var pendingText: String? = null
    private var pendingUri: Uri? = null
    private val listener: (UiState) -> Unit = { state -> render(state) }
    private var availableUpdate: AppUpdate? = null
    private var pendingUpdate: AppUpdate? = null
    private var checkingUpdate = false
    private var downloadingUpdate = false
    private var shown: Shown? = null

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            val connected = (binder as ConnectService.LocalBinder).service()
            service = connected
            connected.addListener(listener)
            render(connected.state())
            flushShare()
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            service?.removeListener(listener)
            service = null
        }
    }

    private val requestNotifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }
    private val requestBluetooth = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            chooseBluetoothDevice()
        } else {
            Toast.makeText(this, "Sem a permissão de Bluetooth não dá para conectar por Bluetooth.", Toast.LENGTH_LONG).show()
        }
    }
    private val pickFile = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) service?.sendFile(uri)
    }
    private val clipListener = ClipboardManager.OnPrimaryClipChangedListener {
        val text = clipboardText()
        if (text.isNotEmpty()) service?.onLocalClipboard(text)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        drawBehindSystemBars()
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        applySystemInsets(binding.root, binding.toolbarHost, binding.bottomNav)
        bindNavigation(savedInstanceState?.getInt(KEY_TAB) ?: R.id.navHome)
        bindActions()
        bindUpdates()
        handleShare(intent)
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        ContextCompat.startForegroundService(this, Intent(this, ConnectService::class.java))
        if (savedInstanceState == null) checkForUpdate(manual = false)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putInt(KEY_TAB, binding.bottomNav.selectedItemId)
    }

    private fun bindNavigation(selected: Int) {
        binding.bottomNav.setOnItemSelectedListener { item ->
            showPage(item.itemId)
            true
        }
        binding.bottomNav.selectedItemId = selected
        showPage(selected)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (binding.bottomNav.selectedItemId != R.id.navHome) {
                    binding.bottomNav.selectedItemId = R.id.navHome
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                    isEnabled = true
                }
            }
        })
    }

    private fun submitPairCode() {
        service?.submitCode(binding.pairCode.text?.toString().orEmpty())
        binding.pairCode.clearFocus()
        WindowCompat.getInsetsController(window, binding.pairCode).hide(WindowInsetsCompat.Type.ime())
    }

    private fun showPage(itemId: Int) {
        binding.pageHome.visibility = if (itemId == R.id.navHome) View.VISIBLE else View.GONE
        binding.pageConnect.visibility = if (itemId == R.id.navConnect) View.VISIBLE else View.GONE
        binding.pageSettings.visibility = if (itemId == R.id.navSettings) View.VISIBLE else View.GONE
    }

    private fun bindUpdates() {
        binding.appVersion.text = "Versão ${AppUpdater.currentVersion(this)}"
        binding.checkUpdate.setOnClickListener {
            val update = availableUpdate
            if (update != null) showUpdateDialog(update) else checkForUpdate(manual = true)
        }
        binding.updateNow.setOnClickListener { availableUpdate?.let { startUpdate(it) } }
        binding.updateLater.setOnClickListener {
            availableUpdate?.let { AppUpdater.dismiss(this, it.version) }
            binding.updateBanner.visibility = View.GONE
        }
    }

    private fun checkForUpdate(manual: Boolean) {
        if (checkingUpdate) return
        checkingUpdate = true
        if (manual) binding.updateStatus.text = "Procurando versão nova…"
        val context = applicationContext
        Thread {
            val result = runCatching { AppUpdater.check(context) }
            runOnUiThread {
                checkingUpdate = false
                if (isDestroyed) return@runOnUiThread
                result.onSuccess { update ->
                    availableUpdate = update
                    if (update == null) {
                        binding.updateStatus.text = "Você está usando a versão mais nova"
                        if (manual) Toast.makeText(this, "O app já está atualizado.", Toast.LENGTH_SHORT).show()
                        return@onSuccess
                    }
                    binding.updateStatus.text = "Versão ${update.version} disponível. Toque para atualizar"
                    binding.updateTitle.text = "Nova versão ${update.version} disponível"
                    val dismissed = AppUpdater.wasDismissed(this, update.version)
                    binding.updateBanner.visibility = if (dismissed && !manual) View.GONE else View.VISIBLE
                    if (manual || !dismissed) showUpdateDialog(update)
                }.onFailure {
                    if (manual) {
                        binding.updateStatus.text = "Não foi possível procurar agora. Verifique a internet"
                    }
                }
            }
        }.start()
    }

    private fun showUpdateDialog(update: AppUpdate) {
        MaterialAlertDialogBuilder(this)
            .setTitle("Atualização disponível")
            .setMessage(
                "A versão ${update.version} do Connect Linux está disponível. " +
                    "Você está usando a ${AppUpdater.currentVersion(this)}.\n\nDeseja atualizar agora?",
            )
            .setPositiveButton("Atualizar") { _, _ -> startUpdate(update) }
            .setNegativeButton("Depois") { _, _ -> AppUpdater.dismiss(this, update.version) }
            .show()
    }

    private fun startUpdate(update: AppUpdate) {
        if (downloadingUpdate) return
        if (!packageManager.canRequestPackageInstalls()) {
            pendingUpdate = update
            MaterialAlertDialogBuilder(this)
                .setTitle("Permitir a atualização")
                .setMessage(
                    "Para instalar a versão nova, o Android precisa que você permita que o Connect Linux " +
                        "instale apps. Ative a opção na próxima tela e volte aqui.",
                )
                .setPositiveButton("Abrir configurações") { _, _ ->
                    startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
                }
                .setNegativeButton("Cancelar") { _, _ -> pendingUpdate = null }
                .show()
            return
        }
        downloadingUpdate = true
        binding.updateBanner.visibility = View.VISIBLE
        binding.updateNow.isEnabled = false
        binding.updateProgress.isIndeterminate = true
        binding.updateProgress.visibility = View.VISIBLE
        binding.updateStatus.text = "Baixando a versão ${update.version}…"
        val context = applicationContext
        Thread {
            val result = runCatching {
                AppUpdater.download(context, update) { percent ->
                    runOnUiThread {
                        binding.updateProgress.isIndeterminate = false
                        binding.updateProgress.setProgressCompat(percent, true)
                        binding.updateStatus.text = "Baixando a versão ${update.version}… $percent%"
                    }
                }
            }
            runOnUiThread {
                downloadingUpdate = false
                if (isDestroyed) return@runOnUiThread
                binding.updateNow.isEnabled = true
                binding.updateProgress.visibility = View.GONE
                result.onSuccess { apk ->
                    binding.updateStatus.text = "Versão ${update.version} baixada. Toque para instalar"
                    startActivity(AppUpdater.installIntent(this, apk))
                }.onFailure { error ->
                    binding.updateStatus.text = "Falha ao baixar. Toque para tentar de novo"
                    Toast.makeText(this, "Não deu para baixar a atualização: ${error.message}", Toast.LENGTH_LONG).show()
                }
            }
        }.start()
    }

    override fun onStart() {
        super.onStart()
        bindService(Intent(this, ConnectService::class.java), connection, BIND_AUTO_CREATE)
        clipboardManager()?.addPrimaryClipChangedListener(clipListener)
    }

    override fun onStop() {
        clipboardManager()?.removePrimaryClipChangedListener(clipListener)
        service?.removeListener(listener)
        service = null
        unbindService(connection)
        super.onStop()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleShare(intent)
        flushShare()
    }

    private fun bindActions() {
        binding.phoneName.setOnFocusChangeListener { _, hasFocus ->
            if (!hasFocus) service?.setPhoneName(binding.phoneName.text?.toString().orEmpty())
        }
        binding.phoneName.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_DONE) {
                service?.setPhoneName(binding.phoneName.text?.toString().orEmpty())
                binding.phoneName.clearFocus()
                true
            } else {
                false
            }
        }
        binding.pairConfirm.setOnClickListener { submitPairCode() }
        binding.pairCode.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_DONE) {
                submitPairCode()
                true
            } else {
                false
            }
        }
        binding.pairCancel.setOnClickListener { service?.disconnect() }
        binding.sendText.setOnClickListener {
            val text = binding.shareInput.text?.toString()?.trim().orEmpty()
            if (text.isEmpty()) return@setOnClickListener
            service?.sendText(text)
            binding.shareInput.text?.clear()
        }
        binding.sendUrl.setOnClickListener {
            val text = binding.shareInput.text?.toString()?.trim().orEmpty()
            if (text.isEmpty()) return@setOnClickListener
            service?.sendUrl(text)
            binding.shareInput.text?.clear()
        }
        binding.sendFile.setOnClickListener { pickFile.launch(arrayOf("*/*")) }
        binding.sendClipboard.setOnClickListener {
            val text = clipboardText()
            if (text.isEmpty()) {
                Toast.makeText(this, "A área de transferência está vazia.", Toast.LENGTH_SHORT).show()
            } else {
                service?.sendClipboardNow(text)
            }
        }
        binding.findPc.setOnClickListener { service?.findComputer() }
        binding.forgetPc.setOnClickListener { service?.forget() }
        binding.manualConnect.setOnClickListener {
            val host = binding.manualHost.text?.toString()?.trim().orEmpty()
            val port = binding.manualPort.text?.toString()?.toIntOrNull() ?: Protocol.LINK_PORT
            if (host.isEmpty() || port !in 1..65535) {
                Toast.makeText(this, "Informe o IP mostrado no computador.", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            service?.connectTo(host, port, forcePair = false)
        }
        binding.bluetoothConnect.setOnClickListener {
            if (Build.VERSION.SDK_INT >= 31 &&
                ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED
            ) {
                requestBluetooth.launch(Manifest.permission.BLUETOOTH_CONNECT)
            } else {
                chooseBluetoothDevice()
            }
        }
        binding.usbConnect.setOnClickListener { service?.connectUsb() }
        binding.openNotifSettings.setOnClickListener {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }
        binding.chooseNotifApps.setOnClickListener {
            startActivity(Intent(this, NotificationAppsActivity::class.java))
        }
        binding.openBatterySettings.setOnClickListener {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
        binding.prepareMirror.setOnClickListener { prepareMirror() }
        binding.openOverlayRow.setOnClickListener {
            Toast.makeText(this, "Ative \"Sobrepor a outros apps\" para o Connect Linux.", Toast.LENGTH_LONG).show()
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        }
    }

    override fun onResume() {
        super.onResume()
        binding.openOverlaySettings.visibility = if (Settings.canDrawOverlays(this)) View.GONE else View.VISIBLE
        binding.notifAccessState.text = if (notificationAccessEnabled()) {
            "Permitido"
        } else {
            "Não permitido: toque para permitir"
        }
        binding.mirrorState.text = when {
            usbDebuggingEnabled() -> "Pronto: conecte o cabo e clique em Espelhar tela no computador"
            developerOptionsEnabled() -> "Falta ligar a Depuração USB: toque para ver como"
            else -> "Precisa de uma configuração única: toque para ver como"
        }
        val update = pendingUpdate
        if (update != null && packageManager.canRequestPackageInstalls()) {
            pendingUpdate = null
            startUpdate(update)
        }
    }

    private fun render(state: UiState) {
        val connected = state.connectedId != null
        binding.statusText.text = state.status
        binding.statusDot.backgroundTintList = ColorStateList.valueOf(
            ContextCompat.getColor(this, if (connected) R.color.mint else R.color.offline),
        )
        binding.sessionTitle.text = if (connected) {
            state.connectedName.ifBlank { "Computador" }
        } else {
            "Desconectado"
        }
        binding.sessionDetails.visibility = if (connected) View.VISIBLE else View.GONE
        binding.forgetPc.visibility = if (connected) View.VISIBLE else View.GONE
        if (!binding.phoneName.hasFocus() && binding.phoneName.text?.toString() != state.phoneName) {
            binding.phoneName.setText(state.phoneName)
        }
        val pairing = state.pairing && state.connectedId == null
        val before = shown
        if (before != null && ((pairing && !before.pairing) || (connected && !before.connected))) {
            binding.bottomNav.selectedItemId = R.id.navHome
        }
        shown = Shown(pairing, connected)
        binding.pairBox.visibility = if (pairing) View.VISIBLE else View.GONE
        binding.pairHint.text = if (state.pairError.isNotBlank()) {
            state.pairError
        } else {
            "Digite o código que apareceu no computador ${state.pairingName}."
        }
        binding.sessionBox.visibility = if (connected) View.VISIBLE else View.GONE
        val latency = state.latencyMs?.let { "$it ms" } ?: "medindo…"
        val battery = state.desktopBattery.ifBlank { "bateria do computador indisponível" }
        binding.sessionDetails.text = "$latency · $battery"
        binding.fileStatus.text = state.fileStatus
        binding.fileStatus.visibility = if (state.fileStatus.isBlank()) View.GONE else View.VISIBLE
        binding.eventText.text = state.lastEvent
        binding.eventText.visibility = if (state.lastEvent.isBlank()) View.GONE else View.VISIBLE
        binding.clipSwitch.setOnCheckedChangeListener(null)
        binding.clipSwitch.isChecked = state.clipboardSync
        binding.clipSwitch.setOnCheckedChangeListener { _, checked -> service?.setClipboardSync(checked) }
        binding.notifSwitch.setOnCheckedChangeListener(null)
        binding.notifSwitch.isChecked = state.forwardNotifications
        binding.notifSwitch.setOnCheckedChangeListener { _, checked ->
            service?.setForwardNotifications(checked)
            if (checked && !notificationAccessEnabled()) {
                Toast.makeText(this, "Ative o acesso às notificações do Connect Linux.", Toast.LENGTH_LONG).show()
            }
        }
        renderDevices(state.devices)
        flushShare()
    }

    private fun renderDevices(devices: List<FoundDevice>) {
        val signature = devices.joinToString("|") { "${it.id}:${it.name}:${it.host}:${it.port}:${it.online}:${it.paired}" }
        if (signature == deviceSignature) return
        deviceSignature = signature
        binding.deviceList.removeAllViews()
        binding.deviceEmpty.visibility = if (devices.isEmpty()) View.VISIBLE else View.GONE
        if (devices.isEmpty()) {
            return
        }
        for (device in devices) {
            val row = ItemDeviceBinding.inflate(layoutInflater, binding.deviceList, false)
            row.deviceName.text = device.name
            val state = when {
                device.online -> "Conectado"
                device.paired -> "Pareado"
                else -> "Disponível na rede"
            }
            val address = if (device.port == Protocol.LINK_PORT) device.host else "${device.host}:${device.port}"
            row.deviceMeta.text = "$state · $address"
            row.deviceAction.text = when {
                device.online -> "Conectado"
                device.paired -> "Conectar"
                else -> "Parear"
            }
            row.deviceAction.isEnabled = !device.online && device.host.isNotBlank()
            row.deviceAction.setOnClickListener {
                service?.connectTo(device.host, device.port, forcePair = !device.paired)
            }
            binding.deviceList.addView(row.root)
        }
    }

    @SuppressLint("MissingPermission")
    private fun chooseBluetoothDevice() {
        val adapter = getSystemService(BluetoothManager::class.java)?.adapter
        if (adapter == null) {
            Toast.makeText(this, "Este celular não tem Bluetooth.", Toast.LENGTH_LONG).show()
            return
        }
        if (!adapter.isEnabled) {
            Toast.makeText(this, "Ligue o Bluetooth do celular e tente de novo.", Toast.LENGTH_LONG).show()
            startActivity(Intent(Settings.ACTION_BLUETOOTH_SETTINGS))
            return
        }
        val bonded = adapter.bondedDevices.orEmpty().sortedBy { it.name.orEmpty().lowercase() }
        if (bonded.isEmpty()) {
            MaterialAlertDialogBuilder(this)
                .setTitle("Nenhum aparelho pareado")
                .setMessage(
                    "No computador, clique em \"Ligar Bluetooth e ficar visível\". " +
                        "Depois pareie o celular com o computador nas configurações de Bluetooth do Android e volte aqui.",
                )
                .setPositiveButton("Abrir Bluetooth") { _, _ -> startActivity(Intent(Settings.ACTION_BLUETOOTH_SETTINGS)) }
                .setNegativeButton("Cancelar", null)
                .show()
            return
        }
        val names = bonded.map { device -> device.name?.takeIf { it.isNotBlank() } ?: device.address }.toTypedArray()
        MaterialAlertDialogBuilder(this)
            .setTitle("Escolha o computador")
            .setItems(names) { _, which ->
                val device = bonded[which]
                service?.connectBluetooth(device.address, names[which])
            }
            .setNeutralButton("Parear outro") { _, _ -> startActivity(Intent(Settings.ACTION_BLUETOOTH_SETTINGS)) }
            .setNegativeButton("Cancelar", null)
            .show()
    }

    private fun handleShare(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND) return
        val stream = if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(Intent.EXTRA_STREAM)
        }
        val text = intent.getStringExtra(Intent.EXTRA_TEXT)
        when {
            stream != null -> pendingUri = stream
            !text.isNullOrBlank() -> pendingText = text
        }
    }

    private fun flushShare() {
        val current = service ?: return
        if (pendingUri == null && pendingText == null) return
        if (current.state().connectedId == null) return
        val uri = pendingUri
        val text = pendingText
        pendingUri = null
        pendingText = null
        when {
            uri != null -> current.sendFile(uri)
            text != null && Protocol.isHttpUrl(text.trim()) -> current.sendUrl(text.trim())
            text != null -> current.sendText(text)
        }
    }

    private fun clipboardManager(): ClipboardManager? = getSystemService(ClipboardManager::class.java)

    private fun clipboardText(): String {
        val clip: ClipData = clipboardManager()?.primaryClip ?: return ""
        if (clip.itemCount == 0) return ""
        return clip.getItemAt(0).coerceToText(this)?.toString().orEmpty()
    }

    private fun developerOptionsEnabled(): Boolean =
        Settings.Global.getInt(contentResolver, Settings.Global.DEVELOPMENT_SETTINGS_ENABLED, 0) == 1

    private fun usbDebuggingEnabled(): Boolean =
        Settings.Global.getInt(contentResolver, Settings.Global.ADB_ENABLED, 0) == 1

    private fun prepareMirror() {
        val (message, action, intent) = when {
            usbDebuggingEnabled() -> Triple(
                "A Depuração USB já está ligada.\n\n" +
                    "1. Conecte o celular ao computador pelo cabo USB.\n" +
                    "2. No computador, clique em Espelhar tela (na janela ou no ícone ao lado do relógio).\n" +
                    "3. Na primeira vez, aceite \"Permitir depuração USB\" aqui no celular.",
                null,
                null,
            )
            developerOptionsEnabled() -> Triple(
                "Falta só ligar a Depuração USB.\n\n" +
                    "Na tela que vai abrir, procure \"Depuração USB\" e ligue a chave. " +
                    "Depois volte para o Connect Linux.",
                "Abrir opções do desenvolvedor",
                Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS),
            )
            else -> Triple(
                "O Android só permite espelhar a tela depois de uma configuração feita por você, uma única vez:\n\n" +
                    "1. Na tela que vai abrir, entre em \"Informações do software\" e toque 7 vezes em " +
                    "\"Número de compilação\" até aparecer \"Modo de desenvolvedor ativado\".\n" +
                    "2. Volte às Configurações, abra \"Opções do desenvolvedor\" e ligue \"Depuração USB\".\n" +
                    "3. Volte para o Connect Linux.",
                "Abrir configurações",
                Intent(Settings.ACTION_DEVICE_INFO_SETTINGS),
            )
        }
        val dialog = MaterialAlertDialogBuilder(this)
            .setTitle("Espelhar a tela no computador")
            .setMessage(message)
        if (action != null && intent != null) {
            dialog.setPositiveButton(action) { _, _ ->
                try {
                    startActivity(intent)
                } catch (_: Exception) {
                    startActivity(Intent(Settings.ACTION_SETTINGS))
                }
            }
            dialog.setNegativeButton("Agora não", null)
        } else {
            dialog.setPositiveButton("Entendi", null)
        }
        dialog.show()
    }

    private fun notificationAccessEnabled(): Boolean {
        val enabled = Settings.Secure.getString(contentResolver, "enabled_notification_listeners") ?: return false
        return enabled.split(":").any { it.contains(packageName) }
    }

    private data class Shown(val pairing: Boolean, val connected: Boolean)

    private companion object {
        const val KEY_TAB = "tab"
    }
}
