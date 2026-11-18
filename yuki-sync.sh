#!/bin/sh
# Allume ou éteint Yuki selon l'état RÉEL de l'alimentation, au moment où on décide.
# Appelé par 120hz.sh, 60hz.sh et au démarrage de session.
# Le verrou évite que deux appels (branché/débranché rapprochés) se marchent dessus.

# Tes réglages (voir README / yuki.py --help)
YUKI_ARGS="${YUKI_ARGS:---density 0.7 --pile 0.35 --melt 0.006 --over-windows 0.6 --over-max 0.1 --fps 60}"

sleep "${1:-0}"   # laisse l'écran se stabiliser après un changement de fréquence

exec 9>"${XDG_RUNTIME_DIR:-/tmp}/yuki-sync.lock"
flock 9

if cat /sys/class/power_supply/*/online 2>/dev/null | grep -q 1; then
    # Sur secteur : démarrer s'il ne tourne pas
    if ! pgrep -f "python[0-9.]* .*yuki\.py" >/dev/null; then
        systemctl --user reset-failed yuki 2>/dev/null
        # shellcheck disable=SC2086
        systemd-run --user --quiet --collect --unit=yuki -- \
            python3 "$HOME/.local/bin/yuki.py" $YUKI_ARGS
    fi
else
    # Sur batterie : arrêter proprement (le script KWin est déchargé)
    systemctl --user stop yuki 2>/dev/null
    pkill -f "python[0-9.]* .*yuki\.py" 2>/dev/null
fi
exit 0
