"""Janela do Connect Linux em GTK 3. Segue o tema do sistema, claro ou escuro."""

from __future__ import annotations

import shutil
import signal
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib  # noqa: E402

# Antes de importar o Gtk, que já inicia o GDK e fixa a classe da janela.
GLib.set_prgname("connectlinux")
GLib.set_application_name("Connect Linux")

from gi.repository import Gdk, GdkPixbuf, Gtk, Pango  # noqa: E402

from . import __version__, autostart, pix, updater  # noqa: E402
from .app import App  # noqa: E402
from .system_bridge import notify  # noqa: E402
from .tray import TrayIcon, icon_path  # noqa: E402

UPDATE_CHECK_MS = 6 * 60 * 60 * 1000
GITHUB_URL = "https://github.com/lhcabral/connect_linux"
LOG_LINES = 300

CSS = b"""
.cl-card {
  background-color: @theme_base_color;
  border: 1px solid @borders;
  border-radius: 10px;
}
.cl-card list, .cl-card row { background-color: transparent; }
.cl-card row:selected { background-color: alpha(@theme_selected_bg_color, 0.25); color: @theme_fg_color; }
.cl-card row:selected label, .cl-card row:selected image { color: @theme_fg_color; }
.cl-section { font-weight: bold; }
.cl-hero { font-size: 18pt; font-weight: bold; }
.cl-strong { font-weight: bold; }
.cl-dim { opacity: 0.7; }
.cl-code { font-size: 30pt; font-weight: bold; letter-spacing: 6px; }
.cl-banner {
  background-color: alpha(@theme_selected_bg_color, 0.14);
  border: 1px solid alpha(@theme_selected_bg_color, 0.55);
  border-radius: 10px;
}
.cl-online, .cl-card row:selected label.cl-online { color: #3BB54A; }
.cl-seen, .cl-card row:selected label.cl-seen { color: #E5A50A; }
.cl-offline { opacity: 0.35; }
.cl-qr { background-color: #FFFFFF; border-radius: 8px; }
"""


def available() -> bool:
    try:
        result = Gtk.init_check([])
    except Exception:
        return False
    return bool(result[0] if isinstance(result, tuple) else result)


class DesktopWindow:
    def __init__(self, app: App, start_hidden: bool) -> None:
        self.app = app
        self._alive = True
        self._start_hidden = start_hidden
        self._told_hide = False
        self._tray_ok = False
        self._update: dict | None = None
        self._updating = False
        self._rows: list[dict] = []
        self._phone_rows: dict[str, dict] = {}
        self._codes: dict[str, tuple[str, str]] = {}
        self._help: Gtk.Window | None = None

        _load_css()
        self.window = Gtk.Window(title="Connect Linux")
        self.window.set_default_size(920, 680)
        self.window.set_size_request(720, 540)
        try:
            self.window.set_icon_from_file(icon_path())
        except GLib.Error:
            pass
        self.window.connect("delete-event", self._on_close)
        self._build()
        self.window.get_child().show_all()
        self.pair_revealer.set_reveal_child(False)
        self.update_button.hide()

        self._tray = TrayIcon(
            on_present=self._present,
            on_connect=self._connect_phone,
            on_minimize=self._minimize,
            on_maximize=self._maximize,
            on_quit=self._quit,
            on_ready=self._tray_ready,
        )
        if not start_hidden:
            self.window.show_all()
            self.update_button.set_visible(self._update is not None and not self._updating)
        self._tray.start()

        self.refresh()
        GLib.timeout_add(400, self.refresh)
        GLib.timeout_add(1500, self._first_update_check)

    # ---------- montagem ----------

    def _build(self) -> None:
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        self.window.set_titlebar(header)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.stack)
        header.set_custom_title(switcher)
        self.window.set_title("Connect Linux")

        logo = _logo(24)
        if logo is not None:
            header.pack_start(logo)

        menu_button = Gtk.MenuButton()
        menu_button.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        menu_button.set_tooltip_text("Menu")
        menu = Gtk.Menu()
        self.version_item = Gtk.MenuItem.new_with_label(f"Versão {__version__}")
        self.version_item.set_sensitive(False)
        menu.append(self.version_item)
        menu.append(Gtk.SeparatorMenuItem())
        for label, action in (
            ("Ajuda e doação", self._show_help),
            ("Abrir pasta de recebidos", self._open_folder),
            ("Fechar janela", self._close_window),
        ):
            item = Gtk.MenuItem.new_with_label(label)
            item.connect("activate", lambda _item, fn=action: fn())
            menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        leave = Gtk.MenuItem.new_with_label("Sair do Connect Linux")
        leave.connect("activate", lambda _item: self._quit())
        menu.append(leave)
        menu.show_all()
        menu_button.set_popup(menu)
        header.pack_end(menu_button)

        help_button = Gtk.Button.new_from_icon_name("help-about-symbolic", Gtk.IconSize.BUTTON)
        help_button.set_tooltip_text("Ajuda")
        help_button.connect("clicked", lambda _b: self._show_help())
        header.pack_end(help_button)

        self.update_button = Gtk.Button(label="Atualizar")
        self.update_button.get_style_context().add_class("suggested-action")
        self.update_button.connect("clicked", lambda _b: self._start_update())
        header.pack_end(self.update_button)

        self.stack.add_titled(self._build_phones_page(), "phones", "Celulares")
        self.stack.add_titled(self._build_settings_page(), "settings", "Ajustes")
        self.stack.add_titled(self._build_activity_page(), "activity", "Atividade")
        self.window.add(self.stack)

    def _build_phones_page(self) -> Gtk.Widget:
        page = _page()

        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.hero_icon = Gtk.Image.new_from_icon_name("phone-symbolic", Gtk.IconSize.DIALOG)
        self.hero_icon.set_pixel_size(48)
        hero.pack_start(self.hero_icon, False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        texts.set_valign(Gtk.Align.CENTER)
        self.hero_title = _label("Iniciando…", "cl-hero")
        self.hero_subtitle = _label("", "cl-dim", wrap=True)
        texts.pack_start(self.hero_title, False, False, 0)
        texts.pack_start(self.hero_subtitle, False, False, 0)
        hero.pack_start(texts, True, True, 0)
        self.connect_button = Gtk.Button(label="Conectar")
        self.connect_button.get_style_context().add_class("suggested-action")
        self.connect_button.set_valign(Gtk.Align.CENTER)
        self.connect_button.connect("clicked", lambda _b: self._connect_phone())
        hero.pack_end(self.connect_button, False, False, 0)
        page.pack_start(hero, False, False, 0)

        self.pair_revealer = Gtk.Revealer()
        banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        banner.get_style_context().add_class("cl-banner")
        banner.set_border_width(0)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        inner.set_margin_top(14)
        inner.set_margin_bottom(14)
        inner.set_margin_start(18)
        self.pair_title = _label("", "cl-strong", wrap=True)
        self.pair_code = _label("", "cl-code")
        self.pair_code.set_selectable(True)
        inner.pack_start(self.pair_title, False, False, 0)
        inner.pack_start(self.pair_code, False, False, 0)
        banner.pack_start(inner, True, True, 0)
        refuse = Gtk.Button(label="Recusar")
        refuse.set_valign(Gtk.Align.CENTER)
        refuse.set_margin_end(18)
        refuse.connect("clicked", lambda _b: self.app.reject_pair())
        banner.pack_end(refuse, False, False, 0)
        self.pair_revealer.add(banner)
        page.pack_start(self.pair_revealer, False, False, 0)

        page.pack_start(_label("Celulares", "cl-section"), False, False, 0)
        self.phone_list = Gtk.ListBox()
        self.phone_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.phone_list.set_header_func(_separator_header)
        self.phone_list.connect("row-selected", lambda *_args: self._update_actions())
        placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        placeholder.set_border_width(28)
        placeholder.pack_start(_label("Nenhum celular por perto", "cl-strong", center=True), False, False, 0)
        placeholder.pack_start(
            _label(
                "Abra o Connect Linux no celular, na mesma rede Wi-Fi deste computador.",
                "cl-dim",
                wrap=True,
                center=True,
            ),
            False,
            False,
            0,
        )
        placeholder.show_all()
        self.phone_list.set_placeholder(placeholder)
        page.pack_start(_card(self.phone_list), False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.action_buttons = []
        for icon, label, handler in (
            ("document-send-symbolic", "Enviar arquivo", self._send_file),
            ("insert-text-symbolic", "Enviar texto", self._send_text),
            ("web-browser-symbolic", "Abrir link", self._send_url),
            ("audio-volume-high-symbolic", "Chamar", self._find),
        ):
            button = _icon_button(icon, label)
            button.connect("clicked", lambda _b, fn=handler: fn())
            actions.pack_start(button, False, False, 0)
            self.action_buttons.append(button)
        self.forget_button = _icon_button("user-trash-symbolic", "Esquecer")
        self.forget_button.connect("clicked", lambda _b: self._forget())
        actions.pack_end(self.forget_button, False, False, 0)
        page.pack_start(actions, False, False, 0)
        self.action_hint = _label("", "cl-dim")
        page.pack_start(self.action_hint, False, False, 0)
        return _scrolled(page)

    def _build_settings_page(self) -> Gtk.Widget:
        page = _page()

        page.pack_start(_label("Este computador", "cl-section"), False, False, 0)
        computer = _settings_list()
        self.name_entry = Gtk.Entry()
        self.name_entry.set_text(self.app.store.name)
        self.name_entry.set_width_chars(26)
        self.name_entry.set_valign(Gtk.Align.CENTER)
        self.name_entry.connect("activate", self._save_name)
        self.name_entry.connect("focus-out-event", self._save_name)
        computer.add(_setting_row("computer-symbolic", "Nome", "Como o celular vê este computador", self.name_entry))
        folder_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        folder_box.set_valign(Gtk.Align.CENTER)
        open_folder = Gtk.Button(label="Abrir")
        open_folder.connect("clicked", lambda _b: self._open_folder())
        change_folder = Gtk.Button(label="Mudar…")
        change_folder.connect("clicked", lambda _b: self._change_folder())
        folder_box.pack_start(open_folder, False, False, 0)
        folder_box.pack_start(change_folder, False, False, 0)
        folder_row = _setting_row("folder-symbolic", "Arquivos recebidos", str(self.app.store.download_dir), folder_box)
        self.folder_label = folder_row.subtitle
        computer.add(folder_row)
        page.pack_start(_card(computer), False, False, 0)

        page.pack_start(_label("Funcionamento", "cl-section"), False, False, 0)
        behavior = _settings_list()
        self.clip_switch = _switch(self.app.store.clipboard_sync, self._toggle_clip)
        behavior.add(
            _setting_row(
                "edit-paste-symbolic",
                "Sincronizar área de transferência",
                "O que você copia aqui aparece no celular, e vice-versa",
                self.clip_switch,
            )
        )
        self.note_switch = _switch(self.app.store.show_notifications, self._toggle_notes)
        behavior.add(
            _setting_row(
                "dialog-information-symbolic",
                "Mostrar notificações do celular",
                "As notificações do Android aparecem neste computador",
                self.note_switch,
            )
        )
        self.autostart_switch = _switch(autostart.is_enabled(), self._toggle_autostart)
        behavior.add(
            _setting_row(
                "emblem-system-symbolic",
                "Iniciar com o Linux",
                "Fica ao lado do relógio desde o login, sem abrir esta janela",
                self.autostart_switch,
            )
        )
        page.pack_start(_card(behavior), False, False, 0)

        page.pack_start(_label("Conexões", "cl-section"), False, False, 0)
        links = _settings_list()
        wifi = _setting_row("network-wireless-symbolic", "Wi-Fi", "Verificando…", None)
        self.wifi_label = wifi.subtitle
        links.add(wifi)
        visible = Gtk.Button(label="Ficar visível")
        visible.set_valign(Gtk.Align.CENTER)
        visible.set_tooltip_text("Liga o Bluetooth e deixa o computador visível por 3 minutos")
        visible.connect("clicked", lambda _b: self.app.enable_bluetooth())
        bluetooth = _setting_row("bluetooth-active-symbolic", "Bluetooth", "Verificando…", visible)
        self.bt_label = bluetooth.subtitle
        links.add(bluetooth)
        usb = _setting_row("media-removable-symbolic", "Cabo USB", "Verificando…", None)
        self.usb_label = usb.subtitle
        links.add(usb)
        page.pack_start(_card(links), False, False, 0)
        return _scrolled(page)

    def _build_activity_page(self) -> Gtk.Widget:
        page = _page()
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        top.pack_start(_label("Atividade recente", "cl-section"), False, False, 0)
        clear = Gtk.Button(label="Limpar")
        clear.connect("clicked", lambda _b: self.log_buffer.set_text(""))
        top.pack_end(clear, False, False, 0)
        page.pack_start(top, False, False, 0)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_cursor_visible(False)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        for side in ("left", "right", "top", "bottom"):
            getattr(self.log_view, f"set_{side}_margin")(12)
        self.log_buffer = self.log_view.get_buffer()
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.log_view)
        card = _card(scroll)
        page.pack_start(card, True, True, 0)
        return page

    # ---------- atualização periódica ----------

    def refresh(self) -> bool:
        if not self._alive:
            return False
        for kind, payload in self.app.drain_events():
            if kind == "present":
                self._present()
            elif kind == "log":
                self._append_log(str(payload.get("text", "")))
            elif kind == "pair":
                conn_id = str(payload.get("conn_id", ""))
                code = payload.get("code")
                if code:
                    self._codes[conn_id] = (str(payload.get("name", "Celular")), str(code))
                    self._present()
                else:
                    self._codes.pop(conn_id, None)
                self._render_pair()
        self._rows = self.app.snapshot()
        self._render_phones()
        self._render_hero()
        self._render_links()
        self._tray.set_tooltip(self._tray_text())
        return True

    def _render_hero(self) -> None:
        online = [row for row in self._rows if row.get("online")]
        paired = [row for row in self._rows if row.get("paired")]
        ips = ", ".join(self.app.local_ips()) or "IP não identificado"
        if self.app.fatal:
            title, subtitle, icon = "Algo deu errado", self.app.fatal, "dialog-warning-symbolic"
        elif online:
            names = ", ".join(row["name"] for row in online)
            title = f"Conectado a {names}"
            subtitle = "Envie arquivos, textos e links pelos botões abaixo."
            icon = "emblem-ok-symbolic"
        elif paired:
            title = "Celular fora do ar"
            subtitle = f"Abra o Connect Linux no celular ou clique em Conectar. IP deste computador: {ips}"
            icon = "phone-symbolic"
        else:
            title = "Aguardando o celular"
            subtitle = (
                "Abra o Connect Linux no celular, na mesma rede Wi-Fi. "
                f"Se ele não achar o computador, digite este IP: {ips}"
            )
            icon = "phone-symbolic"
        _set_text(self.hero_title, title)
        _set_text(self.hero_subtitle, subtitle)
        if self.hero_icon.get_icon_name()[0] != icon:
            self.hero_icon.set_from_icon_name(icon, Gtk.IconSize.DIALOG)
        self.connect_button.set_visible(bool(paired) and not online)

    def _render_pair(self) -> None:
        if not self._codes:
            self.pair_revealer.set_reveal_child(False)
            return
        names = ", ".join(name for name, _code in self._codes.values())
        self.pair_title.set_text(f"Digite este código no celular ({names})")
        self.pair_code.set_text("   ".join(code for _name, code in self._codes.values()))
        self.pair_revealer.set_reveal_child(True)

    def _render_phones(self) -> None:
        seen = set()
        for row in self._rows:
            device_id = row["id"]
            seen.add(device_id)
            widgets = self._phone_rows.get(device_id)
            if widgets is None:
                widgets = _phone_row(device_id)
                self._phone_rows[device_id] = widgets
                self.phone_list.add(widgets["row"])
                widgets["row"].show_all()
            _fill_phone_row(widgets, row)
        for device_id in list(self._phone_rows):
            if device_id not in seen:
                self.phone_list.remove(self._phone_rows.pop(device_id)["row"])
        if self.phone_list.get_selected_row() is None:
            online = [row["id"] for row in self._rows if row.get("online")]
            if len(online) == 1:
                self.phone_list.select_row(self._phone_rows[online[0]]["row"])
        self._update_actions()

    def _update_actions(self) -> None:
        row = self._selected()
        online = bool(row and row.get("online"))
        for button in self.action_buttons:
            button.set_sensitive(online)
        self.forget_button.set_sensitive(row is not None)
        if row is None:
            hint = "Selecione um celular na lista." if self._rows else ""
        elif online:
            hint = f"Os botões acima enviam para {row['name']}."
        else:
            hint = f"{row['name']} não está conectado."
        _set_text(self.action_hint, hint)

    def _render_links(self) -> None:
        _set_text(self.wifi_label, f"Porta {self.app.port} · {', '.join(self.app.local_ips()) or 'IP não identificado'}")
        bt = self.app.bluetooth_status()
        if not bt:
            text = "Verificando…"
        elif bt.get("error"):
            text = "Indisponível"
        elif not bt.get("available"):
            text = "Nenhum adaptador encontrado"
        elif not bt.get("powered"):
            text = "Desligado"
        else:
            text = f"Pronto ({bt.get('name') or 'este computador'})"
        _set_text(self.bt_label, text)
        has_adb, ready = self.app.usb_status()
        if not has_adb:
            text = "Instale o adb: sudo apt install adb"
        elif ready:
            text = "Pronto"
        else:
            text = "Nenhum celular com depuração USB"
        _set_text(self.usb_label, text)

    def _append_log(self, text: str) -> None:
        if not text:
            return
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, text + "\n")
        extra = self.log_buffer.get_line_count() - LOG_LINES
        if extra > 0:
            self.log_buffer.delete(self.log_buffer.get_start_iter(), self.log_buffer.get_iter_at_line(extra))
        self.log_view.scroll_mark_onscreen(self.log_buffer.get_insert())
        self.log_view.scroll_to_iter(self.log_buffer.get_end_iter(), 0.0, False, 0.0, 1.0)

    def _tray_text(self) -> str:
        online = [row["name"] for row in self._rows if row.get("online")]
        if online:
            return "Connect Linux: conectado a " + ", ".join(online)
        if self.app.fatal:
            return "Connect Linux: " + self.app.fatal
        return "Connect Linux: aguardando o celular"

    # ---------- ações ----------

    def _selected(self) -> dict | None:
        selected = self.phone_list.get_selected_row()
        if selected is None:
            return None
        device_id = getattr(selected, "device_id", None)
        return next((item for item in self._rows if item["id"] == device_id), None)

    def _target(self) -> str | None:
        row = self._selected()
        if row and row.get("online"):
            return row["id"]
        online = [item["id"] for item in self._rows if item.get("online")]
        if row is None and len(online) == 1:
            return online[0]
        self._message("Selecione um celular conectado na lista.")
        return None

    def _send_file(self) -> None:
        device_id = self._target()
        if not device_id:
            return
        dialog = Gtk.FileChooserNative.new(
            "Enviar arquivo ao celular", self.window, Gtk.FileChooserAction.OPEN, "Enviar", "Cancelar"
        )
        dialog.set_select_multiple(True)
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            for name in dialog.get_filenames():
                self.app.send_file(device_id, Path(name))
        dialog.destroy()

    def _send_text(self) -> None:
        device_id = self._target()
        if not device_id:
            return
        text = self._ask_text()
        if text:
            self.app.send_text(device_id, text)

    def _send_url(self) -> None:
        device_id = self._target()
        if not device_id:
            return
        url = self._ask_line("Abrir link no celular", "Endereço http ou https:", "https://")
        if url:
            self.app.send_url(device_id, url.strip())

    def _find(self) -> None:
        device_id = self._target()
        if device_id:
            self.app.find_device(device_id)
            self._append_log("Chamando o celular…")

    def _forget(self) -> None:
        row = self._selected()
        if row is None:
            self._message("Selecione o celular que deseja esquecer.")
            return
        if self._confirm(
            f"Esquecer {row['name']}?",
            "Para conectar de novo, será preciso parear com um código.",
            "Esquecer",
            destructive=True,
        ):
            self.app.forget(row["id"])

    def _connect_phone(self) -> None:
        message = self.app.connect_known_phones()
        self._append_log(message)
        if not self.window.get_visible():
            notify("Connect Linux", message)

    def _save_name(self, *_args) -> bool:
        cleaned = self.app.set_name(self.name_entry.get_text())
        if cleaned != self.name_entry.get_text():
            self.name_entry.set_text(cleaned)
        return False

    def _toggle_clip(self, enabled: bool) -> None:
        self.app.set_clipboard_sync(enabled)

    def _toggle_notes(self, enabled: bool) -> None:
        self.app.set_show_notifications(enabled)

    def _toggle_autostart(self, enabled: bool) -> bool:
        if not autostart.set_enabled(enabled):
            self._message("Não foi possível mudar a inicialização automática.")
            return False
        self._append_log(
            "O Connect Linux vai iniciar com o Linux, ao lado do relógio."
            if enabled
            else "O Connect Linux não vai mais iniciar com o Linux."
        )
        return True

    def _open_folder(self) -> None:
        if not self.app.open_download_dir():
            self._message(str(self.app.store.download_dir))

    def _change_folder(self) -> None:
        dialog = Gtk.FileChooserNative.new(
            "Pasta de arquivos recebidos", self.window, Gtk.FileChooserAction.SELECT_FOLDER, "Escolher", "Cancelar"
        )
        dialog.set_current_folder(str(self.app.store.download_dir))
        if dialog.run() == Gtk.ResponseType.ACCEPT and dialog.get_filename():
            self.app.set_download_dir(Path(dialog.get_filename()))
            self.folder_label.set_text(str(self.app.store.download_dir))
        dialog.destroy()

    # ---------- atualização do programa ----------

    def _first_update_check(self) -> bool:
        self._check_update()
        GLib.timeout_add(UPDATE_CHECK_MS, self._check_update)
        return False

    def _check_update(self) -> bool:
        if not self._alive:
            return False
        if self._updating:
            return True

        def run() -> None:
            try:
                found = updater.check_latest()
            except Exception:
                GLib.idle_add(self._show_update, None, True)
                return
            GLib.idle_add(self._show_update, found, False)

        threading.Thread(target=run, name="connectlinux-update", daemon=True).start()
        return True

    def _show_update(self, found: dict | None, failed: bool) -> bool:
        if self._updating or not self._alive:
            return False
        if failed:
            self.version_item.set_label(f"Versão {__version__}")
            return False
        if not found:
            self._update = None
            self.update_button.hide()
            self.version_item.set_label(f"Versão {__version__} · atualizada")
            return False
        is_new = self._update is None or self._update["version"] != found["version"]
        self._update = found
        self.update_button.set_label(f"Atualizar para {found['version']}")
        self.update_button.set_sensitive(True)
        self.update_button.show()
        self.version_item.set_label(f"Versão {__version__} · {found['version']} disponível")
        if is_new:
            self._append_log(f"Versão {found['version']} disponível. Clique em Atualizar para instalar.")
            notify("Connect Linux", f"Versão {found['version']} disponível. Abra a janela e clique em Atualizar.")
        return False

    def _start_update(self) -> None:
        found = self._update
        if not found or self._updating:
            return
        if not self._confirm(
            f"Instalar a versão {found['version']}?",
            "O programa baixa a nova versão e o sistema pede a sua senha para instalar.",
            "Atualizar",
        ):
            return
        self._updating = True
        self.update_button.set_label("Atualizando…")
        self.update_button.set_sensitive(False)
        self._append_log(f"Baixando a versão {found['version']}…")

        def run() -> None:
            try:
                version = updater.download_and_install(found)
            except Exception as exc:
                GLib.idle_add(self._update_failed, str(exc))
                return
            GLib.idle_add(self._update_done, version)

        threading.Thread(target=run, name="connectlinux-install", daemon=True).start()

    def _update_failed(self, message: str) -> bool:
        self._updating = False
        self._append_log(f"Atualização não concluída: {message}")
        if self._update:
            self.update_button.set_label(f"Atualizar para {self._update['version']}")
            self.update_button.set_sensitive(True)
        return False

    def _update_done(self, version: str) -> bool:
        if updater.running_installed():
            notify("Connect Linux", f"Versão {version} instalada. Reabrindo o programa.")
            updater.restart_later()
            self._quit()
            return False
        self._updating = False
        self.update_button.hide()
        self.version_item.set_label(f"Versão {version} instalada")
        self._append_log(f"Versão {version} instalada. Abra o Connect Linux pelo menu do Mint para usá-la.")
        return False

    # ---------- janela e bandeja ----------

    def _tray_ready(self, supported: bool) -> None:
        self._tray_ok = supported
        if not supported:
            self._append_log("Ícone ao lado do relógio indisponível. Fechar a janela encerra o programa.")
            if self._start_hidden:
                self._present()

    def _on_close(self, *_args) -> bool:
        self._close_window()
        return True

    def _close_window(self) -> None:
        if not self._tray_ok:
            self._quit()
            return
        self.window.hide()
        if self._told_hide or self._start_hidden:
            return
        self._told_hide = True
        notify(
            "Connect Linux",
            "Continua ao lado do relógio. Clique com o direito no ícone para conectar, minimizar, maximizar ou sair.",
        )

    def _present(self) -> None:
        if not self.window.get_visible():
            self.window.show_all()
            self.update_button.set_visible(self._update is not None and not self._updating)
            self._render_pair()
            self._render_hero()
        self.window.deiconify()
        self.window.present()

    def _minimize(self) -> None:
        self._present()
        self.window.iconify()

    def _maximize(self) -> None:
        self._present()
        self.window.maximize()

    def _quit(self) -> None:
        if not self._alive:
            return
        self._alive = False
        self._tray.stop()
        self.window.hide()
        self.app.stop()
        Gtk.main_quit()

    # ---------- diálogos ----------

    def _message(self, text: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self.window if self.window.get_visible() else None,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=text,
        )
        dialog.run()
        dialog.destroy()

    def _confirm(self, title: str, detail: str, accept: str, destructive: bool = False) -> bool:
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=title,
        )
        dialog.format_secondary_text(detail)
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        button = dialog.add_button(accept, Gtk.ResponseType.ACCEPT)
        button.get_style_context().add_class("destructive-action" if destructive else "suggested-action")
        answer = dialog.run()
        dialog.destroy()
        return answer == Gtk.ResponseType.ACCEPT

    def _ask_text(self) -> str | None:
        dialog = Gtk.Dialog(title="Enviar texto", transient_for=self.window, modal=True)
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        send = dialog.add_button("Enviar", Gtk.ResponseType.ACCEPT)
        send.get_style_context().add_class("suggested-action")
        dialog.set_default_size(480, 280)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(16)
        box.pack_start(_label("O texto aparece no celular.", "cl-dim"), False, False, 0)
        view = Gtk.TextView()
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        for side in ("left", "right", "top", "bottom"):
            getattr(view, f"set_{side}_margin")(8)
        scroll = Gtk.ScrolledWindow()
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        scroll.add(view)
        box.pack_start(scroll, True, True, 0)
        dialog.show_all()
        view.grab_focus()
        answer = dialog.run()
        buffer = view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()
        dialog.destroy()
        return text if answer == Gtk.ResponseType.ACCEPT and text else None

    def _ask_line(self, title: str, prompt: str, initial: str = "") -> str | None:
        dialog = Gtk.Dialog(title=title, transient_for=self.window, modal=True)
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        accept = dialog.add_button("Abrir", Gtk.ResponseType.ACCEPT)
        accept.get_style_context().add_class("suggested-action")
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(16)
        box.pack_start(_label(prompt), False, False, 0)
        entry = Gtk.Entry()
        entry.set_text(initial)
        entry.set_width_chars(44)
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)
        dialog.show_all()
        entry.grab_focus()
        entry.set_position(-1)
        answer = dialog.run()
        text = entry.get_text().strip()
        dialog.destroy()
        return text if answer == Gtk.ResponseType.ACCEPT and text and text != initial else None

    def _show_help(self) -> None:
        if self._help is not None:
            self._help.present()
            return
        self._help = help_window(self.window)
        self._help.connect("destroy", lambda _w: setattr(self, "_help", None))
        self._help.show_all()


def help_window(parent: Gtk.Window | None) -> Gtk.Window:
    window = Gtk.Window(title="Ajuda · Connect Linux")
    if parent is not None and parent.get_visible():
        window.set_transient_for(parent)
        window.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
    else:
        window.set_position(Gtk.WindowPosition.CENTER)
    window.set_resizable(False)
    header = Gtk.HeaderBar()
    header.set_show_close_button(True)
    header.set_title("Ajuda")
    window.set_titlebar(header)

    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    body.set_border_width(24)
    window.add(body)

    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    logo = _logo(64)
    if logo is not None:
        top.pack_start(logo, False, False, 0)
    names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    names.set_valign(Gtk.Align.CENTER)
    names.pack_start(_label("Connect Linux", "cl-hero"), False, False, 0)
    names.pack_start(_label(f"Versão {__version__}", "cl-dim"), False, False, 0)
    top.pack_start(names, False, False, 0)
    body.pack_start(top, False, False, 0)

    body.pack_start(
        _label(
            "Liga este computador a um celular Android pelo Wi-Fi, Bluetooth, cabo USB ou Tailscale. "
            "Envia texto, links e arquivos nos dois sentidos, compartilha a área de transferência "
            "e mostra aqui as notificações do celular.",
            wrap=True,
            width=56,
        ),
        False,
        False,
        0,
    )
    body.pack_start(
        _label(
            "Fechar a janela deixa o programa ao lado do relógio. Clique com o direito no ícone para conectar ou sair.",
            "cl-dim",
            wrap=True,
            width=56,
        ),
        False,
        False,
        0,
    )

    credits = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    credits.pack_start(_label("Criado por Luiz Henrique Cabral de Oliveira", "cl-strong"), False, False, 0)
    for text, target in (("luizhenrique.be@gmail.com", "mailto:luizhenrique.be@gmail.com"), (GITHUB_URL, GITHUB_URL)):
        link = Gtk.LinkButton.new_with_label(target, text)
        link.set_halign(Gtk.Align.START)
        link.connect("activate-link", lambda _l, url=target: _open_target(url) or True)
        credits.pack_start(link, False, False, 0)
    body.pack_start(credits, False, False, 0)

    body.pack_start(Gtk.Separator(), False, False, 6)
    body.pack_start(_label("Gostou? Faça uma doação pelo Pix", "cl-section"), False, False, 0)
    code = pix.payload()
    matrix = pix.qr_matrix(code)
    if matrix:
        area = Gtk.DrawingArea()
        area.set_size_request(240, 240)
        area.set_halign(Gtk.Align.CENTER)
        area.connect("draw", lambda widget, cr: _draw_qr(widget, cr, matrix))
        body.pack_start(area, False, False, 4)
    else:
        body.pack_start(_label("Instale python3-qrcode para ver o QR code.", "cl-dim"), False, False, 0)
    key = _label(f"Chave Pix (e-mail): {pix.PIX_KEY}", center=True)
    key.set_selectable(True)
    body.pack_start(key, False, False, 0)

    status = _label("", "cl-dim", center=True)

    def copy(text: str, what: str) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()
        status.set_text(f"{what} copiado.")

    buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    buttons.set_halign(Gtk.Align.CENTER)
    copy_code = _icon_button("edit-copy-symbolic", "Copiar Pix copia e cola")
    copy_code.get_style_context().add_class("suggested-action")
    copy_code.connect("clicked", lambda _b: copy(code, "Código Pix"))
    copy_key = _icon_button("edit-copy-symbolic", "Copiar chave")
    copy_key.connect("clicked", lambda _b: copy(pix.PIX_KEY, "Chave"))
    buttons.pack_start(copy_code, False, False, 0)
    buttons.pack_start(copy_key, False, False, 0)
    body.pack_start(buttons, False, False, 4)
    body.pack_start(status, False, False, 0)

    window.connect("key-press-event", lambda w, event: event.keyval == Gdk.KEY_Escape and (w.destroy() or True))
    return window


# ---------- peças reutilizáveis ----------


def _load_css() -> None:
    provider = Gtk.CssProvider()
    try:
        provider.load_from_data(CSS)
    except GLib.Error:
        return
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _logo(size: int) -> Gtk.Image | None:
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path(), size, size, True)
    except GLib.Error:
        return None
    return Gtk.Image.new_from_pixbuf(pixbuf)


def _page() -> Gtk.Box:
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    page.set_margin_top(20)
    page.set_margin_bottom(20)
    page.set_margin_start(28)
    page.set_margin_end(28)
    return page


def _scrolled(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.add(child)
    return scroll


def _card(child: Gtk.Widget) -> Gtk.Frame:
    frame = Gtk.Frame()
    frame.set_shadow_type(Gtk.ShadowType.NONE)
    frame.get_style_context().add_class("cl-card")
    frame.add(child)
    return frame


def _label(text: str = "", css: str | None = None, wrap: bool = False, center: bool = False, width: int = 0):
    label = Gtk.Label(label=text)
    label.set_xalign(0.5 if center else 0.0)
    if center:
        label.set_justify(Gtk.Justification.CENTER)
    if wrap:
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if width:
        label.set_max_width_chars(width)
        label.set_width_chars(min(width, 40))
    if css:
        label.get_style_context().add_class(css)
    return label


def _set_text(label: Gtk.Label, text: str) -> None:
    if label.get_text() != text:
        label.set_text(text)


def _icon_button(icon: str, text: str) -> Gtk.Button:
    button = Gtk.Button()
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON), False, False, 0)
    box.pack_start(Gtk.Label(label=text), False, False, 0)
    button.add(box)
    return button


def _switch(active: bool, on_change) -> Gtk.Switch:
    switch = Gtk.Switch()
    switch.set_active(active)
    switch.set_valign(Gtk.Align.CENTER)

    def changed(widget, state) -> bool:
        if on_change(state) is False:
            return True
        widget.set_state(state)
        return True

    switch.connect("state-set", changed)
    return switch


def _settings_list() -> Gtk.ListBox:
    listbox = Gtk.ListBox()
    listbox.set_selection_mode(Gtk.SelectionMode.NONE)
    listbox.set_header_func(_separator_header)
    return listbox


def _separator_header(row, before) -> None:
    if before is not None and row.get_header() is None:
        row.set_header(Gtk.Separator())


def _setting_row(icon: str, title: str, subtitle: str, end: Gtk.Widget | None) -> Gtk.ListBoxRow:
    row = Gtk.ListBoxRow()
    row.set_activatable(False)
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
    box.set_border_width(12)
    image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
    box.pack_start(image, False, False, 0)
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    texts.set_valign(Gtk.Align.CENTER)
    texts.pack_start(_label(title), False, False, 0)
    detail = _label(subtitle, "cl-dim", wrap=True)
    detail.set_ellipsize(Pango.EllipsizeMode.NONE)
    texts.pack_start(detail, False, False, 0)
    box.pack_start(texts, True, True, 0)
    if end is not None:
        box.pack_end(end, False, False, 0)
    row.add(box)
    row.subtitle = detail
    return row


def _phone_row(device_id: str) -> dict:
    row = Gtk.ListBoxRow()
    row.device_id = device_id
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
    box.set_border_width(12)
    icon = Gtk.Image.new_from_icon_name("phone-symbolic", Gtk.IconSize.DND)
    icon.set_pixel_size(32)
    box.pack_start(icon, False, False, 0)
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    texts.set_valign(Gtk.Align.CENTER)
    name = _label("", "cl-strong")
    name.set_ellipsize(Pango.EllipsizeMode.END)
    state = _label("", "cl-dim")
    state.set_ellipsize(Pango.EllipsizeMode.END)
    texts.pack_start(name, False, False, 0)
    texts.pack_start(state, False, False, 0)
    box.pack_start(texts, True, True, 0)
    dot = Gtk.Label(label="●")
    dot.set_valign(Gtk.Align.CENTER)
    box.pack_end(dot, False, False, 0)
    battery_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    battery_box.set_valign(Gtk.Align.CENTER)
    battery_icon = Gtk.Image()
    battery_label = Gtk.Label()
    battery_box.pack_start(battery_icon, False, False, 0)
    battery_box.pack_start(battery_label, False, False, 0)
    box.pack_end(battery_box, False, False, 8)
    row.add(box)
    return {
        "row": row,
        "name": name,
        "state": state,
        "dot": dot,
        "battery_box": battery_box,
        "battery_icon": battery_icon,
        "battery_label": battery_label,
        "dot_class": "",
        "battery_name": "",
    }


def _fill_phone_row(widgets: dict, row: dict) -> None:
    if row.get("online"):
        latency = row.get("latency_ms")
        via = f" por {row['via']}" if row.get("via") else ""
        state = f"Conectado{via}" if latency is None else f"Conectado{via} · {latency} ms"
        dot = "cl-online"
    elif row.get("paired"):
        state, dot = "Pareado, fora do ar", "cl-offline"
    elif row.get("seen"):
        state, dot = "Visto na rede, toque em parear no celular", "cl-seen"
    else:
        state, dot = "Desconectado", "cl-offline"
    host = row.get("host")
    if host:
        state += f" · {host}"
    _set_text(widgets["name"], row["name"])
    _set_text(widgets["state"], state)
    if widgets["dot_class"] != dot:
        context = widgets["dot"].get_style_context()
        if widgets["dot_class"]:
            context.remove_class(widgets["dot_class"])
        context.add_class(dot)
        widgets["dot_class"] = dot

    level = row.get("battery")
    if level is None:
        widgets["battery_box"].hide()
        return
    widgets["battery_box"].show()
    charging = bool(row.get("charging"))
    _set_text(widgets["battery_label"], f"{level}%")
    icon = _battery_icon(int(level), charging)
    if widgets["battery_name"] != icon:
        widgets["battery_icon"].set_from_icon_name(icon, Gtk.IconSize.MENU)
        widgets["battery_name"] = icon
    widgets["battery_box"].set_tooltip_text("Carregando" if charging else "Bateria")


def _battery_icon(level: int, charging: bool) -> str:
    step = max(0, min(100, int(round(level / 10.0)) * 10))
    name = f"battery-level-{step}{'-charging' if charging else ''}-symbolic"
    if Gtk.IconTheme.get_default().has_icon(name):
        return name
    return "battery-good-charging-symbolic" if charging else "battery-good-symbolic"


def _draw_qr(widget: Gtk.Widget, cr, matrix: list) -> bool:
    size = min(widget.get_allocated_width(), widget.get_allocated_height())
    quiet = 2
    cells = len(matrix) + quiet * 2
    scale = size / cells
    cr.set_source_rgb(1, 1, 1)
    cr.rectangle(0, 0, size, size)
    cr.fill()
    cr.set_source_rgb(0, 0, 0)
    for y, line in enumerate(matrix):
        for x, dark in enumerate(line):
            if dark:
                cr.rectangle((x + quiet) * scale, (y + quiet) * scale, scale + 0.3, scale + 0.3)
    cr.fill()
    return False


def _open_target(target: str) -> None:
    if shutil.which("xdg-open"):
        subprocess.Popen(
            ["xdg-open", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def run_gui(app: App, start_hidden: bool = False) -> None:
    autostart.ensure_default()
    window = DesktopWindow(app, start_hidden)

    def stop() -> bool:
        window._quit()
        return False

    for signum in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, stop)
    Gtk.main()
