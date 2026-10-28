"""Liga o servidor, a descoberta na rede e a fila de eventos da interface."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
import time
from pathlib import Path

from . import context_menu
from .bluetooth_link import BluetoothLink
from .control import ControlServer
from .link_server import LinkServer
from .mirror import Mirror
from .store import Store
from .system_bridge import local_ipv4s, notify, open_path
from .usb_link import UsbLink

log = logging.getLogger("connectlinux")


class App:
    def __init__(
        self,
        store: Store | None = None,
        host: str = "0.0.0.0",
        port: int = 17171,
        enable_discovery: bool = True,
        enable_extras: bool = True,
    ) -> None:
        self.store = store or Store()
        self.events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.server = LinkServer(
            self.store,
            host=host,
            port=port,
            enable_discovery=enable_discovery,
            on_event=self._on_event,
        )
        self.enable_extras = enable_extras
        self.bluetooth = BluetoothLink(self._adopt_bluetooth)
        self.usb = UsbLink()
        self.mirror = Mirror(self.usb.adb, self._mirror_message)
        self._bt_status: dict = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._extras_stop = threading.Event()
        self._menu_peers: dict[str, str] | None = None
        self.control: ControlServer | None = None

    def _on_event(self, kind: str, payload: dict) -> None:
        self.events.put((kind, payload))

    def start_control(self) -> bool:
        """Abre o canal do menu de contexto. Devolve False se outra cópia já está aberta."""
        server = ControlServer(self.handle_control)
        try:
            if not server.start():
                return False
        except OSError as exc:
            log.warning("canal local indisponível: %s", exc)
            return True
        self.control = server
        return True

    def handle_control(self, message: dict) -> dict:
        command = message.get("cmd")
        if command == "ping":
            return {"ok": True}
        if command == "present":
            self.events.put(("present", {}))
            return {"ok": True}
        if command == "send":
            files = [Path(str(item)) for item in message.get("files", []) if str(item)]
            return self.send_paths(files, str(message.get("device") or ""), bool(message.get("open", True)))
        return {"ok": False, "message": "comando desconhecido"}

    def send_paths(self, files: list[Path], device_id: str = "", open_after: bool = True, wait: float = 20) -> dict:
        """Envia arquivos ao celular, conectando antes se ele estiver pareado mas fora do ar."""
        files = [path for path in files if path.is_file()]
        if not files:
            return {"ok": False, "message": "Nenhum arquivo para enviar."}
        target = self._online_target(device_id)
        if target is None:
            if device_id and device_id not in self.store.peers():
                return {"ok": False, "message": "Esse celular não está pareado com este computador."}
            self.server.request_connect([device_id] if device_id else list(self.store.peers()))
            deadline = time.monotonic() + wait
            while target is None and time.monotonic() < deadline:
                time.sleep(0.5)
                target = self._online_target(device_id)
        if target is None:
            name = self._peer_name(device_id) or "o celular"
            return {
                "ok": False,
                "message": f"{name} não está conectado. Abra o Connect Linux no celular, na mesma rede.",
            }
        target_id, target_name = target
        for path in files:
            self.send_file(target_id, path, open_after=open_after)
        if len(files) == 1:
            text = f"Enviando {files[0].name} para {target_name}."
        else:
            text = f"Enviando {len(files)} arquivos para {target_name}."
        self._log(text)
        return {"ok": True, "message": text}

    def _online_target(self, device_id: str) -> tuple[str, str] | None:
        online = [row for row in self.snapshot() if row["online"]]
        if device_id:
            online = [row for row in online if row["id"] == device_id]
        if not online:
            return None
        return online[0]["id"], online[0]["name"]

    def _peer_name(self, device_id: str) -> str:
        saved = self.store.peers().get(device_id) if device_id else None
        return str(saved.get("name", "")) if isinstance(saved, dict) else ""

    def sync_context_menu(self) -> None:
        peers = {device_id: str(saved.get("name") or "celular") for device_id, saved in self.store.peers().items()}
        if peers == self._menu_peers:
            return
        self._menu_peers = peers
        try:
            context_menu.sync(peers)
        except OSError as exc:
            log.info("menu de contexto: %s", exc)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="connectlinux", daemon=True)
        self._thread.start()
        if not self.server._started.wait(5):
            self._log("O serviço de rede demorou para iniciar.")
        if self.enable_extras:
            self.bluetooth.start()
            if self.bluetooth.error:
                self._log(self.bluetooth.error)
            threading.Thread(target=self._watch_extras, name="connectlinux-extras", daemon=True).start()

    def _adopt_bluetooth(self, sock, address: str) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            sock.close()
            return
        self._log(f"Conexão Bluetooth recebida de {address}.")
        asyncio.run_coroutine_threadsafe(self.server.adopt_socket(sock), loop)

    def _watch_extras(self) -> None:
        while not self._extras_stop.is_set():
            self.sync_context_menu()
            self._bt_status = self.bluetooth.status()
            for message in self.usb.refresh(self.server.port):
                self._log(message)
            self._extras_stop.wait(4)

    def bluetooth_status(self) -> dict:
        return dict(self._bt_status)

    def usb_status(self) -> tuple[bool, int]:
        return self.usb.adb is not None, self.usb.ready_count()

    def enable_bluetooth(self) -> None:
        def run() -> None:
            error = self.bluetooth.power_on_and_show()
            if error:
                self._log(error)
            else:
                self._log("Bluetooth ligado e visível por 3 minutos. Pareie o celular pelo Bluetooth do sistema.")
            self._bt_status = self.bluetooth.status()

        threading.Thread(target=run, daemon=True).start()

    def _run(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self.server.serve())
        finally:
            with contextlib.suppress(Exception):
                self._loop.close()

    def mirror_screen(self, device_id: str = "") -> None:
        """Abre a tela do celular numa janela, preferindo o selecionado ou o conectado."""
        rows = self.snapshot()
        row = next((item for item in rows if item["id"] == device_id), None)
        if row is None:
            row = next((item for item in rows if item["online"]), None)
        name = row["name"] if row else "o celular"
        host = row.get("host") if row else None
        if host and (host.startswith("127.") or host == "::1"):
            host = None
        self.mirror.start(name or "Celular", host)

    def _mirror_message(self, text: str) -> None:
        self._log(text)
        notify("Espelhar tela", text)

    def stop(self) -> None:
        self.mirror.stop_all()
        if self.control is not None:
            self.control.stop()
            self.control = None
        self._extras_stop.set()
        self.bluetooth.stop()
        self.server.request_stop()
        if self._thread:
            self._thread.join(timeout=3)

    def drain_events(self) -> list[tuple[str, dict]]:
        items = []
        while True:
            try:
                items.append(self.events.get_nowait())
            except queue.Empty:
                return items

    def snapshot(self) -> list[dict]:
        return self.server.snapshot()

    def connect_known_phones(self) -> str:
        """Pede aos celulares já pareados para abrirem a conexão com este computador."""
        paired = self.store.peers()
        rows = {row["id"]: row for row in self.snapshot()}
        if not paired:
            visible = [row["name"] for row in rows.values() if row["seen"] and not row["online"]]
            if len(visible) == 1:
                return f"{visible[0]} está na rede, mas ainda não foi pareado. No celular, toque em Parear."
            if visible:
                return (
                    f"{_join_names(visible)} estão na rede, mas ainda não foram pareados. "
                    "No celular, toque em Parear."
                )
            return "Nenhum celular pareado. Abra o Connect Linux no celular e pareie este computador."

        online: list[str] = []
        pending: list[tuple[str, str]] = []
        for device_id, saved in paired.items():
            row = rows.get(device_id)
            name = ""
            if row and row.get("name"):
                name = str(row["name"])
            elif isinstance(saved, dict) and saved.get("name"):
                name = str(saved["name"])
            name = name or "celular"
            if row and row["online"]:
                online.append(name)
            else:
                pending.append((device_id, name))
        if not pending:
            return "Já conectado a " + _join_names(online) + "."
        if not self.server.request_connect([device_id for device_id, _name in pending]):
            return "A busca na rede ainda não está pronta. Tente de novo em alguns segundos."
        asked = _join_names([name for _device_id, name in pending])
        text = (
            f"Pedindo a {asked} para conectar. "
            "Deixe o Connect Linux aberto no celular, na mesma rede."
        )
        if online:
            text += " Já conectado a " + _join_names(online) + "."
        return text

    @property
    def port(self) -> int:
        return self.server.port

    @property
    def fatal(self) -> str | None:
        return self.server.fatal

    def local_ips(self) -> list[str]:
        return local_ipv4s()

    def set_name(self, name: str) -> str:
        return self.store.set_name(name)

    def set_clipboard_sync(self, enabled: bool) -> None:
        self.store.set_clipboard_sync(enabled)

    def set_show_notifications(self, enabled: bool) -> None:
        self.store.set_show_notifications(enabled)

    def set_download_dir(self, path: Path) -> None:
        self.store.set_download_dir(path)

    def open_download_dir(self) -> bool:
        path = self.store.download_dir
        path.mkdir(parents=True, exist_ok=True)
        return open_path(path)

    def reject_pair(self) -> None:
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(self.server.reject_pairing)

    def forget(self, device_id: str) -> None:
        self._spawn(self.server.forget_device(device_id))

    def send_text(self, device_id: str, text: str) -> None:
        self._spawn(self.server.send_text(device_id, text))

    def send_url(self, device_id: str, url: str) -> None:
        self._spawn(self.server.send_url(device_id, url))

    def send_file(self, device_id: str, path: Path, open_after: bool = False) -> None:
        self._spawn(self.server.send_file(device_id, path, open_after))

    def find_device(self, device_id: str) -> None:
        self._spawn(self.server.find_device(device_id))

    def _spawn(self, coro) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            coro.close()
            self._log("O serviço de rede não está em execução.")
            return
        future = asyncio.run_coroutine_threadsafe(coro, loop)

        def done(finished) -> None:
            try:
                finished.result()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._log(str(exc))

        future.add_done_callback(done)

    def _log(self, text: str) -> None:
        self.events.put(("log", {"text": text}))
        log.info("%s", text)


def _join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " e " + names[-1]
