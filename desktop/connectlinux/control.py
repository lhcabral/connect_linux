"""Canal local entre o programa aberto e os comandos do menu de contexto."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger("connectlinux")

MAX_REQUEST = 256 * 1024


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/connectlinux-{os.getuid()}"
    return Path(runtime) / "connectlinux" / "control.sock"


class ControlServer:
    """Atende pedidos JSON, um por conexão, de processos do mesmo usuário."""

    def __init__(self, handler: Callable[[dict], dict], path: Path | None = None) -> None:
        self.handler = handler
        self.path = path or socket_path()
        self._sock: socket.socket | None = None
        self._stop = threading.Event()

    def start(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        if request({"cmd": "ping"}, path=self.path, timeout=1) is not None:
            return False
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.path))
        os.chmod(self.path, 0o600)
        server.listen(8)
        server.settimeout(0.5)
        self._sock = server
        threading.Thread(target=self._serve, name="connectlinux-control", daemon=True).start()
        return True

    def stop(self) -> None:
        self._stop.set()
        server = self._sock
        self._sock = None
        if server is not None:
            with contextlib.suppress(OSError):
                server.close()
            with contextlib.suppress(OSError):
                self.path.unlink()

    def _serve(self) -> None:
        while not self._stop.is_set():
            server = self._sock
            if server is None:
                return
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._answer, args=(conn,), daemon=True).start()

    def _answer(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(60)
            try:
                if _peer_uid(conn) not in (None, os.getuid()):
                    return
                data = _read_line(conn)
                message = json.loads(data.decode("utf-8")) if data else {}
                reply = self.handler(message if isinstance(message, dict) else {})
            except Exception as exc:
                log.exception("controle")
                reply = {"ok": False, "message": str(exc)}
            with contextlib.suppress(OSError):
                conn.sendall(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")


def request(message: dict, path: Path | None = None, timeout: float = 60) -> dict | None:
    """Envia um pedido ao programa aberto. Devolve None se ele não estiver rodando."""
    target = path or socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(target))
            client.sendall(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
            data = _read_line(client)
    except (OSError, socket.timeout):
        return None
    try:
        reply = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return reply if isinstance(reply, dict) else None


def _read_line(conn: socket.socket) -> bytes:
    chunks = bytearray()
    while b"\n" not in chunks:
        block = conn.recv(65536)
        if not block:
            break
        chunks.extend(block)
        if len(chunks) > MAX_REQUEST:
            raise ValueError("pedido grande demais")
    return bytes(chunks).split(b"\n", 1)[0]


def _peer_uid(conn: socket.socket) -> int | None:
    try:
        import struct

        creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        return struct.unpack("3i", creds)[1]
    except (OSError, AttributeError):
        return None
