"""Conexão por cabo USB usando `adb reverse`.

Com a depuração USB ligada no celular, a porta 17171 do celular passa a
apontar para a porta do Connect Linux neste computador.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from .protocol import LINK_PORT

log = logging.getLogger("connectlinux")


def find_adb() -> str | None:
    found = shutil.which("adb")
    if found:
        return found
    for candidate in (
        Path.home() / "Android" / "Sdk" / "platform-tools" / "adb",
        Path("/usr/lib/android-sdk/platform-tools/adb"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


class UsbLink:
    def __init__(self) -> None:
        self.adb = find_adb()
        self.devices: dict[str, str] = {}
        self._forwarded: dict[str, int] = {}

    def refresh(self, local_port: int) -> list[str]:
        """Aplica o redirecionamento nos celulares conectados. Devolve mensagens novas."""
        if not self.adb:
            return []
        try:
            result = subprocess.run(
                [self.adb, "devices"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        current: dict[str, str] = {}
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                current[parts[0]] = parts[1]
        messages: list[str] = []
        for serial, state in current.items():
            if state == "unauthorized" and self.devices.get(serial) != "unauthorized":
                messages.append("Celular no cabo USB: aceite a depuração USB na tela do celular.")
            if state != "device":
                continue
            if self._forwarded.get(serial) == local_port:
                continue
            try:
                done = subprocess.run(
                    [self.adb, "-s", serial, "reverse", f"tcp:{LINK_PORT}", f"tcp:{local_port}"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if done.returncode == 0:
                self._forwarded[serial] = local_port
                messages.append("Cabo USB pronto. No celular, toque em Conectar por cabo USB.")
            else:
                log.info("adb reverse falhou: %s", done.stderr.strip())
        for serial in list(self._forwarded):
            if current.get(serial) != "device":
                del self._forwarded[serial]
        self.devices = current
        return messages

    def ready_count(self) -> int:
        return len(self._forwarded)
