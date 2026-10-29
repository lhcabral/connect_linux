package dev.connectlinux.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.BatteryManager
import android.os.Binder
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.provider.Settings
import androidx.core.app.NotificationCompat
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat
import java.io.File
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class ConnectService : Service() {
    inner class LocalBinder : Binder() {
        fun service(): ConnectService = this@ConnectService
    }

    private val store by lazy { DeviceStore(this) }
    private val commands = Executors.newSingleThreadExecutor()
    private val io = Executors.newCachedThreadPool()
    private val photos = Executors.newSingleThreadExecutor()
    private val scheduler = Executors.newSingleThreadScheduledExecutor()
    private val main = Handler(Looper.getMainLooper())
    private val listeners = CopyOnWriteArrayList<(UiState) -> Unit>()
    private val stateLock = Any()
    private val binder = LocalBinder()
    private val seen = mutableMapOf<String, Seen>()
    private val generation = AtomicInteger(0)

    @Volatile private var ui = UiState()
    @Volatile private var link: LinkClient? = null
    @Volatile private var ready = false
    @Volatile private var activeDesktopId: String? = null
    @Volatile private var userDisconnect = false
    @Volatile private var authBlocked = false
    @Volatile var echoGuard: String? = null

    private var desired: Target? = null
    private var forceNext = false
    private var lastNotice = ""
    private var lastNoticeAt = 0L
    private var lastConnectRequestAt = 0L
    private val pendingOutgoing = mutableListOf<Pair<CachedFile, Boolean>>()
    private var pendingDelete: Pair<LinkClient, List<Long>>? = null
    private lateinit var discovery: DiscoveryClient

    override fun onCreate() {
        super.onCreate()
        instance = this
        NotificationRelayService.rebind(this)
        createChannels()
        startAsForeground("Procurando computador")
        ui = UiState(
            phoneName = store.deviceName,
            clipboardSync = store.clipboardSync,
            forwardNotifications = store.forwardNotifications,
        )
        discovery = DiscoveryClient(
            context = this,
            deviceId = store.deviceId,
            nameProvider = { store.deviceName },
            onAnnounce = { id, name, host, port ->
                commands.execute { noteAnnounce(id, name, host, port) }
            },
            onConnectRequest = { id, name, host, port ->
                commands.execute { noteConnectRequest(id, name, host, port) }
            },
            onFailure = { message ->
                commands.execute {
                    if (link == null) update { it.copy(status = message) }
                }
            },
        )
        discovery.start()
        scheduler.scheduleAtFixedRate({
            commands.execute { publish() }
        }, 3, 3, TimeUnit.SECONDS)
        scheduler.scheduleAtFixedRate({
            val client = link
            if (ready && client != null) {
                try {
                    client.sendPing()
                    readBattery()?.let { client.sendBattery(it.first, it.second) }
                } catch (_: Exception) {
                }
            }
        }, 15, 15, TimeUnit.SECONDS)
        commands.execute {
            val saved = store.lastDesktopId?.let { store.pairing(it) }
            val last = store.lastTarget
            if (saved != null && last != null) {
                desired = last
                startLink()
            } else if (saved != null && saved.host.isNotBlank()) {
                desired = Target.Network(saved.host, saved.port)
                startLink()
            }
            publish()
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startAsForeground(if (ready) "Conectado" else "Procurando computador")
        if (intent?.action == ACTION_SEND_FILES) {
            val paths = intent.getStringArrayExtra(EXTRA_PATHS).orEmpty()
            val names = intent.getStringArrayExtra(EXTRA_NAMES).orEmpty()
            val mimes = intent.getStringArrayExtra(EXTRA_MIMES).orEmpty()
            val outbox = File(cacheDir, "outbox").canonicalFile
            val files = paths.indices.mapNotNull { index ->
                val file = File(paths[index]).canonicalFile
                if (file.parentFile != outbox || !file.isFile) return@mapNotNull null
                CachedFile(file, names.getOrNull(index) ?: file.name, mimes.getOrNull(index) ?: "application/octet-stream")
            }
            if (files.isNotEmpty()) queueOutgoing(files, intent.getBooleanExtra(EXTRA_OPEN, false))
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onDestroy() {
        instance = null
        discovery.stop()
        link?.close()
        commands.shutdownNow()
        io.shutdownNow()
        photos.shutdownNow()
        scheduler.shutdownNow()
        stopForeground(STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }

    fun addListener(listener: (UiState) -> Unit) {
        listeners.add(listener)
    }

    fun removeListener(listener: (UiState) -> Unit) {
        listeners.remove(listener)
    }

    fun state(): UiState = synchronized(stateLock) { ui }

    fun connectTo(host: String, port: Int, forcePair: Boolean) {
        connect(Target.Network(host, port), forcePair)
    }

    fun connectUsb() {
        connect(Target.Network(Target.USB_HOST, Protocol.LINK_PORT), forcePair = false)
    }

    fun connectBluetooth(address: String, name: String) {
        connect(Target.Bluetooth(address, Protocol.sanitizeName(name)), forcePair = false)
    }

    private fun connect(target: Target, forcePair: Boolean) {
        commands.execute {
            userDisconnect = false
            authBlocked = false
            desired = target
            forceNext = forcePair
            val old = link
            link = null
            ready = false
            generation.incrementAndGet()
            old?.close()
            startLink()
        }
    }

    fun disconnect() {
        commands.execute {
            userDisconnect = true
            val old = link
            link = null
            ready = false
            activeDesktopId = null
            generation.incrementAndGet()
            old?.close()
            update { it.copy(connectedId = null, pairing = false, status = "Desconectado.", desktopBattery = "", latencyMs = null) }
            refreshForeground("Desconectado")
            publish()
        }
    }

    fun submitCode(code: String) {
        val digits = code.filter { it.isDigit() }.take(6)
        if (digits.length != 6) {
            update { it.copy(pairError = "Digite os 6 dígitos mostrados no computador.") }
            return
        }
        val client = link
        if (client == null) {
            update { it.copy(pairError = "A conexão caiu. Conecte de novo.") }
            return
        }
        io.execute {
            try {
                client.submitCode(digits)
            } catch (exc: Exception) {
                update { it.copy(pairError = "Não foi possível enviar o código. Conecte de novo.") }
            }
        }
    }

    fun sendText(text: String) {
        val client = link
        if (!ready || client == null) {
            update { it.copy(lastEvent = "Conecte um computador primeiro.") }
            return
        }
        io.execute {
            try {
                client.sendText(text)
                update { it.copy(lastEvent = "Texto enviado.") }
            } catch (exc: Exception) {
                update { it.copy(lastEvent = exc.message ?: "Falha ao enviar o texto.") }
            }
        }
    }

    fun sendUrl(url: String) {
        if (!Protocol.isHttpUrl(url)) {
            update { it.copy(lastEvent = "Use um endereço http ou https.") }
            return
        }
        val client = link
        if (!ready || client == null) {
            update { it.copy(lastEvent = "Conecte um computador primeiro.") }
            return
        }
        io.execute {
            try {
                client.sendUrl(url)
                update { it.copy(lastEvent = "Link enviado ao computador.") }
            } catch (exc: Exception) {
                update { it.copy(lastEvent = exc.message ?: "Falha ao enviar o link.") }
            }
        }
    }

    fun sendFile(uri: Uri) {
        val client = link
        if (!ready || client == null) {
            update { it.copy(lastEvent = "Conecte um computador primeiro.") }
            return
        }
        io.execute {
            val cached = try {
                Outbox.copy(this, uri)
            } catch (exc: Exception) {
                update { it.copy(lastEvent = exc.message ?: "Não foi possível ler o arquivo.") }
                return@execute
            }
            sendCached(client, cached, open = false)
        }
    }

    private fun sendCached(client: LinkClient, cached: CachedFile, open: Boolean) {
        try {
            client.sendFile(cached.file, cached.name, cached.mime, open)
        } catch (exc: Exception) {
            update { it.copy(lastEvent = exc.message ?: "Falha ao enviar o arquivo.", fileStatus = "Falha ao enviar o arquivo.") }
        } finally {
            cached.file.delete()
        }
    }

    private fun queueOutgoing(files: List<CachedFile>, open: Boolean) {
        commands.execute {
            val client = link
            if (ready && client != null) {
                files.forEach { cached -> io.execute { sendCached(client, cached, open) } }
                return@execute
            }
            pendingOutgoing.addAll(files.map { it to open })
            while (pendingOutgoing.size > 20) pendingOutgoing.removeAt(0).first.file.delete()
            update { it.copy(lastEvent = "Conectando ao computador para enviar…") }
            if (link == null) {
                val saved = store.lastDesktopId?.let { store.pairing(it) }
                val last = store.lastTarget
                userDisconnect = false
                authBlocked = false
                desired = when {
                    saved != null && last != null -> last
                    saved != null && saved.host.isNotBlank() -> Target.Network(saved.host, saved.port)
                    else -> desired
                }
                startLink()
            }
            if (desired == null) {
                notifyUser("Connect Linux", "Pareie um computador para enviar imagens.", 4)
            }
        }
    }

    private fun flushOutgoing(client: LinkClient) {
        val waiting = pendingOutgoing.toList()
        pendingOutgoing.clear()
        waiting.forEach { (cached, open) -> io.execute { sendCached(client, cached, open) } }
    }

    private fun showImage(uri: Uri, name: String, from: String) {
        val viewer = Intent(this, ImageViewerActivity::class.java)
            .setData(uri)
            .putExtra(ImageViewerActivity.EXTRA_NAME, name)
            .putExtra(ImageViewerActivity.EXTRA_FROM, from)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_GRANT_READ_URI_PERMISSION)
        val tap = PendingIntent.getActivity(this, 5, viewer, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(this, "images")
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("Imagem de $from")
            .setContentText("$name · toque para ver")
            .setContentIntent(tap)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .build()
        getSystemService(NotificationManager::class.java)?.notify(5, notification)
        main.post {
            try {
                startActivity(viewer)
            } catch (_: Exception) {
            }
        }
    }

    private fun publishShareTarget(desktopId: String, desktopName: String) {
        try {
            val shortcut = ShortcutInfoCompat.Builder(this, "computer-$desktopId")
                .setShortLabel(desktopName.take(25))
                .setLongLabel("Abrir em $desktopName")
                .setIcon(IconCompat.createWithResource(this, R.mipmap.ic_launcher))
                .setIntent(Intent(this, ShareToComputerActivity::class.java).setAction(Intent.ACTION_DEFAULT))
                .setCategories(setOf(SHARE_CATEGORY))
                .setLongLived(true)
                .build()
            ShortcutManagerCompat.pushDynamicShortcut(this, shortcut)
        } catch (_: Exception) {
        }
    }

    fun sendClipboardNow(text: String) {
        val client = link
        if (!ready || client == null || text.isEmpty()) {
            update { it.copy(lastEvent = "Nada para enviar ou sem conexão.") }
            return
        }
        io.execute {
            try {
                client.sendClipboard(text)
                update { it.copy(lastEvent = "Área de transferência enviada.") }
            } catch (exc: Exception) {
                update { it.copy(lastEvent = exc.message ?: "Falha ao enviar a área de transferência.") }
            }
        }
    }

    fun onLocalClipboard(text: String) {
        if (!store.clipboardSync || text.isEmpty()) return
        if (text == echoGuard) {
            echoGuard = null
            return
        }
        val client = link
        if (!ready || client == null) return
        io.execute {
            try {
                client.sendClipboard(text)
            } catch (_: Exception) {
            }
        }
    }

    fun findComputer() {
        val client = link
        if (!ready || client == null) return
        io.execute {
            try {
                client.findComputer()
                update { it.copy(lastEvent = "Chamando o computador…") }
            } catch (exc: Exception) {
                update { it.copy(lastEvent = exc.message ?: "Falha ao chamar o computador.") }
            }
        }
    }

    fun forget() {
        commands.execute {
            val id = activeDesktopId ?: store.lastDesktopId
            val client = link
            userDisconnect = true
            authBlocked = true
            if (ready && client != null) {
                try {
                    client.unpair()
                } catch (_: Exception) {
                }
            }
            if (id != null) store.forget(id)
            link = null
            ready = false
            activeDesktopId = null
            generation.incrementAndGet()
            client?.close()
            update {
                it.copy(
                    connectedId = null,
                    pairing = false,
                    status = "Computador esquecido.",
                    lastEvent = "Computador esquecido.",
                    desktopBattery = "",
                )
            }
            publish()
        }
    }

    fun setPhoneName(name: String) {
        store.deviceName = name
        commands.execute { publish() }
    }

    fun setClipboardSync(enabled: Boolean) {
        store.clipboardSync = enabled
        update { it.copy(clipboardSync = enabled) }
    }

    fun setForwardNotifications(enabled: Boolean) {
        store.forwardNotifications = enabled
        update { it.copy(forwardNotifications = enabled) }
    }

    fun relayNotification(packageName: String, appName: String, title: String, body: String) {
        if (!store.forwardNotifications || !ready) return
        if (!MessagingApps.isMessaging(this, packageName) || !store.notificationAppEnabled(packageName)) return
        val stamp = "$appName|$title|$body"
        val now = SystemClock.elapsedRealtime()
        if (stamp == lastNotice && now - lastNoticeAt < 2500) return
        lastNotice = stamp
        lastNoticeAt = now
        val client = link ?: return
        io.execute {
            try {
                client.sendNotification(UUID.randomUUID().toString(), appName, title, body)
            } catch (_: Exception) {
            }
        }
    }

    private fun noteAnnounce(id: String, name: String, host: String, port: Int) {
        seen[id] = Seen(id, name, host, port, SystemClock.elapsedRealtime())
        val saved = store.pairing(id)
        if (saved != null && (saved.host != host || saved.port != port || saved.name != name)) {
            store.save(saved.copy(name = name, host = host, port = port))
            val current = desired
            val onWifi = current is Target.Network && current.host != Target.USB_HOST
            if (activeDesktopId == id && onWifi && current != Target.Network(host, port)) {
                desired = Target.Network(host, port)
                val old = link
                link = null
                ready = false
                generation.incrementAndGet()
                old?.close()
                startLink()
            }
        }
        if (link == null && !userDisconnect && !authBlocked && saved != null) {
            val preferred = store.lastDesktopId
            if (preferred == null || preferred == id) {
                desired = Target.Network(host, port)
                forceNext = false
                startLink()
            }
        }
        publish()
    }

    private fun noteConnectRequest(id: String, name: String, host: String, port: Int) {
        val saved = store.pairing(id) ?: return
        val recent = seen[id]
        val recentOk = recent != null && recent.host == host && SystemClock.elapsedRealtime() - recent.at < 15_000
        if (!recentOk && saved.host != host) return
        val now = SystemClock.elapsedRealtime()
        seen[id] = Seen(id, name.ifBlank { saved.name }, host, port, now)
        userDisconnect = false
        authBlocked = false
        val target = Target.Network(host, port)
        if (now - lastConnectRequestAt < 2500 && link != null && desired == target) return
        lastConnectRequestAt = now
        desired = target
        forceNext = false
        val old = link
        link = null
        ready = false
        generation.incrementAndGet()
        old?.close()
        startLink()
    }

    private fun startLink() {
        if (link != null) return
        val target = desired ?: return
        if (target is Target.Network && (target.host.isBlank() || target.port !in 1..65535)) return
        val current = generation.incrementAndGet()
        val force = forceNext
        forceNext = false
        lateinit var client: LinkClient
        val callbacks = object : LinkClient.Listener {
            override fun onReady(desktopId: String, desktopName: String, token: String?) {
                commands.execute {
                    if (current != generation.get()) return@execute
                    val savedToken = token?.takeIf { it.isNotBlank() } ?: store.pairing(desktopId)?.token.orEmpty()
                    if (savedToken.isBlank()) return@execute
                    ready = true
                    authBlocked = false
                    activeDesktopId = desktopId
                    val previous = store.pairing(desktopId)
                    val wifi = target is Target.Network && target.host != Target.USB_HOST
                    val host = if (wifi) (target as Target.Network).host else previous?.host.orEmpty()
                    val port = if (wifi) (target as Target.Network).port else previous?.port ?: Protocol.LINK_PORT
                    store.save(SavedDesktop(desktopId, desktopName, savedToken, host, port))
                    store.lastTarget = target
                    update {
                        it.copy(
                            connectedId = desktopId,
                            connectedName = desktopName,
                            pairing = false,
                            pairError = "",
                            status = "Conectado a $desktopName por ${target.label}",
                            lastEvent = "Conectado a $desktopName",
                        )
                    }
                    refreshForeground("Conectado a $desktopName")
                    publish()
                    publishShareTarget(desktopId, desktopName)
                    flushOutgoing(client)
                    readBattery()?.let { battery ->
                        try {
                            client.sendBattery(battery.first, battery.second)
                        } catch (_: Exception) {
                        }
                    }
                    try {
                        client.sendPing()
                    } catch (_: Exception) {
                    }
                }
            }

            override fun onPairRequired(name: String) {
                if (current != generation.get()) return
                update {
                    it.copy(
                        pairing = true,
                        pairingName = name,
                        pairError = "",
                        status = "Digite o código mostrado em $name",
                    )
                }
            }

            override fun onPairFailed(message: String) {
                if (current != generation.get()) return
                update { it.copy(pairing = true, pairError = message, status = message) }
            }

            override fun onPairRejected(message: String) {
                commands.execute {
                    if (current != generation.get()) return@execute
                    authBlocked = true
                    update { it.copy(pairing = false, pairError = message, status = message) }
                }
            }

            override fun onAuthFailed(desktopId: String, message: String) {
                commands.execute {
                    if (current != generation.get()) return@execute
                    authBlocked = true
                    store.forget(desktopId)
                    update { it.copy(pairing = false, pairError = message, status = message, connectedId = null) }
                    publish()
                }
            }

            override fun onClipboard(text: String) {
                if (current != generation.get() || !store.clipboardSync) return
                echoGuard = text
                val manager = getSystemService(ClipboardManager::class.java) ?: return
                manager.setPrimaryClip(ClipData.newPlainText("Connect Linux", text))
                update { it.copy(lastEvent = "Área de transferência atualizada pelo computador.") }
            }

            override fun onText(text: String) {
                if (current != generation.get() || text.isBlank()) return
                notifyUser("Texto do computador", text, 3)
                update { it.copy(lastEvent = "Texto recebido do computador.") }
            }

            override fun onOpenUrl(url: String) {
                if (current != generation.get() || !Protocol.isHttpUrl(url)) return
                main.post {
                    try {
                        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url.trim())).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                        update { it.copy(lastEvent = "Link aberto.") }
                    } catch (_: Exception) {
                        update { it.copy(lastEvent = "Não foi possível abrir o link.") }
                    }
                }
            }

            override fun onFind() {
                if (current != generation.get()) return
                vibratePhone()
                notifyUser("Seu computador está chamando", "O Connect Linux pediu para tocar este celular.", 2, "find")
            }

            override fun onBattery(level: Int, charging: Boolean) {
                if (current != generation.get()) return
                val text = if (charging) "Computador $level% · carregando" else "Computador $level%"
                update { it.copy(desktopBattery = text) }
            }

            override fun onLatency(ms: Int) {
                if (current != generation.get()) return
                update { it.copy(latencyMs = ms) }
            }

            override fun onUnpair() {
                commands.execute {
                    if (current != generation.get()) return@execute
                    authBlocked = true
                    userDisconnect = true
                    activeDesktopId?.let { store.forget(it) }
                    update { it.copy(status = "O computador esqueceu este celular.", pairing = false, connectedId = null) }
                    publish()
                }
            }

            override fun onPhotosRequest(offset: Int, limit: Int) {
                if (current != generation.get()) return
                photos.execute {
                    try {
                        val access = PhotoLibrary.access(this@ConnectService)
                        if (access == PhotoLibrary.Access.NONE) {
                            client.sendPhotosError(offset, "permission")
                            askPhotoPermission()
                            return@execute
                        }
                        val page = PhotoLibrary.recent(this@ConnectService, offset, limit, store.photoFolderId)
                        client.sendPhotos(offset, page, access == PhotoLibrary.Access.PARTIAL, store.photoFolderName)
                    } catch (exc: Exception) {
                        try {
                            client.sendPhotosError(offset, "failed")
                        } catch (_: Exception) {
                        }
                    }
                }
            }

            override fun onPhotoRequest(id: Long) {
                if (current != generation.get()) return
                photos.execute {
                    if (!PhotoLibrary.allowed(this@ConnectService)) return@execute
                    val cached = try {
                        Outbox.copy(this@ConnectService, PhotoLibrary.uriFor(id))
                    } catch (exc: Exception) {
                        update { it.copy(lastEvent = exc.message ?: "Não foi possível ler a foto.") }
                        return@execute
                    }
                    sendCached(client, cached, open = true)
                }
            }

            override fun onEvent(text: String) {
                if (current != generation.get()) return
                update { state ->
                    val fileLine = text.startsWith("Recebendo") || text.startsWith("Enviando") || text.startsWith("Arquivo")
                    state.copy(lastEvent = text, fileStatus = if (fileLine) text else state.fileStatus)
                }
            }

            override fun openDownload(name: String, mime: String, gallery: Boolean): FileSink {
                if (current != generation.get()) error("conexão encerrada")
                return IncomingFiles(this@ConnectService).open(name, mime, gallery)
            }

            override fun onPhotosDelete(ids: List<Long>) {
                if (current != generation.get()) return
                photos.execute { deletePhotos(client, ids) }
            }

            override fun onFileSaved(name: String, mime: String, uri: Uri, open: Boolean) {
                if (current != generation.get() || !open) return
                if (!mime.startsWith("image/") && !Protocol.isImageName(name)) return
                showImage(uri, name, ui.connectedName.ifBlank { "computador" })
            }

            override fun onClosed(reason: String) {
                commands.execute {
                    if (current != generation.get() || link !== client) return@execute
                    link = null
                    ready = false
                    activeDesktopId = null
                    update {
                        it.copy(
                            connectedId = null,
                            pairing = false,
                            desktopBattery = "",
                            latencyMs = null,
                            status = reason.ifBlank { "Desconectado" },
                        )
                    }
                    refreshForeground("Procurando computador")
                    publish()
                    if (!userDisconnect && !authBlocked && desired != null) {
                        scheduler.schedule({
                            commands.execute {
                                if (link == null && !userDisconnect && !authBlocked) startLink()
                            }
                        }, 3, TimeUnit.SECONDS)
                    }
                }
            }
        }
        client = LinkClient(
            context = applicationContext,
            target = target,
            deviceId = store.deviceId,
            deviceName = store.deviceName,
            forcePair = force,
            tokenFor = { id -> store.pairing(id)?.token },
            listener = callbacks,
        )
        link = client
        update { it.copy(status = "Conectando por ${target.label}…", pairing = false) }
        Thread({ client.connectAndRun() }, "connectlinux-link").apply { isDaemon = true }.start()
    }

    private fun publish() {
        val now = SystemClock.elapsedRealtime()
        val expired = seen.filterValues { now - it.at > 12_000 }.keys
        expired.forEach { seen.remove(it) }
        val saved = store.pairings().associateBy { it.id }
        val ids = (seen.keys + saved.keys).distinct()
        val devices = ids.map { id ->
            val live = seen[id]
            val known = saved[id]
            FoundDevice(
                id = id,
                name = live?.name ?: known?.name ?: "Computador",
                host = live?.host ?: known?.host ?: "",
                port = live?.port ?: known?.port ?: Protocol.LINK_PORT,
                paired = known != null,
                online = ready && activeDesktopId == id,
            )
        }.sortedWith(compareByDescending<FoundDevice> { it.online }.thenBy { it.name.lowercase() })
        update {
            it.copy(
                devices = devices,
                phoneName = store.deviceName,
                clipboardSync = store.clipboardSync,
                forwardNotifications = store.forwardNotifications,
            )
        }
    }

    private fun update(block: (UiState) -> UiState) {
        val next = synchronized(stateLock) {
            ui = block(ui)
            ui
        }
        main.post { listeners.forEach { listener -> listener(next) } }
    }

    private fun createChannels() {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(NotificationChannel("link", "Conexão", NotificationManager.IMPORTANCE_LOW))
        val find = NotificationChannel("find", "Chamadas do computador", NotificationManager.IMPORTANCE_HIGH)
        find.enableVibration(true)
        manager.createNotificationChannel(find)
        manager.createNotificationChannel(NotificationChannel("messages", "Mensagens do computador", NotificationManager.IMPORTANCE_DEFAULT))
        manager.createNotificationChannel(NotificationChannel("images", "Imagens do computador", NotificationManager.IMPORTANCE_HIGH))
    }

    private fun startAsForeground(text: String) {
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(this, "link")
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("Connect Linux")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
        try {
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
            } else {
                startForeground(1, notification)
            }
        } catch (_: Exception) {
        }
    }

    private fun refreshForeground(text: String) {
        main.post { startAsForeground(text) }
    }

    private fun deletePhotos(client: LinkClient, ids: List<Long>) {
        if (!PhotoLibrary.allowed(this)) {
            sendDeleted(client, ids, false, "O celular não deu acesso às fotos.")
            return
        }
        if (Build.VERSION.SDK_INT < 30) {
            val gone = ids.filter { id ->
                try {
                    contentResolver.delete(PhotoLibrary.uriFor(id), null, null) > 0
                } catch (_: Exception) {
                    false
                }
            }
            val message = if (gone.size == ids.size) "" else "O celular não deixou excluir algumas fotos."
            sendDeleted(client, gone, gone.isNotEmpty(), message)
            return
        }
        val replaced = synchronized(stateLock) {
            pendingDelete.also { pendingDelete = client to ids }
        }
        replaced?.let { sendDeleted(it.first, emptyList(), false, "Pedido substituído por outro.") }
        val confirm = Intent(this, DeletePhotosActivity::class.java)
            .putExtra(DeletePhotosActivity.EXTRA_IDS, ids.toLongArray())
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val tap = PendingIntent.getActivity(this, 7, confirm, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val count = if (ids.size == 1) "1 foto" else "${ids.size} fotos"
        val body = "O computador pediu para excluir $count. Toque para confirmar."
        val notification = NotificationCompat.Builder(this, "messages")
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("Excluir fotos do celular")
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(tap)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .build()
        getSystemService(NotificationManager::class.java)?.notify(DELETE_NOTIFICATION, notification)
        if (Settings.canDrawOverlays(this)) {
            main.post {
                try {
                    startActivity(confirm)
                } catch (_: Exception) {
                }
            }
        }
    }

    fun finishPhotoDelete(ids: List<Long>, ok: Boolean) {
        val pending = synchronized(stateLock) {
            pendingDelete?.takeIf { it.second == ids }.also { if (it != null) pendingDelete = null }
        } ?: return
        io.execute {
            val message = if (ok) "" else "A exclusão foi cancelada no celular."
            sendDeleted(pending.first, if (ok) ids else emptyList(), ok, message)
        }
    }

    private fun sendDeleted(client: LinkClient, ids: List<Long>, ok: Boolean, message: String) {
        try {
            client.sendPhotosDeleted(ids, ok, message)
        } catch (_: Exception) {
        }
        if (ok && ids.isNotEmpty()) {
            val count = if (ids.size == 1) "1 foto excluída" else "${ids.size} fotos excluídas"
            update { it.copy(lastEvent = "$count pelo computador.") }
        }
    }

    private fun askPhotoPermission() {
        val open = PendingIntent.getActivity(
            this,
            6,
            Intent(this, MainActivity::class.java)
                .putExtra(EXTRA_ASK_PHOTOS, true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val body = "O computador quer mostrar suas fotos recentes. Toque para permitir."
        val notification = NotificationCompat.Builder(this, "messages")
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("Permitir acesso às fotos")
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        getSystemService(NotificationManager::class.java)?.notify(6, notification)
    }

    private fun notifyUser(title: String, body: String, id: Int, channel: String = "messages") {
        val notification = NotificationCompat.Builder(this, channel)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .build()
        getSystemService(NotificationManager::class.java)?.notify(id, notification)
    }

    @Suppress("DEPRECATION")
    private fun readBattery(): Pair<Int, Boolean>? {
        val manager = getSystemService(BatteryManager::class.java) ?: return null
        val level = manager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        if (level !in 0..100) return null
        val sticky = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val status = sticky?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val charging = status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL
        return level to charging
    }

    private fun vibratePhone() {
        val vibrator = if (Build.VERSION.SDK_INT >= 31) {
            getSystemService(VibratorManager::class.java)?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            getSystemService(Vibrator::class.java)
        } ?: return
        vibrator.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 500, 200, 500, 200, 800), -1))
    }

    private data class Seen(val id: String, val name: String, val host: String, val port: Int, val at: Long)

    companion object {
        const val ACTION_SEND_FILES = "dev.connectlinux.app.action.SEND_FILES"
        const val EXTRA_PATHS = "dev.connectlinux.app.extra.PATHS"
        const val EXTRA_NAMES = "dev.connectlinux.app.extra.NAMES"
        const val EXTRA_MIMES = "dev.connectlinux.app.extra.MIMES"
        const val EXTRA_OPEN = "dev.connectlinux.app.extra.OPEN"
        const val EXTRA_ASK_PHOTOS = "dev.connectlinux.app.extra.ASK_PHOTOS"
        const val DELETE_NOTIFICATION = 7
        const val SHARE_CATEGORY = "dev.connectlinux.app.category.COMPUTER"

        @Volatile
        var instance: ConnectService? = null
            private set
    }
}

private const val STOP_FOREGROUND_REMOVE = Service.STOP_FOREGROUND_REMOVE
