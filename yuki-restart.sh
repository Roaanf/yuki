#!/bin/sh
# Arrête Yuki (lancé par systemd-run ou à la main) puis le relance via yuki-sync.sh
if [ "$(uname)" = "Darwin" ]; then
    exec launchctl kickstart -k "gui/$(id -u)/org.roland.yuki"
fi
systemctl --user stop yuki 2>/dev/null
pkill -f "python[0-9.]* .*yuki\.py" 2>/dev/null
sleep 0.5
exec "$HOME/.local/bin/yuki-sync.sh"
