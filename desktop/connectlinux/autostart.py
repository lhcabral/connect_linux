"""Inicia o Connect Linux junto com a sessão, só com o ícone ao lado do relógio."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .context_menu import _quote, launcher

log = logging.getLogger("connectlinux")


def entry_path() -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config / "autostart" / "connectlinux.desktop"


def is_enabled(path: Path | None = None) -> bool:
    target = path or entry_path()
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    values = dict(line.split("=", 1) for line in lines if "=" in line)
    if values.get("Hidden", "").strip().lower() == "true":
        return False
    return values.get("X-GNOME-Autostart-enabled", "true").strip().lower() != "false"


def set_enabled(enabled: bool, path: Path | None = None) -> bool:
    target = path or entry_path()
    flag = "true" if enabled else "false"
    command = launcher()
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Connect Linux\n"
        "Comment=Deixa o Connect Linux ao lado do relógio\n"
        f"TryExec={command}\n"
        f"Exec={_quote(command)} --background\n"
        "Icon=connectlinux\n"
        "Terminal=false\n"
        f"Hidden={'false' if enabled else 'true'}\n"
        f"X-GNOME-Autostart-enabled={flag}\n"
        "X-GNOME-Autostart-Delay=5\n"
    )
    try:
        if target.is_file() and target.read_text(encoding="utf-8") == content:
            return True
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return True
    except OSError as exc:
        log.info("inicialização automática: %s", exc)
        return False


def ensure_default(path: Path | None = None) -> None:
    """Liga na primeira execução e mantém o comando atualizado; respeita quem desligou."""
    target = path or entry_path()
    if not target.exists():
        set_enabled(True, target)
    elif is_enabled(target):
        set_enabled(True, target)
