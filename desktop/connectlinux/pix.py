"""Código Pix estático (BR Code do Banco Central) para doações."""

from __future__ import annotations

import unicodedata

PIX_KEY = "luizhenrique.be@gmail.com"
RECEIVER = "Luiz Henrique C Oliveira"
CITY = "Brasil"


def payload(key: str = PIX_KEY, name: str = RECEIVER, city: str = CITY, txid: str = "***") -> str:
    """Texto do Pix copia e cola, também usado no QR code."""
    account = _field("00", "br.gov.bcb.pix") + _field("01", key)
    body = (
        _field("00", "01")
        + _field("26", account)
        + _field("52", "0000")
        + _field("53", "986")
        + _field("58", "BR")
        + _field("59", _ascii(name)[:25])
        + _field("60", _ascii(city)[:15])
        + _field("62", _field("05", txid))
        + "6304"
    )
    return body + f"{crc16(body.encode('utf-8')):04X}"


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE, exigido no campo 63 do BR Code."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def qr_matrix(text: str) -> list[list[bool]] | None:
    try:
        import qrcode
    except ImportError:
        return None
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2)
    code.add_data(text)
    code.make(fit=True)
    return code.get_matrix()


def _field(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def _ascii(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if ch.isascii() and ch.isprintable()).upper()
