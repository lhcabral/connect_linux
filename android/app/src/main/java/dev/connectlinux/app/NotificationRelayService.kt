package dev.connectlinux.app

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

class NotificationRelayService : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName == packageName) return
        val notification = sbn.notification ?: return
        if (notification.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        val extras = notification.extras ?: return
        if (extras.getInt(Notification.EXTRA_PROGRESS_MAX, 0) > 0) return
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()?.trim().orEmpty()
        val body = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()?.trim().orEmpty()
        if (title.isEmpty() && body.isEmpty()) return
        val appName = try {
            val info = packageManager.getApplicationInfo(sbn.packageName, 0)
            packageManager.getApplicationLabel(info).toString()
        } catch (_: Exception) {
            sbn.packageName
        }
        ConnectService.instance?.relayNotification(sbn.packageName, appName, title, body)
    }
}
