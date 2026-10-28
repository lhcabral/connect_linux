"""Espelhamento da tela do celular com o scrcpy.

O scrcpy fala com o celular pelo adb: pelo cabo USB ou, se a depuração por
Wi-Fi estiver ligada, pelo endereço do celular na rede.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable

log = logging.getLogger("connectlinux")

WIFI_ADB_PORT = 5555
QUICK_EXIT_SECONDS = 4
INSTALL_HINT = "Instale o scrcpy para espelhar a tela: sudo apt install scrcpy (ou snap install scrcpy, versão mais nova)."


def find_scrcpy() -> str | None:
    found = shutil.which("scrcpy")
    if found:
        return found
    for candidate in ("/snap/bin/scrcpy", "/usr/local/bin/scrcpy"):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


class Mirror:
    def __init__(self, adb: str | None, on_log: Callable[[str], None]) -> None:
        self.adb = adb
        self._log = on_log
        self._running: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.adb is not None and find_scrcpy() is not None

    def start(self, name: str, host: str | None) -> None:
        """Abre a janela de espelhamento sem travar quem chamou."""
        threading.Thread(target=self._start, args=(name, host), name="connectlinux-mirror", daemon=True).start()

    def stop_all(self) -> None:
        with self._lock:
            processes = list(self._running.values())
            self._running.clear()
        for process in processes:
            if process.poll() is None:
                process.terminate()

    def _start(self, name: str, host: str | None) -> None:
        scrcpy = find_scrcpy()
        if scrcpy is None:
            self._log(INSTALL_HINT)
            return
        if self.adb is None:
            self._log("Instale o adb para espelhar a tela: sudo apt install adb")
            return
        serial = self._pick_device(host)
        if serial is None:
            self._log(
                f"Não achei {name} pelo adb. Ligue a Depuração USB nas opções de desenvolvedor "
                "e conecte o cabo. Para espelhar sem fio depois, rode uma vez com o cabo: adb tcpip 5555"
            )
            return
        with self._lock:
            current = self._running.get(serial)
            if current is not None and current.poll() is None:
                self._log("O espelhamento desse celular já está aberto.")
                return
            env = dict(os.environ, ADB=self.adb)
            try:
                process = subprocess.Popen(
                    [scrcpy, "-s", serial, "--window-title", f"{name} · Connect Linux", "--stay-awake"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )
            except OSError as exc:
                self._log(f"Não foi possível abrir o scrcpy: {exc}")
                return
            self._running[serial] = process
        via = "Wi-Fi" if ":" in serial else "cabo USB"
        self._log(f"Espelhando a tela de {name} pelo {via}.")
        self._watch(serial, process)

    def _watch(self, serial: str, process: subprocess.Popen) -> None:
        started = time.monotonic()
        _out, err = process.communicate()
        with self._lock:
            if self._running.get(serial) is process:
                del self._running[serial]
        if process.returncode not in (0, None) and time.monotonic() - started < QUICK_EXIT_SECONDS:
            lines = [line for line in (err or "").splitlines() if "ERROR" in line] or (err or "").splitlines()
            detail = lines[-1].strip() if lines else f"código {process.returncode}"
            log.info("scrcpy terminou: %s", err)
            self._log(f"O espelhamento não abriu: {detail}")

    def _pick_device(self, host: str | None) -> str | None:
        devices = self._devices()
        usb = [serial for serial in devices if ":" not in serial]
        if usb:
            return usb[0]
        if host:
            wanted = f"{host}:{WIFI_ADB_PORT}"
            if wanted in devices:
                return wanted
            if self._connect(wanted):
                return wanted
        return devices[0] if devices else None

    def _devices(self) -> list[str]:
        try:
            result = subprocess.run([self.adb, "devices"], check=False, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return []
        found = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                found.append(parts[0])
        return found

    def _connect(self, address: str) -> bool:
        try:
            result = subprocess.run(
                [self.adb, "connect", address], check=False, capture_output=True, text=True, timeout=6
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and address in self._devices()
