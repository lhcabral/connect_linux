"""Ícone do Connect Linux na bandeja do Linux Mint, ao lado do relógio."""

from __future__ import annotations

import ctypes
import logging
import struct
import threading
import zlib
from pathlib import Path

log = logging.getLogger("connectlinux")


class TrayIcon:
    """Fica visível o tempo todo. O botão direito abre o menu."""

    def __init__(
        self,
        on_present,
        on_connect,
        on_minimize,
        on_maximize,
        on_quit,
    ) -> None:
        self._on_present = on_present
        self._on_connect = on_connect
        self._on_minimize = on_minimize
        self._on_maximize = on_maximize
        self._on_quit = on_quit
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop = None
        self._idle = None
        self._icon = None
        self._menu = None
        self._gtk_status = None
        self._tooltip = ""
        self.error: str | None = None
        self.supported = False

    def start(self) -> bool:
        _enable_x11_threads()
        self._thread = threading.Thread(target=self._run, name="connectlinux-tray", daemon=True)
        self._thread.start()
        self._ready.wait(4)
        if self.error or not self.supported:
            self.stop()
            return False
        return True

    def stop(self) -> None:
        idle = self._idle
        loop = self._loop

        def _quit_loop() -> bool:
            if loop is not None and loop.is_running():
                loop.quit()
            return False

        if idle is not None:
            idle(_quit_loop)
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)

    def set_tooltip(self, text: str) -> None:
        if text == self._tooltip:
            return
        self._tooltip = text
        self._invoke(lambda: self._apply_tooltip(text))

    def _invoke(self, fn) -> None:
        idle = self._idle

        def wrapper() -> bool:
            try:
                fn()
            except Exception:
                log.exception("bandeja")
            return False

        if idle is not None:
            idle(wrapper)

    def _run(self) -> None:
        try:
            self._setup()
        except Exception as exc:
            self.error = str(exc)
            log.warning("Ícone ao lado do relógio indisponível: %s", exc)
        if self._loop is not None and self.error is None:
            self._loop.run()
        if not self._ready.is_set():
            self._ready.set()

    def _setup(self) -> None:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib, Gtk

        if not Gtk.init_check([]):
            raise RuntimeError("o ambiente gráfico não está disponível")

        self._idle = GLib.idle_add
        self._loop = GLib.MainLoop()
        icon_file = _icon_file()
        menu = self._build_menu(Gtk)
        self._menu = menu

        if self._try_xapp(menu, icon_file):
            GLib.timeout_add(1200, self._finish_wait)
            return
        if self._try_ayatana(menu, icon_file):
            self.supported = True
            self._ready.set()
            return
        self._try_gtk_status(Gtk, menu, icon_file)
        self.supported = True
        self._ready.set()

    def _build_menu(self, gtk):
        menu = gtk.Menu()
        entries = (
            ("Conectar", self._on_connect),
            ("Minimizar", self._on_minimize),
            ("Maximizar", self._on_maximize),
            ("Sair", self._on_quit),
        )
        for label, callback in entries[:-1]:
            item = gtk.MenuItem.new_with_label(label)
            item.connect("activate", lambda _item, action=callback: action())
            menu.append(item)
        menu.append(gtk.SeparatorMenuItem())
        leave = gtk.MenuItem.new_with_label(entries[-1][0])
        leave.connect("activate", lambda _item, action=entries[-1][1]: action())
        menu.append(leave)
        menu.show_all()
        return menu

    def _try_xapp(self, menu, icon_file: str) -> bool:
        try:
            import gi

            gi.require_version("XApp", "1.0")
            from gi.repository import XApp
        except (ImportError, ValueError):
            return False
        icon = XApp.StatusIcon.new()
        icon.set_name("connectlinux")
        icon.set_icon_name(icon_file)
        icon.set_tooltip_text("Connect Linux")
        icon.set_visible(True)
        icon.set_secondary_menu(menu)
        icon.connect("activate", lambda *_args: self._on_present())
        icon.connect("state-changed", self._on_xapp_state)
        self._icon = icon
        state = icon.get_state()
        if state != XApp.StatusIconState.NO_SUPPORT:
            self.supported = True
            self._ready.set()
        return True

    def _on_xapp_state(self, icon, state) -> None:
        import gi

        gi.require_version("XApp", "1.0")
        from gi.repository import XApp

        self.supported = state != XApp.StatusIconState.NO_SUPPORT
        if self.supported:
            self._ready.set()

    def _finish_wait(self) -> bool:
        icon = self._icon
        if icon is not None and hasattr(icon, "get_state"):
            import gi

            gi.require_version("XApp", "1.0")
            from gi.repository import XApp

            self.supported = icon.get_state() != XApp.StatusIconState.NO_SUPPORT
        if not self._ready.is_set():
            self._ready.set()
        return False

    def _try_ayatana(self, menu, icon_file: str) -> bool:
        try:
            import gi

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
        indicator.set_menu(menu)
        self._icon = indicator
        return True

    def _try_gtk_status(self, gtk, menu, icon_file: str) -> None:
        status = gtk.StatusIcon.new_from_file(icon_file)
        status.set_tooltip_text("Connect Linux")
        status.set_visible(True)
        status.connect("activate", lambda *_args: self._on_present())
        status.connect("popup-menu", lambda _icon, button, when: self._popup_gtk(menu, status, button, when))
        self._gtk_status = status

    def _popup_gtk(self, menu, status, button, when) -> None:
        menu.popup(None, None, status.position_menu, status, button, when)

    def _apply_tooltip(self, text: str) -> None:
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        icon = self._icon
        if icon is not None and hasattr(icon, "set_tooltip_text"):
            icon.set_tooltip_text(safe)
            return
        if self._gtk_status is not None:
            self._gtk_status.set_tooltip_text(text)


def _enable_x11_threads() -> None:
    try:
        ctypes.CDLL("libX11.so.6").XInitThreads()
    except OSError:
        pass


def _icon_file() -> str:
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
