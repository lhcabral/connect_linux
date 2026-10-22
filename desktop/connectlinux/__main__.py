"""Ponto de entrada: janela gráfica ou modo terminal."""

from __future__ import annotations

import argparse
import logging
import queue
import signal
import sys
from pathlib import Path

from . import __version__
from .app import App
from .protocol import LINK_PORT
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="connectlinux",
        description="Conecta este Linux a um celular Android na mesma rede.",
    )
    parser.add_argument("--headless", action="store_true", help="roda no terminal, sem janela")
    parser.add_argument("--name", help="nome deste computador mostrado no celular")
    parser.add_argument("--port", type=int, default=LINK_PORT, help="porta TCP (padrão: 17171)")
    parser.add_argument("--version", action="version", version=f"Connect Linux {__version__}")
    args = parser.parse_args(argv)

    _setup_logging()
    store = Store()
    if args.name:
        store.set_name(args.name)
    app = App(store=store, port=args.port)
    app.start()

    if args.headless:
        return _headless(app)

    try:
        from .gui import run_gui
    except ImportError as exc:
        print(f"Interface gráfica indisponível ({exc}). Continuando no terminal.")
        print("No Linux Mint: sudo apt install python3-tk")
        return _headless(app)

    try:
        run_gui(app)
    except tk_missing() as exc:
        print(f"Interface gráfica indisponível ({exc}). Continuando no terminal.")
        print("No Linux Mint: sudo apt install python3-tk")
        return _headless(app)
    return 0


def tk_missing() -> tuple[type[BaseException], ...]:
    try:
        import tkinter
    except Exception:
        return (Exception,)
    return (tkinter.TclError,)


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
