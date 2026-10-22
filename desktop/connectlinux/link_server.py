"""Servidor TCP do computador. O celular conecta, pareia e troca mensagens."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import ipaddress
import logging
import mimetypes
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Callable

from . import system_bridge as bridge
from .discovery import Discovery
from .protocol import (
    CHUNK_RAW,
    LINK_PORT,
    MAX_CHUNK_RAW,
    MAX_CLIPBOARD,
    MAX_FILE_BYTES,
    PAIR_ATTEMPTS,
    PAIR_TTL_SECONDS,
    UUID_RE,
    ProtocolError,
    decode_payload,
    encode_message,
    frame_size,
    now_ms,
    sanitize_name,
)
from .store import Store

log = logging.getLogger("connectlinux")


class Session:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer_host: str) -> None:
        self.reader = reader
        self.writer = writer
        self.peer_host = peer_host
        self.conn_id = secrets.token_hex(8)
        self.device_id = ""
        self.name = ""
        self.platform = ""
        self.authed = False
        self.battery: int | None = None
        self.charging: bool | None = None
        self.latency_ms: int | None = None
        self.last_rx = time.monotonic()
        self.echo_guard: str | None = None
        self.reject_event: asyncio.Event | None = None
        self.write_lock = asyncio.Lock()
        self.file_lock = asyncio.Lock()
        self.incoming: dict | None = None

    async def send(self, message: dict) -> None:
        data = encode_message(message)
        async with self.write_lock:
            self.writer.write(data)
            await self.writer.drain()


class LinkServer:
    def __init__(
        self,
        store: Store,
        host: str = "0.0.0.0",
        port: int = LINK_PORT,
        enable_discovery: bool = True,
        read_clipboard: Callable[[], str | None] | None = None,
        write_clipboard: Callable[[str], bool] | None = None,
        notify: Callable[[str, str], None] | None = None,
        open_url: Callable[[str], bool] | None = None,
        play_sound: Callable[[], None] | None = None,
        read_battery: Callable[[], tuple[int, bool] | None] | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.store = store
        self.host = host
        self.port = port
        self.enable_discovery = enable_discovery
        self.read_clipboard = read_clipboard or bridge.read_clipboard
        self.write_clipboard = write_clipboard or bridge.write_clipboard
        self.notify = notify or bridge.notify
        self.open_url = open_url or bridge.open_url
        self.play_sound = play_sound or bridge.play_find_sound
        self.read_battery = read_battery or bridge.read_battery
        self.on_event = on_event
        self.fatal: str | None = None
        self.discovery = Discovery(store.device_id, lambda: store.name, lambda: self.port)
        self.sessions: dict[str, Session] = {}
        self._pairing: list[Session] = []
        self._lock = threading.Lock()
        self._started = threading.Event()
        self._stop_flag = threading.Event()
        self._signal_stop: Callable[[], None] | None = None
        self._tcp: asyncio.Server | None = None
        self._tasks: list[asyncio.Task] = []
        self._pending_accept: dict[str, asyncio.Future] = {}
        self._pending_done: dict[str, asyncio.Future] = {}
        self._suppress_clip: str | None = None
        self._last_clip: str | None = None
        self._clip_quiet_until = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None

    def request_stop(self) -> None:
        self._stop_flag.set()
        signal = self._signal_stop
        if signal:
            signal()

    async def wait_started(self, timeout: float = 5) -> None:
        ok = await asyncio.to_thread(self._started.wait, timeout)
        if not ok:
            raise TimeoutError("o servidor não iniciou")

    def snapshot(self) -> list[dict]:
        with self._lock:
            sessions = dict(self.sessions)
        peers = self.discovery.snapshot()
        paired = self.store.peers()
        visible = {key for key, value in peers.items() if value.get("platform") == "android"}
        ids = set(paired) | set(sessions) | visible
        rows = []
        for device_id in ids:
            session = sessions.get(device_id)
            peer = peers.get(device_id, {})
            saved = paired.get(device_id, {})
            name = ""
            if session and session.name:
                name = session.name
            elif peer.get("name"):
                name = peer["name"]
            elif saved.get("name"):
                name = saved["name"]
            rows.append(
                {
                    "id": device_id,
                    "name": name or "Celular",
                    "platform": (session.platform if session else peer.get("platform") or saved.get("platform") or ""),
                    "paired": device_id in paired,
                    "online": session is not None,
                    "seen": device_id in peers or session is not None,
                    "battery": None if session is None else session.battery,
                    "charging": None if session is None else session.charging,
                    "latency_ms": None if session is None else session.latency_ms,
                    "host": session.peer_host if session else peer.get("host"),
                    "via": _transport_label(session.peer_host) if session else "",
                }
            )
        rows.sort(key=lambda row: (not row["online"], not row["seen"], row["name"].lower()))
        return rows

    def reject_pairing(self) -> None:
        for session in list(self._pairing):
            event = session.reject_event
            if event is not None and not event.is_set():
                event.set()

    async def send_text(self, device_id: str, text: str) -> None:
        session = self.sessions.get(device_id)
        if session is None:
            raise ConnectionError("celular desconectado")
        await session.send({"type": "text", "text": text[:MAX_CLIPBOARD]})

    async def send_url(self, device_id: str, url: str) -> None:
        if not bridge.valid_http_url(url):
            raise ValueError("use um endereço http ou https")
        session = self.sessions.get(device_id)
        if session is None:
            raise ConnectionError("celular desconectado")
        await session.send({"type": "open_url", "url": url.strip()})

    async def find_device(self, device_id: str) -> None:
        session = self.sessions.get(device_id)
        if session is None:
            raise ConnectionError("celular desconectado")
        await session.send({"type": "find"})

    async def forget_device(self, device_id: str) -> None:
        session = self.sessions.get(device_id)
        self.store.forget(device_id)
        if session is not None:
            with contextlib.suppress(Exception):
                await session.send({"type": "unpair"})
            session.writer.close()

    async def send_file(self, device_id: str, path: Path) -> None:
        session = self.sessions.get(device_id)
        if session is None:
            raise ConnectionError("celular desconectado")
        path = path.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError("arquivo maior que 100 MB")
        digest = _sha256_file(path)
        file_id = secrets.token_hex(16)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        loop = asyncio.get_running_loop()
        async with session.file_lock:
            accept: asyncio.Future = loop.create_future()
            done: asyncio.Future = loop.create_future()
            self._pending_accept[file_id] = accept
            self._pending_done[file_id] = done
            try:
                await session.send(
                    {
                        "type": "file_meta",
                        "id": file_id,
                        "name": path.name,
                        "size": size,
                        "mime": mime,
                        "sha256": digest,
                    }
                )
                try:
                    accepted = await asyncio.wait_for(accept, 120)
                except asyncio.TimeoutError:
                    self._log(f"O celular não aceitou {path.name}.")
                    return
                if not accepted:
                    self._log(f"O celular recusou {path.name}.")
                    return
                self._log(f"Enviando {path.name}…")
                sent = 0
                sequence = 0
                shown = -10
                with path.open("rb") as handle:
                    if size == 0:
                        await session.send(
                            {"type": "file_chunk", "id": file_id, "seq": 0, "last": True, "data": ""}
                        )
                    while sent < size:
                        block = handle.read(min(CHUNK_RAW, size - sent))
                        sent += len(block)
                        last = sent >= size
                        await session.send(
                            {
                                "type": "file_chunk",
                                "id": file_id,
                                "seq": sequence,
                                "last": last,
                                "data": base64.b64encode(block).decode("ascii"),
                            }
                        )
                        sequence += 1
                        percent = int(sent * 100 / size) if size else 100
                        if percent >= shown + 25 or last:
                            shown = percent
                            self._log(f"Enviando {path.name}: {percent}%")
                try:
                    result = await asyncio.wait_for(done, 120)
                except asyncio.TimeoutError:
                    self._log(f"Sem confirmação do celular para {path.name}.")
                    return
                if result:
                    self._log(f"Arquivo enviado: {path.name}")
                else:
                    self._log(f"O celular não concluiu {path.name}.")
            finally:
                self._pending_accept.pop(file_id, None)
                self._pending_done.pop(file_id, None)

    async def serve(self) -> None:
        loop = asyncio.get_running_loop()
        self._loop = loop
        stopper = asyncio.Event()
        if self._stop_flag.is_set():
            stopper.set()
        self._signal_stop = lambda: loop.call_soon_threadsafe(stopper.set)
        if self._stop_flag.is_set():
            stopper.set()
        try:
            self._tcp = await asyncio.start_server(self._on_client, self.host, self.port)
        except OSError as exc:
            self.fatal = f"Não foi possível abrir a porta {self.port}: {exc}"
            self._log(self.fatal)
            self._started.set()
            return
        sockets = self._tcp.sockets or []
        if sockets:
            self.port = sockets[0].getsockname()[1]
        self._started.set()
        self._log(f"Escutando conexões na porta {self.port}.")
        if self.enable_discovery:
            self._tasks.append(asyncio.create_task(self._run_discovery(), name="discovery"))
        self._tasks.append(asyncio.create_task(self._watch_clipboard(), name="clipboard"))
        try:
            await stopper.wait()
        finally:
            for task in self._tasks:
                task.cancel()
            for task in self._tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            for session in list(self.sessions.values()):
                session.writer.close()
            if self._tcp is not None:
                self._tcp.close()
                await self._tcp.wait_closed()

    async def adopt_socket(self, sock: socket.socket) -> None:
        """Atende uma conexão que chegou por outro caminho, como o Bluetooth."""
        try:
            reader, writer = await asyncio.open_connection(sock=sock, limit=2 * 1024 * 1024)
        except (OSError, ValueError) as exc:
            sock.close()
            self._log(f"Conexão recusada: {exc}")
            return
        await self._on_client(reader, writer)

    async def _run_discovery(self) -> None:
        try:
            await self.discovery.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("descoberta encerrada")

    def _log(self, text: str) -> None:
        log.info("%s", text)
        if self.on_event:
            self.on_event("log", {"text": text})

    def _emit_pair(self, session: Session, code: str | None) -> None:
        if self.on_event:
            self.on_event(
                "pair",
                {"conn_id": session.conn_id, "name": session.name, "code": code},
            )

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        host = peer[0] if isinstance(peer, tuple) else ""
        sock = writer.get_extra_info("socket")
        if isinstance(sock, socket.socket):
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        session = Session(reader, writer, host)
        try:
            hello = await asyncio.wait_for(read_message(reader), 20)
            if hello.get("type") != "hello" or hello.get("v") != 1:
                await session.send({"type": "error", "message": "Versão incompatível do Connect Linux."})
                return
            device_id = str(hello.get("id", ""))
            if not UUID_RE.match(device_id):
                await session.send({"type": "error", "message": "Identificador inválido."})
                return
            session.device_id = device_id
            session.name = sanitize_name(hello.get("name", ""))
            session.platform = str(hello.get("platform", "android"))[:20]
            session.last_rx = time.monotonic()
            await session.send(
                {
                    "type": "hello_ack",
                    "v": 1,
                    "id": self.store.device_id,
                    "name": self.store.name,
                    "platform": "linux",
                }
            )
            first = await asyncio.wait_for(read_message(reader), 30)
            session.last_rx = time.monotonic()
            kind = first.get("type")
            if kind == "auth":
                if self.store.verify(device_id, str(first.get("token", ""))):
                    session.authed = True
                    self.store.touch_peer(device_id, session.name)
                    await session.send({"type": "auth_ok"})
                else:
                    await session.send(
                        {
                            "type": "auth_fail",
                            "message": "Este computador não reconhece o pareamento. Pareie de novo.",
                        }
                    )
                    return
            elif kind == "pair_request":
                if not await self._pair(session):
                    return
            else:
                await session.send({"type": "error", "message": "Mensagem inesperada."})
                return
            await self._session_loop(session)
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError, ProtocolError, OSError) as exc:
            log.info("conexão de %s encerrada: %s", session.name or host, exc)
        finally:
            self._abort_incoming(session)
            self._detach(session)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _pair(self, session: Session) -> bool:
        code = f"{secrets.randbelow(1_000_000):06d}"
        reject = asyncio.Event()
        session.reject_event = reject
        self._pairing.append(session)
        self._emit_pair(session, code)
        self._log(f"{session.name} quer parear. Confira o código na tela.")
        await session.send({"type": "pair_required", "name": self.store.name})
        attempts = 0
        deadline = time.monotonic() + PAIR_TTL_SECONDS
        try:
            while attempts < PAIR_ATTEMPTS:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    await session.send({"type": "pair_fail", "message": "O código expirou. Tente de novo."})
                    return False
                read_task = asyncio.create_task(read_message(session.reader))
                reject_task = asyncio.create_task(reject.wait())
                done, pending = await asyncio.wait(
                    {read_task, reject_task},
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
                if reject.is_set():
                    await session.send(
                        {"type": "pair_rejected", "message": "O pareamento foi recusado no computador."}
                    )
                    self._log(f"Pareamento de {session.name} recusado.")
                    return False
                if read_task not in done or read_task.cancelled():
                    await session.send({"type": "pair_fail", "message": "O código expirou. Tente de novo."})
                    return False
                message = read_task.result()
                session.last_rx = time.monotonic()
                if message.get("type") != "pair_submit":
                    continue
                got = str(message.get("code", "")).strip()
                if len(got) == len(code) and hmac.compare_digest(got, code):
                    token = secrets.token_hex(32)
                    self.store.pair(session.device_id, session.name, token, session.platform)
                    session.authed = True
                    await session.send({"type": "pair_ok", "token": token})
                    self._log(f"Pareado com {session.name}.")
                    self.notify("Celular pareado", session.name)
                    return True
                attempts += 1
                left = PAIR_ATTEMPTS - attempts
                await session.send(
                    {
                        "type": "pair_fail",
                        "message": "Código incorreto." if left else "Número de tentativas esgotado.",
                        "attempts_left": left,
                    }
                )
            return False
        finally:
            if session in self._pairing:
                self._pairing.remove(session)
            session.reject_event = None
            self._emit_pair(session, None)

    async def _session_loop(self, session: Session) -> None:
        self._attach(session)
        self._log(f"{session.name} conectado.")
        self.notify("Celular conectado", session.name)
        await self._send_battery(session)
        await session.send({"type": "ping", "ts": now_ms()})
        pinger = asyncio.create_task(self._pinger(session))
        try:
            while not self._stop_flag.is_set():
                message = await read_message(session.reader)
                session.last_rx = time.monotonic()
                await self._dispatch(session, message)
        finally:
            pinger.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pinger
            self._detach(session)
            self._log(f"{session.name} desconectado.")

    def _attach(self, session: Session) -> None:
        with self._lock:
            previous = self.sessions.get(session.device_id)
            self.sessions[session.device_id] = session
        if previous is not None and previous is not session:
            previous.writer.close()

    def _detach(self, session: Session) -> None:
        with self._lock:
            current = self.sessions.get(session.device_id)
            if current is session:
                del self.sessions[session.device_id]
        if session in self._pairing:
            self._pairing.remove(session)

    async def _pinger(self, session: Session) -> None:
        ticks = 0
        while True:
            await asyncio.sleep(15)
            if time.monotonic() - session.last_rx > 50:
                session.writer.close()
                return
            ticks += 1
            with contextlib.suppress(Exception):
                await session.send({"type": "ping", "ts": now_ms()})
                if ticks % 4 == 0:
                    await self._send_battery(session)

    async def _send_battery(self, session: Session) -> None:
        reading = self.read_battery()
        if reading is None:
            return
        level, charging = reading
        with contextlib.suppress(Exception):
            await session.send({"type": "battery", "level": level, "charging": charging})

    async def _watch_clipboard(self) -> None:
        while not self._stop_flag.is_set():
            await asyncio.sleep(1.2)
            if not self.store.clipboard_sync:
                continue
            if time.monotonic() < self._clip_quiet_until:
                continue
            text = await asyncio.get_running_loop().run_in_executor(None, self.read_clipboard)
            if text is None:
                continue
            if self._suppress_clip is not None and text == self._suppress_clip:
                self._last_clip = text
                self._suppress_clip = None
                continue
            if text == self._last_clip or len(text) > MAX_CLIPBOARD:
                continue
            self._last_clip = text
            for session in list(self.sessions.values()):
                if session.echo_guard == text:
                    session.echo_guard = None
                    continue
                with contextlib.suppress(Exception):
                    await session.send({"type": "clipboard", "text": text, "ts": now_ms()})

    async def _dispatch(self, session: Session, message: dict) -> None:
        kind = message.get("type")
        if kind == "ping":
            ts = message.get("ts")
            await session.send({"type": "pong", "ts": ts if isinstance(ts, int) else now_ms()})
            return
        if kind == "pong":
            ts = message.get("ts")
            if isinstance(ts, int):
                delay = now_ms() - ts
                if 0 <= delay < 60_000:
                    session.latency_ms = delay
            return
        if kind == "clipboard":
            await self._on_clipboard(session, message)
            return
        if kind == "notification":
            self._on_notification(session, message)
            return
        if kind == "battery":
            level = message.get("level")
            if isinstance(level, int) and 0 <= level <= 100:
                session.battery = level
                session.charging = bool(message.get("charging"))
            return
        if kind == "text":
            text = str(message.get("text", ""))[:MAX_CLIPBOARD]
            if text:
                self._log(f"Texto de {session.name}: {text[:180]}")
                self.notify(f"Texto de {session.name}", text)
            return
        if kind == "open_url":
            url = str(message.get("url", "")).strip()
            if self.open_url(url):
                self._log(f"Abrindo link enviado por {session.name}.")
                self.notify("Link do celular", url)
            else:
                self._log("Link recusado. Use apenas http ou https.")
            return
        if kind == "find":
            self._log(f"{session.name} chamou este computador.")
            self.notify("Chamada do celular", "O celular está procurando este computador.")
            self.play_sound()
            return
        if kind == "unpair":
            self.store.forget(session.device_id)
            self._log(f"{session.name} removeu o pareamento.")
            session.writer.close()
            raise ConnectionError("pareamento removido")
        if kind == "file_meta":
            await self._on_file_meta(session, message)
            return
        if kind == "file_chunk":
            await self._on_file_chunk(session, message)
            return
        if kind == "file_accept":
            self._finish_wait(self._pending_accept, str(message.get("id", "")), True)
            return
        if kind == "file_reject":
            self._finish_wait(self._pending_accept, str(message.get("id", "")), False)
            self._finish_wait(self._pending_done, str(message.get("id", "")), False)
            return
        if kind == "file_complete":
            self._finish_wait(self._pending_done, str(message.get("id", "")), bool(message.get("ok")))
            return
        if kind == "file_error":
            self._finish_wait(self._pending_done, str(message.get("id", "")), False)
            self._log(f"Falha no arquivo: {message.get('message', 'erro')}")
            return

    async def _on_clipboard(self, session: Session, message: dict) -> None:
        if not self.store.clipboard_sync:
            return
        text = str(message.get("text", ""))
        if not text or len(text) > MAX_CLIPBOARD:
            return
        session.echo_guard = text
        self._suppress_clip = text
        self._last_clip = text
        self._clip_quiet_until = time.monotonic() + 1.5
        ok = await asyncio.get_running_loop().run_in_executor(None, self.write_clipboard, text)
        if ok:
            self._log(f"Área de transferência atualizada por {session.name}.")
        else:
            self._suppress_clip = None

    def _on_notification(self, session: Session, message: dict) -> None:
        if not self.store.show_notifications:
            return
        title = " ".join(str(message.get("title", "")).split())[:180]
        body = " ".join(str(message.get("body", "")).split())[:600]
        app_name = " ".join(str(message.get("app", session.name)).split())[:80]
        if not title and not body:
            return
        self.notify(title or app_name, body or app_name)
        self._log(f"Notificação de {app_name}: {title}")

    async def _on_file_meta(self, session: Session, message: dict) -> None:
        file_id = str(message.get("id", ""))
        name = str(message.get("name", "arquivo"))
        try:
            size = int(message.get("size", -1))
        except (TypeError, ValueError):
            size = -1
        digest = str(message.get("sha256", "")).lower()
        if session.incoming is not None:
            await session.send({"type": "file_reject", "id": file_id, "message": "Já existe um arquivo em recebimento."})
            return
        if not file_id or size < 0 or size > MAX_FILE_BYTES or len(digest) != 64:
            await session.send({"type": "file_reject", "id": file_id, "message": "Arquivo recusado."})
            return
        try:
            final = bridge.destination_for(self.store.download_dir, name)
        except (OSError, ValueError) as exc:
            await session.send({"type": "file_reject", "id": file_id, "message": "Não foi possível criar o arquivo."})
            log.info("destino inválido: %s", exc)
            return
        part = final.with_name(final.name + ".part")
        handle = part.open("wb")
        session.incoming = {
            "id": file_id,
            "name": final.name,
            "final": final,
            "part": part,
            "size": size,
            "sha": digest,
            "fh": handle,
            "hash": hashlib.sha256(),
            "got": 0,
            "seq": 0,
            "shown": -10,
        }
        await session.send({"type": "file_accept", "id": file_id})
        self._log(f"Recebendo {final.name} de {session.name}.")

    async def _on_file_chunk(self, session: Session, message: dict) -> None:
        incoming = session.incoming
        file_id = str(message.get("id", ""))
        if incoming is None or incoming["id"] != file_id:
            return
        try:
            sequence = int(message.get("seq", -1))
            raw = base64.b64decode(str(message.get("data", "")), validate=True)
        except (TypeError, ValueError):
            await self._fail_incoming(session, "Trecho do arquivo inválido.")
            return
        if sequence != incoming["seq"] or len(raw) > MAX_CHUNK_RAW:
            await self._fail_incoming(session, "Ordem do arquivo inválida.")
            return
        incoming["got"] += len(raw)
        if incoming["got"] > incoming["size"]:
            await self._fail_incoming(session, "Arquivo maior do que o anunciado.")
            return
        last = bool(message.get("last"))
        if last and incoming["got"] != incoming["size"]:
            await self._fail_incoming(session, "Arquivo incompleto.")
            return
        if not last and incoming["got"] == incoming["size"]:
            await self._fail_incoming(session, "Arquivo incompleto.")
            return
        incoming["fh"].write(raw)
        incoming["hash"].update(raw)
        incoming["seq"] += 1
        size = incoming["size"]
        percent = 100 if size == 0 else int(incoming["got"] * 100 / size)
        if percent >= incoming["shown"] + 25 or last:
            incoming["shown"] = percent
            self._log(f"Recebendo {incoming['name']}: {percent}%")
        if not last:
            return
        incoming["fh"].close()
        actual = incoming["hash"].hexdigest()
        if actual != incoming["sha"]:
            incoming["part"].unlink(missing_ok=True)
            session.incoming = None
            await session.send({"type": "file_error", "id": file_id, "message": "O arquivo chegou corrompido."})
            self._log(f"Arquivo corrompido: {incoming['name']}")
            return
        incoming["part"].replace(incoming["final"])
        saved = incoming["final"]
        session.incoming = None
        await session.send({"type": "file_complete", "id": file_id, "ok": True, "sha256": actual})
        self._log(f"Arquivo salvo em {saved}")
        self.notify("Arquivo recebido", saved.name)

    async def _fail_incoming(self, session: Session, reason: str) -> None:
        incoming = session.incoming
        file_id = incoming["id"] if incoming else ""
        name = incoming["name"] if incoming else "arquivo"
        self._abort_incoming(session)
        if file_id:
            with contextlib.suppress(Exception):
                await session.send({"type": "file_error", "id": file_id, "message": reason})
        self._log(f"Falha ao receber {name}: {reason}")

    def _abort_incoming(self, session: Session) -> None:
        incoming = session.incoming
        if not incoming:
            return
        session.incoming = None
        with contextlib.suppress(Exception):
            incoming["fh"].close()
        with contextlib.suppress(OSError):
            incoming["part"].unlink(missing_ok=True)

    def _finish_wait(self, table: dict[str, asyncio.Future], file_id: str, value: bool) -> None:
        future = table.get(file_id)
        if future is not None and not future.done():
            future.set_result(value)


def _transport_label(host: str) -> str:
    if host in {"127.0.0.1", "::1"}:
        return "cabo USB"
    if host.count(":") == 5 and len(host) == 17:
        return "Bluetooth"
    try:
        if ipaddress.ip_address(host) in _TAILSCALE_NET:
            return "Tailscale"
    except ValueError:
        pass
    return "Wi-Fi"


_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


async def read_message(reader: asyncio.StreamReader) -> dict:
    header = await reader.readexactly(4)
    length = frame_size(header)
    payload = await reader.readexactly(length)
    return decode_payload(payload)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
