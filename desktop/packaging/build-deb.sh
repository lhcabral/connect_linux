#!/usr/bin/env bash
# Gera dist/connectlinux_<versão>_all.deb para Linux Mint, Ubuntu e Debian.
set -euo pipefail

DESKTOP="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$DESKTOP/.." && pwd)"
VERSION="$(python3 -c "import sys; sys.path.insert(0, '$DESKTOP'); import connectlinux; print(connectlinux.__version__)")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/connectlinux_${VERSION}_all"

mkdir -p "$PKG/DEBIAN" \
  "$PKG/usr/lib/connectlinux" \
  "$PKG/usr/bin" \
  "$PKG/usr/share/applications" \
  "$PKG/usr/share/icons/hicolor/128x128/apps" \
  "$PKG/usr/share/doc/connectlinux"

cp -r "$DESKTOP/connectlinux" "$PKG/usr/lib/connectlinux/"
find "$PKG/usr/lib/connectlinux" -name "__pycache__" -type d -prune -exec rm -rf {} +
cp "$DESKTOP/icon.png" "$PKG/usr/share/icons/hicolor/128x128/apps/connectlinux.png"
cp "$ROOT/README.md" "$PKG/usr/share/doc/connectlinux/README.md"

cat > "$PKG/usr/bin/connectlinux" << 'EOF'
#!/bin/sh
export PYTHONPATH="/usr/lib/connectlinux${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m connectlinux "$@"
EOF
chmod 755 "$PKG/usr/bin/connectlinux"

cat > "$PKG/usr/share/applications/connectlinux.desktop" << 'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=Connect Linux
GenericName=Vínculo com o Android
Comment=Conecta este Linux a um celular Android
Exec=connectlinux
Icon=connectlinux
Terminal=false
Categories=Network;Utility;
StartupNotify=true
Keywords=android;celular;telefone;bluetooth;clipboard;arquivo;
EOF

cat > "$PKG/DEBIAN/control" << EOF
Package: connectlinux
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-tk, python3-dbus, python3-gi, libnotify-bin, xclip | wl-clipboard
Recommends: bluez, adb
Maintainer: Luiz Henrique <luizhenrique.be@gmail.com>
Description: Conecta o Linux a um celular Android
 Pareamento por código, envio de texto, links e arquivos, área de
 transferência compartilhada e notificações do celular no computador.
 Funciona por Wi-Fi, Bluetooth, cabo USB ou Tailscale.
EOF

cat > "$PKG/DEBIAN/postinst" << 'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
fi
EOF
chmod 755 "$PKG/DEBIAN/postinst"

find "$PKG" -type d -exec chmod 755 {} +
find "$PKG" -type f -exec chmod 644 {} +
chmod 755 "$PKG/usr/bin/connectlinux" "$PKG/DEBIAN/postinst"

mkdir -p "$ROOT/dist"
fakeroot dpkg-deb --build --root-owner-group "$PKG" "$ROOT/dist/connectlinux_${VERSION}_all.deb" >/dev/null
echo "$ROOT/dist/connectlinux_${VERSION}_all.deb"
