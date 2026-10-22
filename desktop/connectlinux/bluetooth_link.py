"""Serviço Bluetooth (RFCOMM) registrado no BlueZ.

O BlueZ anuncia o serviço e entrega cada conexão recebida como um socket já
aberto. O celular precisa estar pareado com o computador no Bluetooth do sistema.
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Callable

from .protocol import BLUETOOTH_CHANNEL, BLUETOOTH_UUID

log = logging.getLogger("connectlinux")

_PROFILE_PATH = "/dev/connectlinux/profile"


class BluetoothLink:
    def __init__(self, on_socket: Callable[[socket.socket, str], None]) -> None:
        self._on_socket = on_socket
        self._loop = None
        self._bus = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.registered = False
        self.error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="connectlinux-bt", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def stop(self) -> None:
        loop = self._loop
        if loop is not None:
            loop.quit()

    def status(self) -> dict:
        info = {"available": False, "powered": False, "registered": self.registered, "name": "", "error": self.error}
        adapter = self._adapter_props()
        if adapter is None:
            return info
        info["available"] = True
        info["powered"] = bool(adapter.get("Powered", False))
        info["name"] = str(adapter.get("Alias", ""))
        return info

    def power_on_and_show(self) -> str | None:
        """Liga o adaptador e deixa o computador visível por 3 minutos."""
        try:
            import dbus

            bus = self._bus or dbus.SystemBus()
            path = self._adapter_path(bus)
            if path is None:
                return "Nenhum adaptador Bluetooth encontrado."
            props = dbus.Interface(bus.get_object("org.bluez", path), "org.freedesktop.DBus.Properties")
            props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
            props.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(180))
            props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(True))
            props.Set("org.bluez.Adapter1", "PairableTimeout", dbus.UInt32(180))
            props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(True))
        except Exception as exc:
            return f"Não foi possível ligar o Bluetooth: {_dbus_message(exc)}"
        return None

    def _run(self) -> None:
        try:
            import dbus
            import dbus.mainloop.glib
            import dbus.service
            from gi.repository import GLib
        except ImportError:
            self.error = "Instale python3-dbus e python3-gi para usar o Bluetooth."
            self._ready.set()
            return

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        try:
            bus = dbus.SystemBus()
        except Exception as exc:
            self.error = f"Bluetooth indisponível: {_dbus_message(exc)}"
            self._ready.set()
            return
        self._bus = bus
        on_socket = self._on_socket

        class Profile(dbus.service.Object):
            @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
            def Release(self) -> None:
                log.info("BlueZ liberou o perfil Connect Linux.")

            @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
            def NewConnection(self, device, fd, _properties) -> None:
                raw = fd.take()
                try:
                    sock = socket.socket(fileno=raw)
                    sock.setblocking(False)
                except OSError as exc:
                    log.warning("conexão Bluetooth inválida: %s", exc)
                    return
                on_socket(sock, _device_address(str(device)))

            @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
            def RequestDisconnection(self, _device) -> None:
                return

        try:
            Profile(bus, _PROFILE_PATH)
            manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1")
            manager.RegisterProfile(
                _PROFILE_PATH,
                BLUETOOTH_UUID,
                {
                    "Name": dbus.String("Connect Linux"),
                    "Role": dbus.String("server"),
                    "Channel": dbus.UInt16(BLUETOOTH_CHANNEL),
                    "RequireAuthentication": dbus.Boolean(True),
                    "RequireAuthorization": dbus.Boolean(False),
                    "AutoConnect": dbus.Boolean(False),
                },
            )
        except Exception as exc:
            self.error = f"Não foi possível registrar o Bluetooth: {_dbus_message(exc)}"
            log.warning("%s", self.error)
            self._ready.set()
            return

        self.registered = True
        self.error = None
        log.info("Serviço Bluetooth registrado no canal %s.", BLUETOOTH_CHANNEL)
        self._loop = GLib.MainLoop()
        self._ready.set()
        try:
            self._loop.run()
        finally:
            self.registered = False
            try:
                manager.UnregisterProfile(_PROFILE_PATH)
            except Exception:
                pass

    def _adapter_props(self) -> dict | None:
        try:
            import dbus

            bus = self._bus or dbus.SystemBus()
            path = self._adapter_path(bus)
            if path is None:
                return None
            props = dbus.Interface(bus.get_object("org.bluez", path), "org.freedesktop.DBus.Properties")
            return dict(props.GetAll("org.bluez.Adapter1"))
        except Exception:
            return None

    @staticmethod
    def _adapter_path(bus) -> str | None:
        import dbus

        manager = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
        for path, interfaces in manager.GetManagedObjects().items():
            if "org.bluez.Adapter1" in interfaces:
                return str(path)
        return None


def _device_address(path: str) -> str:
    tail = path.rsplit("/", 1)[-1]
    if tail.startswith("dev_"):
        return tail[4:].replace("_", ":")
    return "Bluetooth"


def _dbus_message(exc: Exception) -> str:
    getter = getattr(exc, "get_dbus_message", None)
    if callable(getter):
        message = getter()
        if message:
            return str(message)
    return str(exc)
