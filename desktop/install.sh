#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
sed "s|__ROOT__|$ROOT|g" "$ROOT/connectlinux.desktop.in" > "$APPS/connectlinux.desktop"
chmod +x "$ROOT/run.sh"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS" >/dev/null 2>&1 || true
fi
echo "Atalho instalado. Procure por Connect Linux no menu do Mint."
if ! python3 -c "import tkinter" >/dev/null 2>&1; then
  echo "Para a janela: sudo apt install python3-tk"
fi
if ! command -v xclip >/dev/null 2>&1 && ! command -v wl-copy >/dev/null 2>&1; then
  echo "Para a área de transferência: sudo apt install xclip"
fi
if ! command -v notify-send >/dev/null 2>&1; then
  echo "Para os avisos na área de trabalho: sudo apt install libnotify-bin"
fi
