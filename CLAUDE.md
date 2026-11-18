# Yuki

- `yuki.py` n'est que le point d'entrée : les scripts cherchent le motif `python[0-9.]* .*yuki\.py`, ne pas le renommer. Le code est dans `yuki/`.
- `~/.local/bin/yuki.py` est un lien vers ce dépôt : le prochain redémarrage de Yuki charge le code en cours d'édition, même non commité.
- Tests : `python3 tests/test_macos_backend.py`, `tests/test_windows_backend.py` (fausses API, sans Mac ni Windows) et `tests/test_settings.py`.
- Les réglages du menu (`QSettings("yuki", "yuki")`) ont priorité sur les options de lancement : penser à eux si une option semble ignorée.
- Tester KDE sans toucher à la session : `dbus-run-session` + `kwin_wayland --virtual --socket w`, avec `HOME`, `XDG_*` et `XDG_RUNTIME_DIR` pointant vers un dossier jetable (sinon KWin peut écrire sa config d'écrans dans le vrai `~/.config`).
- macOS : le backend n'a jamais tourné sur un vrai Mac. Les écrans y ont souvent des coordonnées négatives : arrondir vers le bas (`x // TILE`), pas avec `int(x)`. Sans Command Line Tools, `/usr/bin/git` et `/usr/bin/python3` existent mais sont des stubs qui ouvrent une fenêtre d'installation : `command -v` ne suffit pas.
- Windows : le backend n'a jamais tourné sur un vrai PC. `tools/windows_probe.py` y mesure le coût de l'overlay (`--dry-run` pour vérifier la boucle ailleurs).
- `install-windows.ps1` reste en ASCII : PowerShell 5 lit mal l'UTF-8 sans BOM.
