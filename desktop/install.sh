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
if ! python3 -c "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk; import cairo" >/dev/null 2>&1; then
  echo "Para a janela: sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0"
fi
if ! command -v xclip >/dev/null 2>&1 && ! command -v wl-copy >/dev/null 2>&1; then
  echo "Para a área de transferência: sudo apt install xclip"
fi
if ! command -v notify-send >/dev/null 2>&1; then
  echo "Para os avisos na área de trabalho: sudo apt install libnotify-bin"
fi
if ! command -v scrcpy >/dev/null 2>&1; then
  echo "Para espelhar a tela do celular: sudo apt install scrcpy adb"
fi
if ! python3 -c "import gi; gi.require_version('XApp', '1.0'); from gi.repository import XApp" >/dev/null 2>&1; then
  echo "Para o ícone ao lado do relógio: sudo apt install gir1.2-xapp-1.0"
fi
