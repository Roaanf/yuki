# Yuki

- `yuki.py` n'est que le point d'entrée : les scripts cherchent le motif `python[0-9.]* .*yuki\.py`, ne pas le renommer. Le code est dans `yuki/`.
- `~/.local/bin/yuki.py` est un lien vers ce dépôt : le prochain redémarrage de Yuki charge le code en cours d'édition, même non commité.
- Tests : `python3 tests/test_macos_backend.py`, `tests/test_windows_backend.py` (fausses API, sans Mac ni Windows), `tests/test_settings.py`, `tests/test_fun.py`, `tests/test_corners.py` et `tests/test_sky.py`.
- Les réglages du menu (`QSettings("yuki", "yuki")`) ont priorité sur les options de lancement : penser à eux si une option semble ignorée.
- Tester KDE sans toucher à la session : `dbus-run-session` + `kwin_wayland --virtual --socket w`, avec `HOME`, `XDG_*` et `XDG_RUNTIME_DIR` pointant vers un dossier jetable (sinon KWin peut écrire sa config d'écrans dans le vrai `~/.config`).
- Dates dans `Sim` : passer par `sim.clock` (les saisons et le calendrier en dépendent), pour que les tests ne changent pas de résultat selon le jour.
- macOS : le backend a tourné sur un seul vrai Mac, où `dock_rect` n'a pas trouvé la largeur du Dock (neige sur toute la largeur de l'écran avant le correctif). Les écrans y ont souvent des coordonnées négatives : arrondir vers le bas (`x // TILE`), pas avec `int(x)`. Sans Command Line Tools, `/usr/bin/git` et `/usr/bin/python3` existent mais sont des stubs qui ouvrent une fenêtre d'installation : `command -v` ne suffit pas.
- Windows : le backend n'a jamais tourné sur un vrai PC. `tools/windows_probe.py` y mesure le coût de l'overlay (`--dry-run` pour vérifier la boucle ailleurs).
- `install-windows.ps1` reste en ASCII : PowerShell 5 lit mal l'UTF-8 sans BOM.
- Coins arrondis : `Surface.base` donne la descente du bord par colonne (0 sur KDE). Tout ce qui pose quelque chose sur un tas doit utiliser `s.y + s.base[c]`, pas `s.y`. Rayons macOS mesurés sur une capture Tahoe (fenêtre 24 pt, Dock 21 pt), à recaler si besoin.
- Dock macOS : les fenêtres CG du Dock ne donnent pas sa largeur, seule l'accessibilité (liste `AXList` du processus Dock) la donne. `--debug` affiche ce que Yuki en voit.
- Performance : skill `yuki-perf` (banc `tools/bench.py`, mesure de KWin). Ne rien rasteriser sur toute la largeur d'une surface à chaque image : le clip de Qt ne réduit pas le coût du rasteriseur, d'où `_pile_spans`.
- Rendu partiel : tout nouvel élément visuel doit entrer dans `Rendering.test_everything_at_once_renders_like_a_full_render` (`tests/test_sky.py`). C'est lui qui attrape les zones oubliées dans les tuiles abîmées. Ce qui est dessiné derrière les fenêtres et gardé en cache (aurore) doit aussi être redessiné quand les fenêtres bougent (`_sky_windows_changed`).
- Inactivité sur KDE : `GetSessionIdleTime` est refusé sous Wayland. `yuki/backends/wl_idle.py` parle `ext-idle-notify-v1` en ctypes, sur une connexion Wayland à part. Sans lui (X11), on ne suit que le curseur, et le givre arrive alors aussi pendant qu'on tape.
