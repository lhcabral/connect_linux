"""Liga o servidor, a descoberta na rede e a fila de eventos da interface."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
from pathlib import Path

from .bluetooth_link import BluetoothLink
from .link_server import LinkServer
from .store import Store
from .system_bridge import local_ipv4s, open_path
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
        self._bt_status: dict = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._extras_stop = threading.Event()

    def _on_event(self, kind: str, payload: dict) -> None:
        self.events.put((kind, payload))

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

    def stop(self) -> None:
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

    def send_file(self, device_id: str, path: Path) -> None:
        self._spawn(self.server.send_file(device_id, path))

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
