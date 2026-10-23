"""Opção "Enviar para o Android" no clique direito do Nemo e no "Abrir com" das imagens."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("connectlinux")

PREFIX = "connectlinux-send-"
IMAGE_TYPES = (
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/heic",
    "image/heif",
    "image/avif",
    "image/svg+xml",
    "image/x-portable-pixmap",
)


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def launcher() -> str:
    installed = Path("/usr/bin/connectlinux")
    package = Path(__file__).resolve().parent
    if package.parent == Path("/usr/lib/connectlinux") and installed.is_file():
        return str(installed)
    return str(package.parent / "run.sh")


def sync(peers: dict[str, str], base: Path | None = None) -> bool:
    """Deixa uma entrada por celular pareado. `peers` é {id: nome}. Devolve True se mudou algo."""
    root = base or data_home()
    actions = root / "nemo" / "actions"
    apps = root / "applications"
    wanted: dict[Path, str] = {}
    command = launcher()
    for device_id, name in peers.items():
        short = device_id.split("-")[0]
        label = f"Enviar para o Android “{_clean(name)}”"
        exec_line = f"{_quote(command)} --send --device {device_id}"
        wanted[actions / f"{PREFIX}{short}.nemo_action"] = _nemo_action(label, exec_line, name)
        wanted[apps / f"{PREFIX}{short}.desktop"] = _desktop_entry(label, exec_line, name)

    changed = False
    for folder, suffix in ((actions, ".nemo_action"), (apps, ".desktop")):
        if folder.is_dir():
            for existing in folder.glob(f"{PREFIX}*{suffix}"):
                if existing not in wanted:
                    existing.unlink(missing_ok=True)
                    changed = True
    for path, content in wanted.items():
        try:
            if path.is_file() and path.read_text(encoding="utf-8") == content:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            changed = True
        except OSError as exc:
            log.info("menu de contexto: %s", exc)
    if changed and shutil.which("update-desktop-database"):
        subprocess.run(
            ["update-desktop-database", str(apps)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    return changed


def _nemo_action(label: str, exec_line: str, name: str) -> str:
    return (
        "[Nemo Action]\n"
        "Active=true\n"
        f"Name={label}\n"
        f"Comment=Abre a imagem na tela de {_clean(name)}\n"
        f"Exec={exec_line} %F\n"
        "Icon-Name=phone\n"
        "Selection=notnone\n"
        f"Mimetypes={';'.join(IMAGE_TYPES)};\n"
        "Quote=double\n"
    )


def _desktop_entry(label: str, exec_line: str, name: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={label}\n"
        f"Comment=Abre a imagem na tela de {_clean(name)}\n"
        f"Exec={exec_line} %F\n"
        "Icon=phone\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        f"MimeType={';'.join(IMAGE_TYPES)};\n"
    )


def _clean(name: str) -> str:
    text = " ".join(str(name).split())
    return "".join(ch for ch in text if ch not in "\"%;\\=") or "celular"


def _quote(path: str) -> str:
    if any(ch in path for ch in " \t\"'\\$`"):
        escaped = path.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        return f'"{escaped}"'
    return path
