"""Janela do Connect Linux para o ambiente gráfico do Linux Mint."""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import __version__, autostart, pix, updater
from .app import App
from .system_bridge import notify
from .tray import TrayIcon

UPDATE_CHECK_MS = 6 * 60 * 60 * 1000
GITHUB_URL = "https://github.com/lhcabral/connect_linux"


class DesktopWindow:
    def __init__(self, app: App) -> None:
        self.app = app
        self._alive = True
        self._loading = True
        self._told_hide = False
        self._update: dict | None = None
        self._updating = False
        self._rows: list[dict] = []
        self._codes: dict[str, tuple[str, str]] = {}
        self._actions: queue.Queue = queue.Queue()
        self._tray = TrayIcon(
            on_present=lambda: self._post(self._present),
            on_connect=lambda: self._post(self._connect_phone),
            on_minimize=lambda: self._post(self._minimize),
            on_maximize=lambda: self._post(self._maximize),
            on_quit=lambda: self._post(self._quit),
        )
        self._tray_ok = self._tray.start()
        if not self._tray_ok:
            self._tray = None

        self.root = tk.Tk()
        self.root.title("Connect Linux")
        self.root.geometry("960x700")
        self.root.minsize(760, 560)
        self._apply_theme()
        self._build()
        self._loading = False
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(200, self.refresh)
        self.root.after(150, self._poll_actions)
        self.root.after(1500, self._check_update)
        if not self._tray_ok:
            self._append_log("Ícone ao lado do relógio indisponível. Fechar a janela encerra o programa.")

    def _apply_theme(self) -> None:
        background = "#F3F6F1"
        card = "#FFFFFF"
        ink = "#1C2416"
        self.root.configure(bg=background)
        icon = tk.PhotoImage(width=16, height=16)
        icon.put("#2F6B2C", to=(0, 0, 16, 16))
        icon.put("#E7F5DF", to=(4, 4, 12, 12))
        self.root.iconphoto(True, icon)
        self._icon = icon

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", background=background, foreground=ink, font=("Sans", 11))
        style.configure("TFrame", background=background)
        style.configure("Card.TFrame", background=card)
        style.configure("TLabel", background=background, foreground=ink)
        style.configure("Card.TLabel", background=card, foreground=ink)
        style.configure("Title.TLabel", font=("Sans", 20, "bold"), background=background, foreground="#2F6B2C")
        style.configure("Hint.TLabel", background=background, foreground="#5E6B58")
        style.configure("Code.TLabel", font=("Sans", 28, "bold"), background=card, foreground="#2F6B2C")
        style.configure("TButton", padding=(12, 8))
        style.configure("TCheckbutton", background=background, foreground=ink)
        style.configure("Treeview", rowheight=28, font=("Sans", 11))
        style.configure("Treeview.Heading", font=("Sans", 11, "bold"))
        style.map("Treeview", background=[("selected", "#D7ECC8")], foreground=[("selected", ink)])

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        outer.rowconfigure(5, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        title_row = ttk.Frame(header)
        title_row.pack(fill="x")
        ttk.Label(title_row, text="Connect Linux", style="Title.TLabel").pack(side="left")
        self.update_button = ttk.Button(
            title_row,
            text=f"Versão {__version__} · procurando atualização…",
            command=self._start_update,
            state="disabled",
        )
        self.update_button.pack(side="right")
        ttk.Button(title_row, text="Ajuda", command=self._show_help).pack(side="right", padx=(0, 8))
        ttk.Label(
            header,
            text="Deixe o celular na mesma rede Wi-Fi e abra o aplicativo Connect Linux. Fechar esta janela deixa o programa ao lado do relógio.",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(2, 8))

        name_row = ttk.Frame(header)
        name_row.pack(fill="x")
        ttk.Label(name_row, text="Nome deste computador").pack(side="left")
        self.name_var = tk.StringVar(value=self.app.store.name)
        name_entry = ttk.Entry(name_row, textvariable=self.name_var, width=28)
        name_entry.pack(side="left", padx=(8, 0))
        name_entry.bind("<FocusOut>", self._save_name)
        name_entry.bind("<Return>", self._save_name)

        self.status = tk.StringVar(value="Iniciando…")
        ttk.Label(header, textvariable=self.status, style="Hint.TLabel").pack(anchor="w", pady=(8, 0))

        other = ttk.Frame(header)
        other.pack(fill="x", pady=(6, 0))
        self.bt_status = tk.StringVar(value="Bluetooth: verificando…")
        self.usb_status = tk.StringVar(value="Cabo USB: verificando…")
        ttk.Label(other, textvariable=self.bt_status).pack(side="left")
        ttk.Button(other, text="Ligar Bluetooth e ficar visível", command=self.app.enable_bluetooth).pack(
            side="left", padx=(8, 16)
        )
        ttk.Label(other, textvariable=self.usb_status).pack(side="left")

        self.pair_frame = ttk.Frame(outer, style="Card.TFrame", padding=12)
        self.pair_title = ttk.Label(self.pair_frame, text="", style="Card.TLabel")
        self.pair_title.pack(anchor="w")
        self.pair_code = ttk.Label(self.pair_frame, text="", style="Code.TLabel")
        self.pair_code.pack(anchor="w", pady=(4, 8))
        ttk.Button(self.pair_frame, text="Recusar", command=self.app.reject_pair).pack(anchor="w")

        ttk.Label(outer, text="Celulares").grid(row=2, column=0, sticky="w", pady=(12, 4))
        table_frame = ttk.Frame(outer)
        table_frame.grid(row=3, column=0, sticky="nsew")
        columns = ("name", "state", "battery", "host")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("name", text="Nome")
        self.tree.heading("state", text="Estado")
        self.tree.heading("battery", text="Bateria")
        self.tree.heading("host", text="Endereço")
        self.tree.column("name", width=220, stretch=True)
        self.tree.column("state", width=240, stretch=False)
        self.tree.column("battery", width=140, stretch=False)
        self.tree.column("host", width=180, stretch=False)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        actions = ttk.Frame(outer)
        actions.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Enviar arquivo", command=self._send_file).pack(side="left")
        ttk.Button(actions, text="Enviar texto", command=self._send_text).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Abrir link no celular", command=self._send_url).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Chamar celular", command=self._find).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Esquecer", command=self._forget).pack(side="left", padx=(8, 0))

        options = ttk.Frame(outer)
        options.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self.clip_var = tk.BooleanVar(value=self.app.store.clipboard_sync)
        self.note_var = tk.BooleanVar(value=self.app.store.show_notifications)
        ttk.Checkbutton(
            options,
            text="Sincronizar área de transferência",
            variable=self.clip_var,
            command=self._toggle_clip,
        ).pack(side="left")
        ttk.Checkbutton(
            options,
            text="Mostrar notificações do celular",
            variable=self.note_var,
            command=self._toggle_notes,
        ).pack(side="left", padx=(16, 0))
        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(
            options,
            text="Iniciar com o Linux",
            variable=self.autostart_var,
            command=self._toggle_autostart,
        ).pack(side="left", padx=(16, 0))

        folder = ttk.Frame(outer)
        folder.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(folder, text="Abrir pasta de recebidos", command=self._open_folder).pack(side="left")
        ttk.Button(folder, text="Mudar pasta", command=self._change_folder).pack(side="left", padx=(8, 0))
        self.folder_label = tk.StringVar(value=str(self.app.store.download_dir))
        ttk.Label(folder, textvariable=self.folder_label, style="Hint.TLabel").pack(side="left", padx=(12, 0))

        ttk.Label(outer, text="Atividade").grid(row=7, column=0, sticky="w", pady=(12, 4))
        log_frame = ttk.Frame(outer)
        log_frame.grid(row=8, column=0, sticky="nsew")
        outer.rowconfigure(8, weight=1)
        self.log = tk.Text(log_frame, height=8, wrap="word", relief="flat", bg="#FFFFFF", fg="#1C2416")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set, state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    def refresh(self) -> None:
        if not self._alive:
            return
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
                else:
                    self._codes.pop(conn_id, None)
                self._render_pair()
        self._rows = self.app.snapshot()
        self._render_devices()
        self._render_status()
        if self._tray is not None:
            self._tray.set_tooltip(self._tray_text())
        self.root.after(400, self.refresh)

    def _render_status(self) -> None:
        if self.app.fatal:
            self.status.set(self.app.fatal)
            return
        ips = ", ".join(self.app.local_ips()) or "IP não identificado"
        self.status.set(f"Wi-Fi: porta {self.app.port}  ·  {ips}  ·  digite esse IP no celular se ele não achar o computador")

        bt = self.app.bluetooth_status()
        if not bt:
            text = "Bluetooth: verificando…"
        elif bt.get("error"):
            text = "Bluetooth: indisponível"
        elif not bt.get("available"):
            text = "Bluetooth: sem adaptador"
        elif not bt.get("powered"):
            text = "Bluetooth: desligado"
        else:
            text = f"Bluetooth: pronto ({bt.get('name') or 'este computador'})"
        self.bt_status.set(text)

        has_adb, ready = self.app.usb_status()
        if not has_adb:
            self.usb_status.set("Cabo USB: adb não encontrado")
        elif ready:
            self.usb_status.set("Cabo USB: pronto")
        else:
            self.usb_status.set("Cabo USB: nenhum celular com depuração USB")

    def _render_pair(self) -> None:
        if not self._codes:
            self.pair_frame.grid_remove()
            return
        lines = [f"{name}: {code}" for name, code in self._codes.values()]
        names = ", ".join(name for name, _code in self._codes.values())
        self.pair_title.configure(text=f"Digite este código no celular ({names})")
        self.pair_code.configure(text="\n".join(lines))
        self.pair_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))

    def _render_devices(self) -> None:
        selected = self.tree.selection()
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        for row in self._rows:
            if row["online"]:
                latency = row.get("latency_ms")
                via = f" ({row['via']})" if row.get("via") else ""
                state = f"Conectado{via}" if latency is None else f"Conectado{via} · {latency} ms"
            elif row["paired"]:
                state = "Pareado, fora do ar"
            elif row["seen"]:
                state = "Visto na rede"
            else:
                state = "Desconectado"
            if row.get("battery") is None:
                battery = "—"
            else:
                battery = f"{row['battery']}%"
                if row.get("charging"):
                    battery += " · carregando"
            self.tree.insert(
                "",
                "end",
                iid=row["id"],
                values=(row["name"], state, battery, row.get("host") or "—"),
            )
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])

    def _append_log(self, text: str) -> None:
        if not text:
            return
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 300:
            self.log.delete("1.0", f"{lines - 300}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _save_name(self, _event=None) -> None:
        if self._loading:
            return
        cleaned = self.app.set_name(self.name_var.get())
        self.name_var.set(cleaned)

    def _toggle_clip(self) -> None:
        self.app.set_clipboard_sync(bool(self.clip_var.get()))

    def _toggle_notes(self) -> None:
        self.app.set_show_notifications(bool(self.note_var.get()))

    def _toggle_autostart(self) -> None:
        wanted = bool(self.autostart_var.get())
        if not autostart.set_enabled(wanted):
            self.autostart_var.set(not wanted)
            messagebox.showerror("Connect Linux", "Não foi possível mudar a inicialização automática.")
            return
        self._append_log(
            "O Connect Linux vai iniciar com o Linux, ao lado do relógio."
            if wanted
            else "O Connect Linux não vai mais iniciar com o Linux."
        )

    def _target(self) -> str | None:
        selected = self.tree.selection()
        if selected:
            device_id = selected[0]
            row = next((item for item in self._rows if item["id"] == device_id), None)
            if row and row["online"]:
                return device_id
            messagebox.showwarning("Connect Linux", "Esse celular não está conectado.", parent=self.root)
            return None
        online = [item["id"] for item in self._rows if item["online"]]
        if len(online) == 1:
            return online[0]
        messagebox.showwarning(
            "Connect Linux",
            "Selecione um celular conectado na lista.",
            parent=self.root,
        )
        return None

    def _send_file(self) -> None:
        device_id = self._target()
        if not device_id:
            return
        chosen = filedialog.askopenfilename(parent=self.root, title="Enviar arquivo ao celular")
        if not chosen:
            return
        self.app.send_file(device_id, Path(chosen))

    def _send_text(self) -> None:
        device_id = self._target()
        if not device_id:
            return
        text = _ask_text(self.root)
        if text:
            self.app.send_text(device_id, text)

    def _send_url(self) -> None:
        device_id = self._target()
        if not device_id:
            return
        url = simpledialog.askstring("Abrir link", "Endereço http ou https:", parent=self.root)
        if url:
            self.app.send_url(device_id, url.strip())

    def _find(self) -> None:
        device_id = self._target()
        if device_id:
            self.app.find_device(device_id)
            self._append_log("Chamando o celular…")

    def _forget(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Connect Linux", "Selecione o celular que deseja esquecer.", parent=self.root)
            return
        device_id = selected[0]
        row = next((item for item in self._rows if item["id"] == device_id), None)
        name = row["name"] if row else "este celular"
        if messagebox.askyesno("Connect Linux", f"Esquecer {name}?", parent=self.root):
            self.app.forget(device_id)

    def _open_folder(self) -> None:
        if not self.app.open_download_dir():
            messagebox.showinfo("Connect Linux", str(self.app.store.download_dir), parent=self.root)

    def _change_folder(self) -> None:
        chosen = filedialog.askdirectory(parent=self.root, title="Pasta de arquivos recebidos")
        if not chosen:
            return
        self.app.set_download_dir(Path(chosen))
        self.folder_label.set(str(self.app.store.download_dir))

    def _tray_text(self) -> str:
        online = [row["name"] for row in self._rows if row.get("online")]
        if online:
            return "Connect Linux: conectado a " + ", ".join(online)
        if self.app.fatal:
            return "Connect Linux: " + self.app.fatal
        return "Connect Linux: aguardando o celular"

    def _post(self, action) -> None:
        self._actions.put(action)

    def _poll_actions(self) -> None:
        if not self._alive:
            return
        while True:
            try:
                action = self._actions.get_nowait()
            except queue.Empty:
                break
            action()
            if not self._alive:
                return
        self.root.after(150, self._poll_actions)

    def _check_update(self) -> None:
        if not self._alive:
            return
        self.root.after(UPDATE_CHECK_MS, self._check_update)
        if self._updating:
            return

        def run() -> None:
            try:
                found = updater.check_latest()
            except Exception as exc:
                log_text = f"Não foi possível procurar atualização: {exc}"
                self._post(lambda: self._show_update(None, log_text))
                return
            self._post(lambda: self._show_update(found, None))

        threading.Thread(target=run, name="connectlinux-update", daemon=True).start()

    def _show_update(self, found: dict | None, error: str | None) -> None:
        if self._updating:
            return
        if error:
            self.update_button.configure(text=f"Versão {__version__}", state="disabled")
            return
        if not found:
            self._update = None
            self.update_button.configure(text=f"Versão {__version__} · atualizada", state="disabled")
            return
        is_new = self._update is None or self._update["version"] != found["version"]
        self._update = found
        self.update_button.configure(text=f"Atualizar para {found['version']}", state="normal")
        if is_new:
            self._append_log(f"Versão {found['version']} disponível. Clique em Atualizar para instalar.")
            notify("Connect Linux", f"Versão {found['version']} disponível. Abra a janela e clique em Atualizar.")

    def _start_update(self) -> None:
        found = self._update
        if not found or self._updating:
            return
        if not messagebox.askyesno(
            "Connect Linux",
            f"Baixar e instalar a versão {found['version']}? O sistema vai pedir a sua senha.",
            parent=self.root,
        ):
            return
        self._updating = True
        self.update_button.configure(text="Atualizando…", state="disabled")
        self._append_log(f"Baixando a versão {found['version']}…")

        def run() -> None:
            try:
                version = updater.download_and_install(found)
            except Exception as exc:
                message = str(exc)
                self._post(lambda: self._update_failed(message))
                return
            self._post(lambda: self._update_done(version))

        threading.Thread(target=run, name="connectlinux-install", daemon=True).start()

    def _update_failed(self, message: str) -> None:
        self._updating = False
        self._append_log(f"Atualização não concluída: {message}")
        if self._update:
            self.update_button.configure(text=f"Atualizar para {self._update['version']}", state="normal")

    def _update_done(self, version: str) -> None:
        if updater.running_installed():
            notify("Connect Linux", f"Versão {version} instalada. Reabrindo o programa.")
            updater.restart_later()
            self._quit()
            return
        self._updating = False
        self.update_button.configure(text=f"Versão {version} instalada", state="disabled")
        self._append_log(f"Versão {version} instalada. Abra o Connect Linux pelo menu do Mint para usá-la.")

    def _show_help(self) -> None:
        window = getattr(self, "_help_window", None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return
        self._help_window = _help_window(self.root)

    def _on_close(self) -> None:
        if self._tray_ok:
            self._hide()
            return
        self._quit()

    def _hide(self) -> None:
        self.root.withdraw()
        if self._told_hide:
            return
        self._told_hide = True
        notify(
            "Connect Linux",
            "Continua ao lado do relógio. Clique com o direito no ícone para conectar, minimizar, maximizar ou sair.",
        )

    def _present(self) -> None:
        try:
            self.root.state("normal")
        except tk.TclError:
            self.root.deiconify()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _minimize(self) -> None:
        self.root.deiconify()
        self.root.update_idletasks()
        try:
            self.root.state("iconic")
        except tk.TclError:
            try:
                self.root.iconify()
            except tk.TclError:
                return

    def _maximize(self) -> None:
        self.root.deiconify()
        self.root.update_idletasks()
        _request_maximize(self.root)
        self.root.lift()
        self.root.focus_force()

    def _connect_phone(self) -> None:
        message = self.app.connect_known_phones()
        self._append_log(message)
        if not self.root.winfo_viewable():
            notify("Connect Linux", message)

    def _quit(self) -> None:
        if not self._alive:
            return
        self._alive = False
        tray = self._tray
        self._tray = None
        if tray is not None:
            tray.stop()
        self.app.stop()
        self.root.destroy()


def _help_window(parent: tk.Tk) -> tk.Toplevel:
    window = tk.Toplevel(parent)
    window.title("Ajuda · Connect Linux")
    window.transient(parent)
    window.resizable(False, False)
    window.configure(bg="#F3F6F1")
    body = ttk.Frame(window, padding=20)
    body.pack(fill="both", expand=True)
    font = tkfont.Font(family="Sans", size=11)
    line = font.metrics("linespace")
    wrap = font.measure("0") * 52

    ttk.Label(body, text="Connect Linux", style="Title.TLabel").pack(anchor="w")
    ttk.Label(body, text=f"Versão {__version__}", style="Hint.TLabel").pack(anchor="w")
    ttk.Label(
        body,
        text=(
            "Liga este computador a um celular Android pelo Wi-Fi, Bluetooth, cabo USB ou Tailscale. "
            "Envia texto, links e arquivos nos dois sentidos, compartilha a área de transferência "
            "e mostra aqui as notificações do celular."
        ),
        wraplength=wrap,
        justify="left",
    ).pack(anchor="w", pady=(10, 4))
    ttk.Label(
        body,
        text="Fechar a janela principal deixa o programa ao lado do relógio. Clique com o direito no ícone para conectar ou sair.",
        style="Hint.TLabel",
        wraplength=wrap,
        justify="left",
    ).pack(anchor="w", pady=(0, 12))

    ttk.Label(body, text="Criador: Luiz Henrique Cabral de Oliveira").pack(anchor="w")
    _link(body, "luizhenrique.be@gmail.com", "mailto:luizhenrique.be@gmail.com")
    _link(body, GITHUB_URL, GITHUB_URL)

    ttk.Separator(body).pack(fill="x", pady=14)
    ttk.Label(body, text="Gostou? Faça uma doação pelo Pix", font=("Sans", 12, "bold")).pack(anchor="w")
    code = pix.payload()
    matrix = pix.qr_matrix(code)
    if matrix:
        scale = max(3, line * 12 // len(matrix))
        size = len(matrix) * scale
        canvas = tk.Canvas(body, width=size, height=size, bg="#FFFFFF", highlightthickness=0)
        for row, cells in enumerate(matrix):
            for column, dark in enumerate(cells):
                if dark:
                    x, y = column * scale, row * scale
                    canvas.create_rectangle(x, y, x + scale, y + scale, fill="#000000", outline="")
        canvas.pack(pady=(8, 6))
    else:
        ttk.Label(body, text="(Instale python3-qrcode para ver o QR code.)", style="Hint.TLabel").pack(anchor="w")
    ttk.Label(body, text=f"Chave Pix (e-mail): {pix.PIX_KEY}").pack(anchor="center")

    status = tk.StringVar(value="")

    def copy(text: str, label: str) -> None:
        window.clipboard_clear()
        window.clipboard_append(text)
        status.set(f"{label} copiado.")

    buttons = ttk.Frame(body)
    buttons.pack(pady=(8, 0))
    ttk.Button(buttons, text="Copiar Pix copia e cola", command=lambda: copy(code, "Código Pix")).pack(side="left")
    ttk.Button(buttons, text="Copiar chave", command=lambda: copy(pix.PIX_KEY, "Chave")).pack(side="left", padx=(8, 0))
    ttk.Button(buttons, text="Fechar", command=window.destroy).pack(side="left", padx=(8, 0))
    ttk.Label(body, textvariable=status, style="Hint.TLabel").pack(pady=(6, 0))
    window.bind("<Escape>", lambda _event: window.destroy())
    return window


def _link(parent: ttk.Frame, text: str, target: str) -> None:
    label = tk.Label(parent, text=text, fg="#2F6B2C", bg="#F3F6F1", cursor="hand2", font=("Sans", 11, "underline"))
    label.pack(anchor="w")
    label.bind("<Button-1>", lambda _event: _open_target(target))


def _open_target(target: str) -> None:
    if shutil.which("xdg-open"):
        subprocess.Popen(
            ["xdg-open", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def _ask_text(parent: tk.Tk) -> str | None:
    window = tk.Toplevel(parent)
    window.title("Enviar texto")
    window.transient(parent)
    window.grab_set()
    window.configure(bg="#F3F6F1")
    ttk.Label(window, text="O texto aparece no celular.").pack(anchor="w", padx=12, pady=(12, 6))
    widget = tk.Text(window, width=54, height=8, wrap="word")
    widget.pack(fill="both", expand=True, padx=12)
    result = {"value": None}

    def confirm() -> None:
        result["value"] = widget.get("1.0", "end").strip()
        window.destroy()

    buttons = ttk.Frame(window)
    buttons.pack(fill="x", padx=12, pady=12)
    ttk.Button(buttons, text="Cancelar", command=window.destroy).pack(side="right")
    ttk.Button(buttons, text="Enviar", command=confirm).pack(side="right", padx=(0, 8))
    window.bind("<Escape>", lambda _event: window.destroy())
    widget.focus_set()
    parent.wait_window(window)
    return result["value"] or None


def run_gui(app: App, start_hidden: bool = False) -> None:
    autostart.ensure_default()
    window = DesktopWindow(app)
    if start_hidden and window._tray_ok:
        window._told_hide = True
        window.root.withdraw()
    window.root.mainloop()


def _request_maximize(root: tk.Tk) -> None:
    try:
        root.state("zoomed")
        return
    except tk.TclError:
        pass
    if not shutil.which("wmctrl"):
        _fill_screen(root)
        return
    window = _frame_window(root)
    if window is None:
        _fill_screen(root)
        return
    done = subprocess.run(
        ["wmctrl", "-ir", window, "-b", "add,maximized_vert,maximized_horz"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=2,
    )
    if done.returncode != 0:
        _fill_screen(root)


def _frame_window(root: tk.Tk) -> str | None:
    try:
        listing = subprocess.check_output(["wmctrl", "-lp"], text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    pid = str(os.getpid())
    title = root.title()
    fallback = None
    for line in listing.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5 or parts[4] != title:
            continue
        if parts[2] == pid:
            return parts[0]
        fallback = parts[0]
    return fallback


def _fill_screen(root: tk.Tk) -> None:
    root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")
