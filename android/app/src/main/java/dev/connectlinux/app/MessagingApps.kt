package dev.connectlinux.app

import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.content.pm.ResolveInfo
import android.graphics.drawable.Drawable
import android.net.Uri
import android.os.Build
import java.util.concurrent.ConcurrentHashMap

data class MessagingApp(
    val packageName: String,
    val label: String,
    val icon: Drawable?,
)

object MessagingApps {
    private val known = setOf(
        "com.whatsapp",
        "com.whatsapp.w4b",
        "org.telegram.messenger",
        "org.telegram.messenger.web",
        "org.thunderdog.challegram",
        "org.thoughtcrime.securesms",
        "com.facebook.orca",
        "com.facebook.mlite",
        "com.facebook.katana",
        "com.facebook.lite",
        "com.instagram.android",
        "com.instagram.barcelona",
        "com.twitter.android",
        "com.zhiliaoapp.musically",
        "com.ss.android.ugc.trill",
        "com.snapchat.android",
        "com.discord",
        "com.Slack",
        "com.microsoft.teams",
        "com.skype.raider",
        "com.linkedin.android",
        "com.reddit.frontpage",
        "com.pinterest",
        "com.viber.voip",
        "jp.naver.line.android",
        "com.tencent.mm",
        "com.kakao.talk",
        "im.vector.app",
        "xyz.blueskyweb.app",
        "org.joinmastodon.android",
        "com.google.android.apps.messaging",
        "com.samsung.android.messaging",
        "com.google.android.apps.dynamite",
        "com.google.android.talk",
        "com.kwai.video",
        "com.tumblr",
        "ch.threema.app",
        "chat.simplex.app",
        "com.wire",
    )
    private val cache = ConcurrentHashMap<String, Boolean>()

    fun isMessaging(context: Context, packageName: String): Boolean =
        cache.getOrPut(packageName) { detect(context.packageManager, packageName) }

    fun installed(context: Context): List<MessagingApp> {
        val pm = context.packageManager
        val candidates = linkedSetOf<String>()
        query(pm, Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)).mapTo(candidates) { it.activityInfo.packageName }
        query(pm, smsIntent()).mapTo(candidates) { it.activityInfo.packageName }
        return candidates
            .filter { it != context.packageName && isMessaging(context, it) }
            .mapNotNull { name ->
                try {
                    val info = pm.getApplicationInfo(name, 0)
                    MessagingApp(name, pm.getApplicationLabel(info).toString(), pm.getApplicationIcon(info))
                } catch (_: PackageManager.NameNotFoundException) {
                    null
                }
            }
            .sortedBy { it.label.lowercase() }
    }

    private fun detect(pm: PackageManager, packageName: String): Boolean {
        if (packageName in known) return true
        val info = try {
            pm.getApplicationInfo(packageName, 0)
        } catch (_: PackageManager.NameNotFoundException) {
            return false
        }
        if (info.category == ApplicationInfo.CATEGORY_SOCIAL) return true
        return query(pm, smsIntent().setPackage(packageName)).isNotEmpty()
    }

    private fun smsIntent() = Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:"))

    private fun query(pm: PackageManager, intent: Intent): List<ResolveInfo> =
        if (Build.VERSION.SDK_INT >= 33) {
            pm.queryIntentActivities(intent, PackageManager.ResolveInfoFlags.of(0))
        } else {
            @Suppress("DEPRECATION")
            pm.queryIntentActivities(intent, 0)
        }
}
