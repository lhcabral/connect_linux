"""Mensagens do Connect Linux.

O celular e o computador precisam usar os mesmos valores. A cópia Android
está em android/app/src/main/java/dev/connectlinux/app/Protocol.kt.
"""

from __future__ import annotations

import json
import re
import struct
import time
import uuid

APP = "connectlinux"
VERSION = 1
DISCOVERY_PORT = 17170
LINK_PORT = 17171
# A descoberta também usa type "connect": o computador pede ao celular
# indicado em "target" para abrir a conexão. Um celular antigo ignora.
MULTICAST_GROUP = "239.255.171.70"
BLUETOOTH_UUID = "6f1c0a4e-6b9d-4c55-9a8e-2b1d7c1e5a71"
BLUETOOTH_CHANNEL = 22

MAX_FRAME = 8 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024
CHUNK_RAW = 32 * 1024
MAX_CHUNK_RAW = 256 * 1024
MAX_CLIPBOARD = 250_000
# Fotos recentes: o computador pede "photos_list" (offset, limit) e o celular
# responde "photos" com miniaturas JPEG em base64. "photo_get" traz a foto
# inteira pelo envio de arquivo comum, com open=true. Um celular antigo ignora.
PHOTO_PAGE = 30
MAX_PHOTO_PAGE = 60
MAX_THUMB_B64 = 200_000
PAIR_ATTEMPTS = 5
PAIR_TTL_SECONDS = 90

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class ProtocolError(Exception):
    """Mensagem fora do formato combinado."""


def now_ms() -> int:
    return int(time.time() * 1000)


def new_device_id() -> str:
    return str(uuid.uuid4())


def sanitize_name(name: object) -> str:
    text = str(name or "")
    cleaned = "".join(ch for ch in text if ch.isprintable() and ch not in "\r\n\t")
    cleaned = " ".join(cleaned.split()).strip()[:60]
    return cleaned or "Aparelho"


def encode_message(message: dict) -> bytes:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME:
        raise ProtocolError("mensagem grande demais")
    return struct.pack("!I", len(payload)) + payload


def decode_payload(payload: bytes) -> dict:
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("json inválido") from exc
    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
        raise ProtocolError("mensagem inválida")
    return data


def frame_size(header: bytes) -> int:
    if len(header) != 4:
        raise ProtocolError("cabeçalho inválido")
    length = struct.unpack("!I", header)[0]
    if length <= 0 or length > MAX_FRAME:
        raise ProtocolError("tamanho inválido")
    return length
