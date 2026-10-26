package dev.connectlinux.app

import android.content.Context
import android.os.Build
import org.json.JSONObject
import java.util.UUID

data class SavedDesktop(
    val id: String,
    val name: String,
    val token: String,
    val host: String,
    val port: Int,
)

class DeviceStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("connectlinux", Context.MODE_PRIVATE)
    private val lock = Any()

    val deviceId: String = synchronized(lock) {
        prefs.getString(KEY_ID, null)?.takeIf { Protocol.isDeviceId(it) }
            ?: UUID.randomUUID().toString().also { prefs.edit().putString(KEY_ID, it).apply() }
    }

    var deviceName: String
        get() = Protocol.sanitizeName(prefs.getString(KEY_NAME, null) ?: defaultName())
        set(value) {
            prefs.edit().putString(KEY_NAME, Protocol.sanitizeName(value)).apply()
        }

    var clipboardSync: Boolean
        get() = prefs.getBoolean(KEY_CLIPBOARD, true)
        set(value) {
            prefs.edit().putBoolean(KEY_CLIPBOARD, value).apply()
        }

    var forwardNotifications: Boolean
        get() = prefs.getBoolean(KEY_NOTIFICATIONS, true)
        set(value) {
            prefs.edit().putBoolean(KEY_NOTIFICATIONS, value).apply()
        }

    fun notificationAppEnabled(packageName: String): Boolean =
        packageName !in prefs.getStringSet(KEY_MUTED_APPS, emptySet()).orEmpty()

    fun setNotificationAppEnabled(packageName: String, enabled: Boolean) = synchronized(lock) {
        val muted = prefs.getStringSet(KEY_MUTED_APPS, emptySet()).orEmpty().toMutableSet()
        if (enabled) muted.remove(packageName) else muted.add(packageName)
        prefs.edit().putStringSet(KEY_MUTED_APPS, muted).apply()
    }

    var lastDesktopId: String?
        get() = prefs.getString(KEY_LAST, null)
        set(value) {
            prefs.edit().putString(KEY_LAST, value).apply()
        }

    var lastTarget: Target?
        get() {
            val parts = prefs.getString(KEY_TARGET, null)?.split('|') ?: return null
            return when {
                parts.size == 3 && parts[0] == "bt" -> Target.Bluetooth(parts[1], parts[2])
                parts.size == 3 && parts[0] == "net" -> parts[2].toIntOrNull()?.let { Target.Network(parts[1], it) }
                else -> null
            }
        }
        set(value) {
            val encoded = when (value) {
                is Target.Bluetooth -> "bt|${value.address}|${value.name.replace('|', ' ')}"
                is Target.Network -> "net|${value.host}|${value.port}"
                null -> null
            }
            prefs.edit().putString(KEY_TARGET, encoded).apply()
        }

    fun pairing(id: String): SavedDesktop? = synchronized(lock) { read()[id] }

    fun pairings(): List<SavedDesktop> = synchronized(lock) { read().values.toList() }

    fun save(desktop: SavedDesktop) = synchronized(lock) {
        val all = read()
        all[desktop.id] = desktop
        write(all)
        prefs.edit().putString(KEY_LAST, desktop.id).apply()
    }

    fun forget(id: String) = synchronized(lock) {
        val all = read()
        all.remove(id)
        write(all)
        if (prefs.getString(KEY_LAST, null) == id) {
            prefs.edit().remove(KEY_LAST).remove(KEY_TARGET).apply()
        }
    }

    private fun read(): MutableMap<String, SavedDesktop> {
        val raw = prefs.getString(KEY_PAIRS, null) ?: return mutableMapOf()
        val result = mutableMapOf<String, SavedDesktop>()
        try {
            val json = JSONObject(raw)
            for (key in json.keys()) {
                val item = json.optJSONObject(key) ?: continue
                val token = item.optString("token")
                if (!Protocol.isDeviceId(key) || token.isBlank()) continue
                result[key] = SavedDesktop(
                    id = key,
                    name = Protocol.sanitizeName(item.optString("name")),
                    token = token,
                    host = item.optString("host"),
                    port = item.optInt("port", Protocol.LINK_PORT),
                )
            }
        } catch (_: Exception) {
            return mutableMapOf()
        }
        return result
    }

    private fun write(all: Map<String, SavedDesktop>) {
        val json = JSONObject()
        for ((id, desktop) in all) {
            json.put(
                id,
                JSONObject()
                    .put("name", desktop.name)
                    .put("token", desktop.token)
                    .put("host", desktop.host)
                    .put("port", desktop.port),
            )
        }
        prefs.edit().putString(KEY_PAIRS, json.toString()).apply()
    }

    private fun defaultName(): String {
        val maker = Build.MANUFACTURER.replaceFirstChar { char ->
            if (char.isLowerCase()) char.titlecase() else char.toString()
        }
        return "$maker ${Build.MODEL}"
    }

    private companion object {
        const val KEY_ID = "device_id"
        const val KEY_NAME = "device_name"
        const val KEY_CLIPBOARD = "clipboard"
        const val KEY_NOTIFICATIONS = "notifications"
        const val KEY_MUTED_APPS = "muted_notification_apps"
        const val KEY_LAST = "last_desktop"
        const val KEY_TARGET = "last_target"
        const val KEY_PAIRS = "pairs"
    }
}
