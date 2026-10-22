"""Configuração local do computador: identidade, pareamentos e preferências."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import hmac

from .protocol import new_device_id, sanitize_name


def default_config_path() -> Path:
    return Path.home() / ".config" / "connectlinux" / "config.json"


def default_download_dir() -> Path:
    return Path.home() / "ConnectLinux" / "Recebidos"


class Store:
    def __init__(self, path: Path | None = None, download_dir: Path | None = None) -> None:
        self.path = path or default_config_path()
        self._lock = threading.Lock()
        self._data = {
            "device_id": new_device_id(),
            "name": "",
            "clipboard_sync": True,
            "show_notifications": True,
            "download_dir": "",
            "peers": {},
        }
        self._load()
        if download_dir is not None:
            self._data["download_dir"] = str(download_dir)
        if not self._data.get("name"):
            self._data["name"] = _host_name()
            self._save_locked()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(raw, dict):
            self._data.update({k: raw[k] for k in self._data if k in raw})
            if not isinstance(self._data.get("peers"), dict):
                self._data["peers"] = {}

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    @property
    def device_id(self) -> str:
        with self._lock:
            return str(self._data["device_id"])

    @property
    def name(self) -> str:
        with self._lock:
            return str(self._data["name"])

    def set_name(self, name: str) -> str:
        clean = sanitize_name(name)
        with self._lock:
            self._data["name"] = clean
            self._save_locked()
        return clean

    @property
    def clipboard_sync(self) -> bool:
        with self._lock:
            return bool(self._data["clipboard_sync"])

    def set_clipboard_sync(self, enabled: bool) -> None:
        with self._lock:
            self._data["clipboard_sync"] = bool(enabled)
            self._save_locked()

    @property
    def show_notifications(self) -> bool:
        with self._lock:
            return bool(self._data["show_notifications"])

    def set_show_notifications(self, enabled: bool) -> None:
        with self._lock:
            self._data["show_notifications"] = bool(enabled)
            self._save_locked()

    @property
    def download_dir(self) -> Path:
        with self._lock:
            raw = str(self._data.get("download_dir") or "")
        path = Path(raw).expanduser() if raw else default_download_dir()
        return path

    def set_download_dir(self, path: Path) -> None:
        with self._lock:
            self._data["download_dir"] = str(path.expanduser())
            self._save_locked()

    def peers(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data["peers"]))

    def is_paired(self, device_id: str) -> bool:
        with self._lock:
            return device_id in self._data["peers"]

    def verify(self, device_id: str, token: str) -> bool:
        with self._lock:
            peer = self._data["peers"].get(device_id)
            if not peer:
                return False
            saved = str(peer.get("token", ""))
        if not saved or not token or len(saved) != len(token):
            return False
        return hmac.compare_digest(saved, token)

    def pair(self, device_id: str, name: str, token: str, platform: str) -> None:
        with self._lock:
            self._data["peers"][device_id] = {
                "name": sanitize_name(name),
                "token": token,
                "platform": str(platform)[:20],
                "paired_at": int(time.time()),
            }
            self._save_locked()

    def touch_peer(self, device_id: str, name: str) -> None:
        with self._lock:
            peer = self._data["peers"].get(device_id)
            if not peer:
                return
            peer["name"] = sanitize_name(name)
            self._save_locked()

    def forget(self, device_id: str) -> None:
        with self._lock:
            self._data["peers"].pop(device_id, None)
            self._save_locked()


def _host_name() -> str:
    import socket

    name = socket.gethostname().split(".")[0]
    return sanitize_name(name)
