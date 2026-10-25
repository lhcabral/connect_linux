"""Ponto de entrada: janela gráfica ou modo terminal."""

from __future__ import annotations

import argparse
import logging
import os
import queue
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, bluetooth_link, control
from .app import App
from .protocol import LINK_PORT
from .store import Store
from .system_bridge import notify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="connectlinux",
        description="Conecta este Linux a um celular Android na mesma rede.",
    )
    parser.add_argument("--headless", action="store_true", help="roda no terminal, sem janela")
    parser.add_argument("--name", help="nome deste computador mostrado no celular")
    parser.add_argument("--port", type=int, default=LINK_PORT, help="porta TCP (padrão: 17171)")
    parser.add_argument("--send", action="store_true", help="envia os arquivos ao celular e abre na tela dele")
    parser.add_argument("--device", default="", help="identificador do celular usado com --send")
    parser.add_argument("--background", action="store_true", help="começa só com o ícone ao lado do relógio")
    parser.add_argument("files", nargs="*", help="arquivos usados com --send")
    parser.add_argument("--version", action="version", version=f"Connect Linux {__version__}")
    args = parser.parse_args(argv)

    if args.send:
        return _send_command([Path(item).expanduser().resolve() for item in args.files], args.device)

    _setup_logging()
    store = Store()
    if args.name:
        store.set_name(args.name)
    app = App(store=store, port=args.port)
    if not app.start_control():
        if not args.background:
            control.request({"cmd": "present"}, timeout=5)
        print("O Connect Linux já está aberto.")
        return 0

    run_gui = None if args.headless else _load_gui()
    if run_gui is not None:
        bluetooth_link.SHARED_MAIN_LOOP = True
    app.start()
    if run_gui is None:
        return _headless(app)
    run_gui(app, start_hidden=args.background)
    return 0


def _load_gui():
    try:
        from . import gui
    except (ImportError, ValueError) as exc:
        print(f"Interface gráfica indisponível ({exc}). Continuando no terminal.")
        print("No Linux Mint: sudo apt install python3-gi gir1.2-gtk-3.0")
        return None
    if not gui.available():
        print("Ambiente gráfico indisponível. Continuando no terminal.")
        return None
    return gui.run_gui


def _send_command(files: list[Path], device_id: str) -> int:
    files = [path for path in files if path.is_file()]
    if not files:
        notify("Connect Linux", "Nenhum arquivo para enviar.")
        return 1
    message = {"cmd": "send", "files": [str(path) for path in files], "device": device_id, "open": True}
    if control.request({"cmd": "ping"}, timeout=2) is None:
        _launch_background()
        deadline = time.monotonic() + 15
        while control.request({"cmd": "ping"}, timeout=2) is None:
            if time.monotonic() > deadline:
                notify("Connect Linux", "Não foi possível abrir o Connect Linux.")
                return 1
            time.sleep(0.5)
    reply = control.request(message, timeout=60)
    if reply is None:
        notify("Connect Linux", "O Connect Linux não respondeu.")
        return 1
    text = str(reply.get("message", ""))
    print(text)
    if not reply.get("ok"):
        notify("Connect Linux", text)
        return 1
    return 0


def _launch_background() -> None:
    package_root = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = package_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    subprocess.Popen(
        [sys.executable, "-m", "connectlinux", "--background"],
        cwd=package_root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _headless(app: App) -> int:
    ips = ", ".join(app.local_ips()) or "IP não identificado"
    print(f"Connect Linux escutando na porta {app.port}")
    print(f"Endereços deste computador: {ips}")
    print("Abra o aplicativo no celular, na mesma rede Wi-Fi.")
    print("Se o celular não encontrar o computador, conecte pelo IP acima.")
    bt = app.bluetooth.status()
    if app.bluetooth.registered and bt.get("powered"):
        print(f"Bluetooth pronto ({bt.get('name') or 'este computador'}).")
    elif app.bluetooth.registered:
        print("Bluetooth desligado. Ligue com: bluetoothctl power on")
    has_adb, _ready = app.usb_status()
    if has_adb:
        print("Cabo USB: ligue a depuração USB no celular e conecte o cabo.")
    print("Encerre com Ctrl+C.")
    if app.fatal:
        print(app.fatal)
        app.stop()
        return 1

    def stop(_signum=None, _frame=None) -> None:
        print("\nEncerrando…")
        app.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while True:
        try:
            kind, payload = app.events.get(timeout=0.5)
        except queue.Empty:
            continue
        if kind == "pair" and payload.get("code"):
            print(f"Código para {payload.get('name', 'o celular')}: {payload['code']}")
        elif kind == "log":
            print(payload.get("text", ""))


def _setup_logging() -> None:
    directory = Path.home() / ".local" / "state" / "connectlinux"
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(directory / "connectlinux.log", encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
