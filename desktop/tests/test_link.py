"""Testes do protocolo e de uma conversa completa entre computador e celular."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from connectlinux.discovery import Discovery
from connectlinux.link_server import LinkServer, read_message
from connectlinux.protocol import decode_payload, encode_message, frame_size
from connectlinux.store import Store
from connectlinux.system_bridge import destination_for, sanitize_filename, valid_http_url
from connectlinux.updater import is_newer, parse_version


class ProtocolTests(unittest.TestCase):
    def test_frame_roundtrip(self) -> None:
        encoded = encode_message({"type": "ping", "text": "ação"})
        length = frame_size(encoded[:4])
        self.assertEqual(decode_payload(encoded[4:])["text"], "ação")
        self.assertEqual(length, len(encoded) - 4)

    def test_filename_stays_inside_folder(self) -> None:
        self.assertEqual(sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_filename("..\\..\\secreto.txt"), "secreto.txt")
        self.assertEqual(sanitize_filename(""), "arquivo")
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            path = destination_for(folder, "../../etc/passwd")
            self.assertEqual(path.parent, folder.resolve())
            self.assertEqual(path.name, "passwd")

    def test_only_http_links(self) -> None:
        self.assertTrue(valid_http_url("https://exemplo.com/a"))
        self.assertFalse(valid_http_url("javascript:alert(1)"))
        self.assertFalse(valid_http_url("https://exemplo.com/a b"))
        self.assertFalse(valid_http_url("file:///etc/passwd"))

    def test_pix_payload(self) -> None:
        from connectlinux import pix

        self.assertEqual(pix.crc16(b"123456789"), 0x29B1)
        code = pix.payload()
        self.assertTrue(code.startswith("000201"))
        self.assertIn("0014br.gov.bcb.pix0125luizhenrique.be@gmail.com", code)
        self.assertIn("5303986", code)
        self.assertEqual(code[-8:-4], "6304")
        self.assertEqual(code[-4:], f"{pix.crc16(code[:-4].encode()):04X}")

    def test_version_compare(self) -> None:
        self.assertEqual(parse_version("v1.0.10"), (1, 0, 10))
        self.assertTrue(is_newer("1.0.10", "1.0.9"))
        self.assertTrue(is_newer("v1.1.0", "1.0.2"))
        self.assertFalse(is_newer("1.0.2", "1.0.2"))
        self.assertFalse(is_newer("1.0.1", "1.0.2"))


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = Store(path=root / "config.json", download_dir=root / "recebidos")
        self.clips: list[str] = []
        self.notes: list[tuple[str, str]] = []
        self.urls: list[str] = []
        self.sounds: list[str] = []
        self.codes: list[str] = []
        self.server = LinkServer(
            self.store,
            host="127.0.0.1",
            port=0,
            enable_discovery=False,
            read_clipboard=lambda: None,
            write_clipboard=self._save_clip,
            notify=lambda title, body: self.notes.append((title, body)),
            open_url=self._save_url,
            play_sound=lambda: self.sounds.append("tocou"),
            read_battery=lambda: None,
            on_event=self._on_event,
            open_file=lambda path: self.opened.append(path) or True,
        )
        self.opened: list[Path] = []
        self.task = asyncio.create_task(self.server.serve())
        await self.server.wait_started()

    async def asyncTearDown(self) -> None:
        self.server.request_stop()
        await asyncio.wait_for(self.task, 5)
        self.tmp.cleanup()

    def _save_clip(self, text: str) -> bool:
        self.clips.append(text)
        return True

    def _save_url(self, url: str) -> bool:
        self.urls.append(url)
        return True

    def _on_event(self, kind: str, payload: dict) -> None:
        if kind == "pair" and payload.get("code"):
            self.codes.append(str(payload["code"]))

    async def test_pair_clipboard_file_and_reconnect(self) -> None:
        device_id = str(uuid.uuid4())
        peer = await self._connect(device_id, "Pixel de teste")
        await peer.recv_type("hello_ack")
        await peer.send({"type": "pair_request"})
        await peer.recv_type("pair_required")
        wrong = "000000" if self.codes[-1] != "000000" else "111111"
        await peer.send({"type": "pair_submit", "code": wrong})
        refused = await peer.recv_type("pair_fail")
        self.assertIn("incorreto", refused["message"])

        await peer.send({"type": "pair_submit", "code": self.codes[-1]})
        paired = await peer.recv_type("pair_ok")
        token = paired["token"]
        self.assertTrue(self.store.verify(device_id, token))

        await peer.send({"type": "clipboard", "text": "ação e café", "ts": 1})
        await peer.send({"type": "ping", "ts": 1})
        await peer.recv_type("pong")
        self.assertEqual(self.clips, ["ação e café"])

        await peer.send(
            {
                "type": "notification",
                "id": "n1",
                "app": "Mensagens",
                "title": "Oi",
                "body": "olá",
                "ts": 1,
            }
        )
        await peer.send({"type": "open_url", "url": "https://linuxmint.com"})
        await peer.send({"type": "find"})
        await peer.send({"type": "ping", "ts": 2})
        await peer.recv_type("pong")
        self.assertTrue(any(title == "Oi" for title, _body in self.notes))
        self.assertEqual(self.urls, ["https://linuxmint.com"])
        self.assertEqual(self.sounds, ["tocou"])

        payload = "arquivo de prova\n".encode()
        digest = hashlib.sha256(payload).hexdigest()
        file_id = "abc123"
        await peer.send(
            {
                "type": "file_meta",
                "id": file_id,
                "name": "../../etc/passwd",
                "size": len(payload),
                "mime": "text/plain",
                "sha256": digest,
            }
        )
        accepted = await peer.recv_type("file_accept")
        self.assertEqual(accepted["id"], file_id)
        await peer.send(
            {
                "type": "file_chunk",
                "id": file_id,
                "seq": 0,
                "last": True,
                "data": base64.b64encode(payload).decode("ascii"),
            }
        )
        done = await peer.recv_type("file_complete")
        self.assertTrue(done["ok"])
        saved = self.store.download_dir / "passwd"
        self.assertEqual(saved.read_bytes(), payload)
        self.assertEqual(self.opened, [])

        picture = b"\x89PNG\r\n\x1a\nfalso"
        for name, should_open in (("foto.png", True), ("script.sh", False)):
            await peer.send(
                {
                    "type": "file_meta",
                    "id": name,
                    "name": name,
                    "size": len(picture),
                    "mime": "image/png",
                    "sha256": hashlib.sha256(picture).hexdigest(),
                    "open": True,
                }
            )
            await peer.recv_type("file_accept")
            await peer.send(
                {
                    "type": "file_chunk",
                    "id": name,
                    "seq": 0,
                    "last": True,
                    "data": base64.b64encode(picture).decode("ascii"),
                }
            )
            await peer.recv_type("file_complete")
            opened = self.store.download_dir / name in self.opened
            self.assertEqual(opened, should_open, name)

        peer.writer.close()
        await peer.writer.wait_closed()
        await self._wait_offline()

        again = await self._connect(device_id, "Pixel de teste")
        await again.recv_type("hello_ack")
        await again.send({"type": "auth", "token": token})
        await again.recv_type("auth_ok")

        outgoing = self.store.download_dir / "volta.txt"
        outgoing.write_text("de volta", encoding="utf-8")
        received: list[bytes] = []

        async def accept_outgoing() -> None:
            meta = await again.recv_type("file_meta")
            self.assertTrue(meta["open"])
            await again.send({"type": "file_accept", "id": meta["id"]})
            chunk = await again.recv_type("file_chunk")
            self.assertTrue(chunk["last"])
            received.append(base64.b64decode(chunk["data"]))
            await again.send(
                {"type": "file_complete", "id": meta["id"], "ok": True, "sha256": meta["sha256"]}
            )

        incoming = asyncio.create_task(accept_outgoing())
        await self.server.send_file(device_id, outgoing, open_after=True)
        await incoming
        self.assertEqual(received, [b"de volta"])

        await again.send({"type": "unpair"})
        await self._wait_offline()
        self.assertFalse(self.store.is_paired(device_id))
        again.writer.close()

    async def test_adopted_socket_pairs_like_tcp(self) -> None:
        import socket as socket_module

        server_end, phone_end = socket_module.socketpair()
        server_end.setblocking(False)
        adopted = asyncio.create_task(self.server.adopt_socket(server_end))
        reader, writer = await asyncio.open_connection(sock=phone_end)
        peer = _Peer(reader, writer)
        await peer.send(
            {"type": "hello", "v": 1, "id": str(uuid.uuid4()), "name": "Pelo Bluetooth", "platform": "android"}
        )
        await peer.recv_type("hello_ack")
        await peer.send({"type": "pair_request"})
        await peer.recv_type("pair_required")
        await peer.send({"type": "pair_submit", "code": self.codes[-1]})
        await peer.recv_type("pair_ok")
        self.assertTrue(any(row["online"] for row in self.server.snapshot()))
        writer.close()
        await asyncio.wait_for(adopted, 5)

    async def test_corrupt_file_is_discarded(self) -> None:
        device_id = str(uuid.uuid4())
        peer = await self._connect(device_id, "Outro celular")
        await peer.recv_type("hello_ack")
        await peer.send({"type": "pair_request"})
        await peer.recv_type("pair_required")
        await peer.send({"type": "pair_submit", "code": self.codes[-1]})
        await peer.recv_type("pair_ok")

        payload = b"dados"
        await peer.send(
            {
                "type": "file_meta",
                "id": "ruim",
                "name": "nota.txt",
                "size": len(payload),
                "mime": "text/plain",
                "sha256": hashlib.sha256(b"outro").hexdigest(),
            }
        )
        await peer.recv_type("file_accept")
        await peer.send(
            {
                "type": "file_chunk",
                "id": "ruim",
                "seq": 0,
                "last": True,
                "data": base64.b64encode(payload).decode("ascii"),
            }
        )
        failed = await peer.recv_type("file_error")
        self.assertIn("corrompido", failed["message"])
        self.assertFalse((self.store.download_dir / "nota.txt").exists())
        self.assertFalse((self.store.download_dir / "nota.txt.part").exists())
        peer.writer.close()

    async def _connect(self, device_id: str, name: str):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.server.port)
        peer = _Peer(reader, writer)
        await peer.send(
            {"type": "hello", "v": 1, "id": device_id, "name": name, "platform": "android"}
        )
        return peer

    async def _wait_offline(self) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not any(row["online"] for row in self.server.snapshot()):
                return
            await asyncio.sleep(0.02)
        self.fail("a sessão deveria ter caído")


class _Peer:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        try:
            while True:
                await self.inbox.put(await read_message(self.reader))
        except Exception:
            await self.inbox.put(None)

    async def send(self, message: dict) -> None:
        self.writer.write(encode_message(message))
        await self.writer.drain()

    async def recv_type(self, kind: str, timeout: float = 5):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(kind)
            message = await asyncio.wait_for(self.inbox.get(), remaining)
            if message is None:
                raise ConnectionError(kind)
            if message.get("type") == kind:
                return message


class DiscoveryTests(unittest.TestCase):
    def test_connect_invite_names_the_phone(self) -> None:
        phone = "22222222-2222-2222-2222-222222222222"
        discovery = Discovery("11111111-1111-1111-1111-111111111111", lambda: "PC", lambda: 17171)
        message = json.loads(discovery.connect_bytes(phone))
        self.assertEqual(message["type"], "connect")
        self.assertEqual(message["platform"], "linux")
        self.assertEqual(message["target"], phone)
        self.assertEqual(message["port"], 17171)
        self.assertFalse(discovery.request_connect([phone]))

    def test_connect_packet_is_not_a_peer(self) -> None:
        discovery = Discovery("11111111-1111-1111-1111-111111111111", lambda: "PC", lambda: 17171)
        foreign = json.dumps(
            {
                "app": "connectlinux",
                "v": 1,
                "type": "connect",
                "id": "33333333-3333-3333-3333-333333333333",
                "name": "Outro",
                "port": 17171,
                "platform": "linux",
                "target": "22222222-2222-2222-2222-222222222222",
            },
            separators=(",", ":"),
        ).encode()
        discovery.handle_packet(foreign, ("192.168.0.8", 17170))
        self.assertEqual(discovery.snapshot(), {})

    def test_connect_explains_when_nothing_is_paired(self) -> None:
        from connectlinux.app import App

        with tempfile.TemporaryDirectory() as temporary:
            store = Store(path=Path(temporary) / "config.json")
            app = App(store=store, enable_discovery=False, enable_extras=False)
            self.assertIn("Nenhum celular pareado", app.connect_known_phones())


class DesktopIntegrationTests(unittest.TestCase):
    def test_context_menu_follows_paired_phones(self) -> None:
        from connectlinux import context_menu

        phone = "22222222-2222-2222-2222-222222222222"
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            self.assertTrue(context_menu.sync({phone: 'Galaxy "A54"; teste'}, base=base))
            action = base / "nemo" / "actions" / "connectlinux-send-22222222.nemo_action"
            entry = base / "applications" / "connectlinux-send-22222222.desktop"
            text = action.read_text(encoding="utf-8")
            self.assertIn("Name=Enviar para o Android “Galaxy A54 teste”", text)
            self.assertIn(f"--send --device {phone} %F", text)
            self.assertIn("image/jpeg", text)
            self.assertIn("NoDisplay=true", entry.read_text(encoding="utf-8"))
            self.assertFalse(context_menu.sync({phone: 'Galaxy "A54"; teste'}, base=base))
            context_menu.sync({}, base=base)
            self.assertFalse(action.exists())
            self.assertFalse(entry.exists())

    def test_autostart_on_by_default_and_respects_opt_out(self) -> None:
        from connectlinux import autostart

        with tempfile.TemporaryDirectory() as temporary:
            entry = Path(temporary) / "autostart" / "connectlinux.desktop"
            self.assertFalse(autostart.is_enabled(entry))
            autostart.ensure_default(entry)
            self.assertTrue(autostart.is_enabled(entry))
            self.assertIn(" --background", entry.read_text(encoding="utf-8"))
            self.assertTrue(autostart.set_enabled(False, entry))
            autostart.ensure_default(entry)
            self.assertFalse(autostart.is_enabled(entry))
            entry.write_text(entry.read_text(encoding="utf-8").replace("Hidden=true", "Hidden=false"), encoding="utf-8")
            self.assertFalse(autostart.is_enabled(entry))

    def test_control_socket_roundtrip(self) -> None:
        from connectlinux import control

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "c" / "control.sock"
            server = control.ControlServer(lambda message: {"ok": True, "echo": message.get("cmd")}, path=path)
            self.assertTrue(server.start())
            try:
                self.assertEqual(control.request({"cmd": "send"}, path=path, timeout=5), {"ok": True, "echo": "send"})
                second = control.ControlServer(lambda _message: {}, path=path)
                self.assertFalse(second.start())
            finally:
                server.stop()
            self.assertIsNone(control.request({"cmd": "ping"}, path=path, timeout=1))


if __name__ == "__main__":
    unittest.main()
