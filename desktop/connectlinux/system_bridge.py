"""Integração com o Linux Mint: área de transferência, avisos, sons e arquivos."""

from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

log = logging.getLogger("connectlinux")

_INVALID_FILENAME = re.compile(r"[^0-9A-Za-zÀ-ÿ._ ()-]+")
_SKIP_IFACE = ("docker", "veth", "virbr", "br-", "vmnet")
_clip_warned = False


def local_ipv4s() -> list[str]:
    found: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            ip = probe.getsockname()[0]
            if ip and not ip.startswith("127."):
                found.append(ip)
        finally:
            probe.close()
    except OSError:
        pass

    net = Path("/sys/class/net")
    if net.is_dir():
        for entry in sorted(net.iterdir()):
            name = entry.name
            if name == "lo" or name.startswith(_SKIP_IFACE):
                continue
            ip = _iface_ipv4(name)
            if ip and ip not in found and not ip.startswith("127."):
                found.append(ip)
    return found


def _iface_ipv4(name: str) -> str | None:
    if len(name) >= 16:
        return None
    try:
        import fcntl
    except ImportError:
        return None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = fcntl.ioctl(
            sock.fileno(),
            0x8915,
            struct.pack("256s", name.encode("utf-8")[:15]),
        )
    except OSError:
        return None
    finally:
        sock.close()
    return socket.inet_ntoa(packed[20:24])


def sanitize_filename(name: str) -> str:
    base = str(name or "").replace("\\", "/").split("/")[-1]
    base = base.replace("\x00", "")
    base = _INVALID_FILENAME.sub("_", base).strip(" .")
    if base in {"", ".", ".."}:
        return "arquivo"
    return base[:120]


def destination_for(directory: Path, filename: str) -> Path:
    safe = sanitize_filename(filename)
    directory = directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)

    def candidate(index: int) -> Path:
        if index == 0:
            chosen = safe
        else:
            path = Path(safe)
            chosen = f"{path.stem} ({index}){path.suffix}"
        final = (directory / chosen).resolve()
        if final.parent != directory:
            raise ValueError("caminho inválido")
        return final

    for index in range(1000):
        path = candidate(index)
        if not path.exists():
            return path
    raise FileExistsError(safe)


def valid_http_url(url: str) -> bool:
    text = str(url or "").strip()
    if not text or any(ch in text for ch in ("\n", "\r", "\x00", " ")):
        return False
    parsed = urllib.parse.urlparse(text)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _clipboard_commands(reading: bool) -> list[list[str]]:
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    if reading:
        wayland_cmd = ["wl-paste", "-n"]
        xclip_cmd = ["xclip", "-selection", "clipboard", "-o"]
        xsel_cmd = ["xsel", "--clipboard", "--output"]
    else:
        wayland_cmd = ["wl-copy"]
        xclip_cmd = ["xclip", "-selection", "clipboard"]
        xsel_cmd = ["xsel", "--clipboard", "--input"]
    ordered = (
        [wayland_cmd, xclip_cmd, xsel_cmd]
        if wayland
        else [xclip_cmd, xsel_cmd, wayland_cmd]
    )
    return [cmd for cmd in ordered if shutil.which(cmd[0])]


def read_clipboard() -> str | None:
    global _clip_warned
    commands = _clipboard_commands(True)
    if not commands:
        if not _clip_warned:
            _clip_warned = True
            log.warning("Instale xclip ou wl-clipboard para sincronizar a área de transferência.")
        return None
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace")
    return None


def write_clipboard(text: str) -> bool:
    global _clip_warned
    commands = _clipboard_commands(False)
    if not commands:
        if not _clip_warned:
            _clip_warned = True
            log.warning("Instale xclip ou wl-clipboard para sincronizar a área de transferência.")
        return False
    payload = text.encode("utf-8")
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                input=payload,
                check=False,
                capture_output=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False


def notify(title: str, body: str) -> None:
    summary = " ".join(str(title).split())[:180] or "Connect Linux"
    message = " ".join(str(body).split())[:600]
    if not shutil.which("notify-send"):
        log.info("Aviso: %s — %s", summary, message)
        return
    subprocess.run(
        ["notify-send", "-a", "Connect Linux", "-i", "phone", summary, message],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=3,
    )


def open_url(url: str) -> bool:
    if not valid_http_url(url):
        return False
    if not shutil.which("xdg-open"):
        return False
    subprocess.Popen(
        ["xdg-open", url.strip()],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True


def open_path(path: Path) -> bool:
    if not shutil.which("xdg-open"):
        return False
    subprocess.Popen(
        ["xdg-open", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True


def read_battery() -> tuple[int, bool] | None:
    base = Path("/sys/class/power_supply")
    if not base.is_dir():
        return None
    entries = sorted(base.iterdir(), key=lambda item: (not item.name.upper().startswith("BAT"), item.name))
    for entry in entries:
        capacity = entry / "capacity"
        if not capacity.is_file():
            continue
        try:
            level = int(capacity.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        charging = False
        status = entry / "status"
        if status.is_file():
            try:
                state = status.read_text(encoding="utf-8").strip().lower()
            except OSError:
                state = ""
            charging = state in {"charging", "full"}
        return max(0, min(level, 100)), charging
    return None


def play_find_sound() -> None:
    def run() -> None:
        commands = [
            ["canberra-gtk-play", "-i", "bell"],
            ["canberra-gtk-play", "-i", "complete"],
            ["paplay", "/usr/share/sounds/freedesktop/stereo/bell.oga"],
            ["paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"],
        ]
        for _ in range(4):
            played = False
            for cmd in commands:
                if shutil.which(cmd[0]) is None:
                    continue
                if cmd[0] == "paplay" and not Path(cmd[1]).is_file():
                    continue
                try:
                    subprocess.run(
                        cmd,
                        check=False,
                        timeout=5,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    played = True
                    break
                except (OSError, subprocess.TimeoutExpired):
                    continue
            if not played:
                print("\a", end="", flush=True)
            time.sleep(0.35)

    threading.Thread(target=run, daemon=True).start()
