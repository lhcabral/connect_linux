package dev.connectlinux.app

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import dev.connectlinux.app.databinding.ActivityNotificationAppsBinding
import dev.connectlinux.app.databinding.ItemNotificationAppBinding

class NotificationAppsActivity : AppCompatActivity() {
    private lateinit var binding: ActivityNotificationAppsBinding
    private lateinit var store: DeviceStore

    override fun onCreate(savedInstanceState: Bundle?) {
        drawBehindSystemBars()
        super.onCreate(savedInstanceState)
        binding = ActivityNotificationAppsBinding.inflate(layoutInflater)
        setContentView(binding.root)
        applySystemInsets(binding.root, binding.toolbarHost)
        store = DeviceStore(this)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.openAccess.setOnClickListener {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }
        binding.masterSwitch.isChecked = store.forwardNotifications
        binding.masterSwitch.setOnCheckedChangeListener { _, checked ->
            ConnectService.instance?.setForwardNotifications(checked) ?: run { store.forwardNotifications = checked }
            binding.appList.alpha = if (checked) 1f else 0.5f
        }
        binding.appList.alpha = if (store.forwardNotifications) 1f else 0.5f
        loadApps()
    }

    override fun onResume() {
        super.onResume()
        binding.accessBox.visibility = if (notificationAccessEnabled()) View.GONE else View.VISIBLE
    }

    private fun loadApps() {
        val context = applicationContext
        Thread {
            val apps = MessagingApps.installed(context)
            runOnUiThread { if (!isDestroyed) renderApps(apps) }
        }.start()
    }

    private fun renderApps(apps: List<MessagingApp>) {
        binding.loading.visibility = View.GONE
        binding.emptyText.visibility = if (apps.isEmpty()) View.VISIBLE else View.GONE
        binding.appList.removeAllViews()
        for (app in apps) {
            val row = ItemNotificationAppBinding.inflate(layoutInflater, binding.appList, false)
            row.appIcon.setImageDrawable(app.icon)
            row.appName.text = app.label
            val enabled = store.notificationAppEnabled(app.packageName)
            row.appSwitch.isChecked = enabled
            row.appState.text = stateLabel(enabled)
            row.appSwitch.contentDescription = app.label
            row.appSwitch.setOnCheckedChangeListener { _, checked ->
                store.setNotificationAppEnabled(app.packageName, checked)
                row.appState.text = stateLabel(checked)
            }
            row.root.setOnClickListener { row.appSwitch.toggle() }
            binding.appList.addView(row.root)
        }
    }

    private fun stateLabel(enabled: Boolean) =
        if (enabled) "Envia notificações para o computador" else "Não envia notificações"

    private fun notificationAccessEnabled(): Boolean {
        val enabled = Settings.Secure.getString(contentResolver, "enabled_notification_listeners") ?: return false
        return enabled.split(":").any { it.contains(packageName) }
    }
}
