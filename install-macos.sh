#!/bin/sh
# Installe Yuki sur macOS pour l'utilisateur courant (aucun sudo) : venv + LaunchAgent.
# Réglages : YUKI_ARGS="--density 0.5" ./install-macos.sh
set -e
cd "$(dirname "$0")"
REPO="$PWD"
LABEL="org.roland.yuki"
VENV="$HOME/Library/Application Support/Yuki/venv"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/yuki.log"
BIN="$HOME/.local/bin"
YUKI_ARGS="${YUKI_ARGS:---density 0.7 --pile 0.35 --melt 0.006 --over-windows 0.6 --over-max 0.1 --fps 60}"

if [ "$(uname)" != "Darwin" ]; then
    echo "Ce script est réservé à macOS (sur Linux : ./install.sh)."
    exit 1
fi
# Sans Command Line Tools, /usr/bin/python3 n'est qu'un stub qui ouvre la fenêtre d'installation.
PY3="$(command -v python3 || true)"
DEV="$(xcode-select -p 2>/dev/null || true)"
if [ -z "$PY3" ] || { [ "$PY3" = /usr/bin/python3 ] && [ ! -x "${DEV:-/nonexistent}/usr/bin/python3" ]; }; then
    echo "python3 manque : installe-le depuis https://www.python.org/downloads/macos/"
    echo "puis relance ce script dans un nouveau terminal."
    exit 1
fi

mkdir -p "$(dirname "$VENV")" "$(dirname "$PLIST")" "$(dirname "$LOG")" "$BIN"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade PyQt6 pyobjc-framework-Quartz pyobjc-framework-Cocoa

# plistlib s'occupe de l'échappement XML des chemins et des options.
# shellcheck disable=SC2086
"$VENV/bin/python" - "$PLIST" "$LABEL" "$LOG" "$VENV/bin/python" "$REPO/yuki.py" $YUKI_ARGS <<'PY'
import plistlib
import sys

path, label, log, *command = sys.argv[1:]
with open(path, "wb") as f:
    plistlib.dump({"Label": label, "ProgramArguments": command, "RunAtLoad": True,
                   "ProcessType": "Interactive",
                   "StandardOutPath": log, "StandardErrorPath": log}, f)
PY

chmod +x yuki.py yuki-restart.sh
ln -sf "$REPO/yuki-restart.sh" "$BIN/yuki-restart"

DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"

echo "Installé ❄"
echo "  - démarre à la connexion, se met en pause sur batterie"
echo "  - relancer : $BIN/yuki-restart"
echo "  - journal : $LOG"
