package dev.connectlinux.app

data class FoundDevice(
    val id: String,
    val name: String,
    val host: String,
    val port: Int,
    val paired: Boolean,
    val online: Boolean,
)

data class UiState(
    val status: String = "Procurando computadores na rede…",
    val devices: List<FoundDevice> = emptyList(),
    val phoneName: String = "",
    val connectedId: String? = null,
    val connectedName: String = "",
    val desktopBattery: String = "",
    val latencyMs: Int? = null,
    val pairing: Boolean = false,
    val pairingName: String = "",
    val pairError: String = "",
    val clipboardSync: Boolean = true,
    val forwardNotifications: Boolean = true,
    val fileStatus: String = "",
    val lastEvent: String = "",
)
