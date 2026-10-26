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
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.provider.Settings
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
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
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        bindActions()
        handleShare(intent)
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        ContextCompat.startForegroundService(this, Intent(this, ConnectService::class.java))
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
        binding.pairConfirm.setOnClickListener {
            service?.submitCode(binding.pairCode.text?.toString().orEmpty())
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
        binding.openOverlaySettings.setOnClickListener {
            Toast.makeText(this, "Ative \"Sobrepor a outros apps\" para o Connect Linux.", Toast.LENGTH_LONG).show()
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        }
    }

    override fun onResume() {
        super.onResume()
        binding.openOverlaySettings.visibility = if (Settings.canDrawOverlays(this)) View.GONE else View.VISIBLE
    }

    private fun render(state: UiState) {
        binding.statusText.text = state.status
        if (!binding.phoneName.hasFocus() && binding.phoneName.text?.toString() != state.phoneName) {
            binding.phoneName.setText(state.phoneName)
        }
        val pairing = state.pairing && state.connectedId == null
        binding.pairBox.visibility = if (pairing) View.VISIBLE else View.GONE
        binding.pairHint.text = if (state.pairError.isNotBlank()) {
            state.pairError
        } else {
            "Digite o código que apareceu no computador ${state.pairingName}."
        }
        binding.sessionBox.visibility = if (state.connectedId != null) View.VISIBLE else View.GONE
        binding.sessionTitle.text = state.connectedName.ifBlank { "Computador" }
        val latency = state.latencyMs?.let { "$it ms" } ?: "medindo…"
        val battery = state.desktopBattery.ifBlank { "bateria do computador indisponível" }
        binding.sessionDetails.text = "$latency · $battery"
        binding.fileStatus.text = state.fileStatus
        binding.fileStatus.visibility = if (state.fileStatus.isBlank()) View.GONE else View.VISIBLE
        binding.eventText.text = state.lastEvent
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

    private fun notificationAccessEnabled(): Boolean {
        val enabled = Settings.Secure.getString(contentResolver, "enabled_notification_listeners") ?: return false
        return enabled.split(":").any { it.contains(packageName) }
    }
}
