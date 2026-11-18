#!/bin/sh
# Installe Yuki pour l'utilisateur courant (aucun sudo).
# Les fichiers de ~/.local/bin sont des liens vers ce dépôt : un `git pull` suffit pour mettre à jour.
set -e
cd "$(dirname "$0")"
[ "$(uname)" = "Darwin" ] && exec ./install-macos.sh
REPO="$PWD"
BIN="$HOME/.local/bin"
AUTOSTART="$HOME/.config/autostart"

if ! python3 -c "import PyQt6.QtWidgets, PyQt6.QtDBus" 2>/dev/null; then
    echo "PyQt6 manque. Installe celui de ta distribution (pas celui de pip) :"
    echo "  Fedora : sudo dnf install python3-pyqt6"
    echo "  Arch   : sudo pacman -S python-pyqt6"
    echo "  Debian/Ubuntu : sudo apt install python3-pyqt6"
    exit 1
fi

mkdir -p "$BIN" "$AUTOSTART"
chmod +x yuki.py yuki-sync.sh yuki-restart.sh
ln -sf "$REPO/yuki.py" "$BIN/yuki.py"
ln -sf "$REPO/yuki-sync.sh" "$BIN/yuki-sync.sh"
ln -sf "$REPO/yuki-restart.sh" "$BIN/yuki-restart"

cat > "$AUTOSTART/yuki.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Yuki
Comment=Neige sur le bureau
Exec=$BIN/yuki-sync.sh
Icon=weather-snow
DESKTOP

echo "Installé ❄"
echo "  - démarre à la connexion (si le PC est sur secteur)"
echo "  - relancer : yuki-restart"
echo "  - réglages : YUKI_ARGS dans $REPO/yuki-sync.sh"
