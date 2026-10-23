"""Procura versões novas no GitHub e instala o pacote .deb."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

from . import __version__

log = logging.getLogger("connectlinux")

LATEST_URL = "https://api.github.com/repos/lhcabral/connect_linux/releases/latest"
DEB_ASSET = "connectlinux_all.deb"
SUMS_ASSET = "SHA256SUMS.txt"
INSTALLED_DIR = Path("/usr/lib/connectlinux")


def parse_version(text: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(text))
    return tuple(int(number) for number in numbers[:4]) or (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def check_latest(timeout: float = 10) -> dict | None:
    """Devolve a versão nova e os endereços do pacote, ou None se já está em dia."""
    request = urllib.request.Request(
        LATEST_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"connectlinux/{__version__}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        release = json.loads(response.read().decode("utf-8"))
    version = str(release.get("tag_name") or "").lstrip("vV")
    if not version or not is_newer(version):
        return None
    assets = {str(item.get("name")): str(item.get("browser_download_url")) for item in release.get("assets", [])}
    deb = assets.get(DEB_ASSET)
    if not deb:
        return None
    return {"version": version, "deb": deb, "sums": assets.get(SUMS_ASSET)}


def download_and_install(update: dict) -> str:
    """Baixa o .deb, confere a soma SHA-256 e instala com a senha do administrador."""
    folder = Path.home() / ".cache" / "connectlinux"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"connectlinux_{update['version']}_all.deb"
    _download(update["deb"], target)
    if update.get("sums"):
        expected = _expected_sum(update["sums"])
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if expected and expected != actual:
            target.unlink(missing_ok=True)
            raise RuntimeError("o arquivo baixado está corrompido; tente de novo")
    if not shutil.which("pkexec"):
        raise RuntimeError(f"instale manualmente: sudo apt install {target}")
    done = subprocess.run(
        ["pkexec", "/usr/bin/apt-get", "install", "-y", "--allow-downgrades", str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if done.returncode in (126, 127):
        raise RuntimeError("atualização cancelada")
    if done.returncode != 0:
        log.warning("apt-get: %s", done.stderr.strip())
        raise RuntimeError("a instalação falhou; veja o registro em ~/.local/state/connectlinux")
    return update["version"]


def running_installed() -> bool:
    return INSTALLED_DIR in Path(__file__).resolve().parents


def restart_later() -> None:
    """Abre a versão instalada alguns segundos depois, quando esta já liberou a porta."""
    subprocess.Popen(
        ["sh", "-c", "sleep 2; exec /usr/bin/connectlinux"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=os.environ.copy(),
    )


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": f"connectlinux/{__version__}"})
    temporary = target.with_suffix(".part")
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    temporary.replace(target)


def _expected_sum(url: str) -> str | None:
    request = urllib.request.Request(url, headers={"User-Agent": f"connectlinux/{__version__}"})
    with urllib.request.urlopen(request, timeout=20) as response:
        text = response.read().decode("utf-8", "replace")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == DEB_ASSET:
            return parts[0].lower()
    return None
