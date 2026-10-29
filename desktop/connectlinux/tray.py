"""Ícone do Connect Linux na bandeja do Linux Mint, ao lado do relógio.

Roda no laço principal do GTK, na mesma thread da janela.
"""

from __future__ import annotations

import logging
import struct
import zlib
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

log = logging.getLogger("connectlinux")

XAPP_WAIT_MS = 1200


class TrayIcon:
    """Fica visível o tempo todo. O botão direito abre o menu."""

    def __init__(self, on_present, on_connect, on_minimize, on_maximize, on_quit, on_ready) -> None:
        self._on_present = on_present
        self._on_connect = on_connect
        self._on_minimize = on_minimize
        self._on_maximize = on_maximize
        self._on_quit = on_quit
        self._on_ready = on_ready
        self._icon = None
        self._gtk_status = None
        self._menu = None
        self._tooltip = ""
        self._decided = False
        self.supported = False

    def start(self) -> None:
        """Mostra o ícone. `on_ready(suportado)` é chamado uma vez, logo ou em até XAPP_WAIT_MS."""
        try:
            icon_file = icon_path()
            self._menu = self._build_menu()
            if self._try_xapp(icon_file):
                GLib.timeout_add(XAPP_WAIT_MS, self._finish_wait)
                return
            if not self._try_ayatana(icon_file):
                self._try_gtk_status(icon_file)
            self._decide(True)
        except Exception as exc:
            log.warning("Ícone ao lado do relógio indisponível: %s", exc)
            self._decide(False)

    def stop(self) -> None:
        icon = self._icon
        self._icon = None
        if icon is not None and hasattr(icon, "set_visible"):
            icon.set_visible(False)
        elif icon is not None:
            from gi.repository import AyatanaAppIndicator3

            icon.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        if self._gtk_status is not None:
            self._gtk_status.set_visible(False)
            self._gtk_status = None

    def set_tooltip(self, text: str) -> None:
        if text == self._tooltip:
            return
        self._tooltip = text
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        icon = self._icon
        if icon is not None and hasattr(icon, "set_tooltip_text"):
            icon.set_tooltip_text(safe)
        elif self._gtk_status is not None:
            self._gtk_status.set_tooltip_text(text)

    def _decide(self, supported: bool) -> None:
        if self._decided:
            return
        self._decided = True
        self.supported = supported
        self._on_ready(supported)

    def _build_menu(self):
        menu = Gtk.Menu()
        entries = (
            ("Abrir o Connect Linux", self._on_present),
            ("Conectar", self._on_connect),
            ("Minimizar", self._on_minimize),
            ("Maximizar", self._on_maximize),
        )
        for label, callback in entries:
            item = Gtk.MenuItem.new_with_label(label)
            item.connect("activate", lambda _item, action=callback: action())
            menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        leave = Gtk.MenuItem.new_with_label("Sair")
        leave.connect("activate", lambda _item: self._on_quit())
        menu.append(leave)
        menu.show_all()
        return menu

    def _try_xapp(self, icon_file: str) -> bool:
        try:
            gi.require_version("XApp", "1.0")
            from gi.repository import XApp
        except (ImportError, ValueError):
            return False
        icon = XApp.StatusIcon.new()
        icon.set_name("connectlinux")
        icon.set_icon_name(icon_file)
        icon.set_tooltip_text("Connect Linux")
        icon.set_visible(True)
        icon.set_secondary_menu(self._menu)
        icon.connect("activate", lambda *_args: self._on_present())
        icon.connect("state-changed", lambda _icon, state: self._xapp_state(state))
        self._icon = icon
        self._xapp_state(icon.get_state())
        return True

    def _xapp_state(self, state) -> None:
        from gi.repository import XApp

        if state != XApp.StatusIconState.NO_SUPPORT:
            self._decide(True)

    def _finish_wait(self) -> bool:
        if not self._decided:
            self._decide(False)
        return False

    def _try_ayatana(self, icon_file: str) -> bool:
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3
        except (ImportError, ValueError):
            return False
        indicator = AyatanaAppIndicator3.Indicator.new(
            "connectlinux",
            icon_file,
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        indicator.set_title("Connect Linux")
        indicator.set_menu(self._menu)
        self._icon = indicator
        return True

    def _try_gtk_status(self, icon_file: str) -> None:
        status = Gtk.StatusIcon.new_from_file(icon_file)
        status.set_tooltip_text("Connect Linux")
        status.set_visible(True)
        status.connect("activate", lambda *_args: self._on_present())
        status.connect(
            "popup-menu",
            lambda _icon, button, when: self._menu.popup(None, None, status.position_menu, status, button, when),
        )
        self._gtk_status = status


def icon_path() -> str:
    for candidate in (
        Path("/usr/share/icons/hicolor/128x128/apps/connectlinux.png"),
        Path(__file__).resolve().parents[1] / "icon.png",
    ):
        if candidate.is_file():
            return str(candidate)
    path = Path.home() / ".cache" / "connectlinux" / "tray-icon.png"
    if not path.is_file():
        _write_icon(path)
    return str(path)


def _write_icon(path: Path) -> None:
    size = 64
    green = (0x2F, 0x6B, 0x2C, 255)
    light = (0xE7, 0xF5, 0xDF, 255)
    clear = (0, 0, 0, 0)
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            raw.extend(_pixel(x, y, green, light, clear))
    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def _pixel(x: int, y: int, green: tuple, light: tuple, clear: tuple) -> tuple:
    if not (6 <= x < 58 and 4 <= y < 60):
        return clear
    if (x < 12 or x >= 52) and (y < 10 or y >= 54):
        return clear
    if 16 <= x < 48 and 12 <= y < 42:
        return light
    if 28 <= x < 36 and 48 <= y < 54:
        return light
    return green


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
