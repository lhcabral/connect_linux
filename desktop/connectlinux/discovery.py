"""Anúncio do computador na rede local e escuta dos celulares."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
import time
from typing import Callable

from .protocol import (
    APP,
    DISCOVERY_PORT,
    MULTICAST_GROUP,
    UUID_RE,
    VERSION,
    sanitize_name,
)

log = logging.getLogger("connectlinux")


class Discovery:
    def __init__(self, device_id: str, name_fn: Callable[[], str], port_fn: Callable[[], int]) -> None:
        self.device_id = device_id
        self._name_fn = name_fn
        self._port_fn = port_fn
        self.peers: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.transport: asyncio.DatagramTransport | None = None
        self.fatal: str | None = None

    def snapshot(self) -> dict[str, dict]:
        now = time.monotonic()
        with self._lock:
            stale = [key for key, value in self.peers.items() if now - value["seen"] > 12]
            for key in stale:
                del self.peers[key]
            return {key: dict(value) for key, value in self.peers.items()}

    def announce_bytes(self) -> bytes:
        message = {
            "app": APP,
            "v": VERSION,
            "type": "announce",
            "id": self.device_id,
            "name": self._name_fn(),
            "port": self._port_fn(),
            "platform": "linux",
        }
        return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            transport, _protocol = await loop.create_datagram_endpoint(
                lambda: _Protocol(self),
                local_addr=("0.0.0.0", DISCOVERY_PORT),
                allow_broadcast=True,
                family=socket.AF_INET,
            )
        except OSError as exc:
            self.fatal = str(exc)
            log.warning("Descoberta na rede indisponível (UDP %s): %s", DISCOVERY_PORT, exc)
            return

        self.transport = transport
        sock = transport.get_extra_info("socket")
        if isinstance(sock, socket.socket):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                membership = socket.inet_aton(MULTICAST_GROUP) + socket.inet_aton("0.0.0.0")
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            except OSError:
                log.info("Multicast indisponível; a busca segue por broadcast.")
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            except OSError:
                pass

        try:
            while True:
                self._broadcast(self.announce_bytes())
                discover = json.dumps(
                    {"app": APP, "v": VERSION, "type": "discover", "id": self.device_id},
                    separators=(",", ":"),
                ).encode("utf-8")
                self._broadcast(discover)
                await asyncio.sleep(2.5)
        finally:
            transport.close()
            self.transport = None

    def handle_packet(self, data: bytes, addr: tuple) -> None:
        if len(data) > 2048:
            return
        try:
            message = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(message, dict):
            return
        if message.get("app") != APP or message.get("v") != VERSION:
            return
        if message.get("id") == self.device_id:
            return
        kind = message.get("type")
        host = str(addr[0])
        if kind == "discover":
            self._send(self.announce_bytes(), (host, DISCOVERY_PORT))
            return
        if kind != "announce":
            return
        peer_id = str(message.get("id", ""))
        if not UUID_RE.match(peer_id):
            return
        try:
            port = int(message.get("port", 0))
        except (TypeError, ValueError):
            return
        if not 1 <= port <= 65535:
            return
        record = {
            "id": peer_id,
            "name": sanitize_name(message.get("name", "")),
            "port": port,
            "platform": str(message.get("platform", ""))[:20],
            "host": host,
            "seen": time.monotonic(),
        }
        with self._lock:
            self.peers[peer_id] = record

    def _broadcast(self, payload: bytes) -> None:
        self._send(payload, ("255.255.255.255", DISCOVERY_PORT))
        self._send(payload, (MULTICAST_GROUP, DISCOVERY_PORT))

    def _send(self, payload: bytes, dest: tuple[str, int]) -> None:
        transport = self.transport
        if transport is None:
            return
        try:
            transport.sendto(payload, dest)
        except OSError:
            return


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, discovery: Discovery) -> None:
        self.discovery = discovery

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self.discovery.handle_packet(data, addr)

    def error_received(self, exc: Exception) -> None:
        log.debug("udp: %s", exc)
