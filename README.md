# ❄ Yuki

De la neige sur le bureau KDE Plasma 6, en Wayland comme en X11, sur macOS (voir [macOS](#macos)) et sur Windows (voir [Windows](#windows)).

- Les flocons tombent par-dessus tout, et les clics passent au travers.
- La neige s'accumule sur le haut des fenêtres et sur la barre des tâches. Elle suit le panneau flottant ou collé.
- Si on secoue une fenêtre, la neige glisse et tombe. Si on ferme la fenêtre, tout dégringole. Si on la réduit, sa neige est gardée de côté.
- 🚜 Un chasse-neige passe de temps en temps sur la barre des tâches, un par écran.
- ⛄ Des lutins construisent des bonshommes de neige.
- 🎅 Le père Noël passe en traîneau.
- Les lutins, les bonshommes et le père Noël restent derrière les fenêtres maximisées. Les flocons y sont masqués ou rendus transparents, au choix.
- Rien n'est affiché au-dessus des apps en plein écran.
- Une icône ❄ dans la zone de notification donne accès au chasse-neige, au bonhomme, au père Noël, à la pause et à Quitter.
- Le rendu est partiel : seules les zones qui bougent sont redessinées, environ 10 % de l'écran.

## Installation

Il faut le PyQt6 **de la distribution**. Celui de pip apporte son propre Qt, incompatible avec le Wayland du système.

```bash
sudo dnf install python3-pyqt6        # Fedora
# sudo pacman -S python-pyqt6         # Arch
# sudo apt install python3-pyqt6      # Debian / Ubuntu

git clone <ce dépôt> ~/src/yuki
~/src/yuki/install.sh
```

`install.sh` :
- crée des liens dans `~/.local/bin` vers `yuki.py`, `yuki-sync.sh` et `yuki-restart` ;
- ajoute Yuki au démarrage automatique de la session.

Pour mettre à jour, il suffit de faire `git pull` puis `yuki-restart`.

## Utilisation

| Commande | Effet |
|---|---|
| `yuki-restart` | Relance Yuki, par exemple après une mise à jour |
| `yuki-sync.sh` | Démarre Yuki sur secteur, l'arrête sur batterie |
| `python3 yuki.py [options]` | Lancement manuel (Ctrl+C pour arrêter) |

Tes réglages se trouvent dans la variable `YUKI_ARGS`, au début de `yuki-sync.sh`.

### Options

| Option | Défaut | Effet |
|---|---|---|
| `--density` | `1.0` | Quantité de flocons |
| `--wind` | `15` | Vent moyen en px/s (négatif = vers la gauche) |
| `--pile` | `1.0` | Vitesse d'accumulation (`0` = la neige ne se pose pas) |
| `--melt` | `0.003` | Vitesse de fonte de la neige posée |
| `--fps` | `30` | Images par seconde |
| `--over-max` | `0` | Opacité des flocons sur les fenêtres maximisées |
| `--over-windows` | `1.0` | Opacité des flocons sur les autres fenêtres |
| `--plow-every` | `10` | Chasse-neige toutes les N minutes (`0` = jamais) |
| `--elves-every` | `6` | Bonhomme de neige toutes les N minutes (`0` = jamais) |
| `--santa-every` | `12` | Père Noël toutes les N minutes (`0` = jamais) |
| `--panel-gap` | `8` | KDE : écart du panneau flottant en px (`0` si le panneau ne flotte pas) |
| `--on-battery` | — | macOS et Windows : continue à neiger sur batterie |
| `--no-tray` | — | Pas d'icône ❄ |
| `--debug` | — | Affiche les fenêtres sur lesquelles la neige peut tenir |

### Réglages depuis le menu ❄

Le sous-menu **Réglages** change la neige, le vent, l'accumulation, la fonte, l'opacité des flocons, la fréquence des passages et les images par seconde, sans relancer Yuki.

Ces choix sont conservés au redémarrage et **ont priorité sur les options de lancement**, y compris `YUKI_ARGS`. L'entrée « Revenir aux réglages de lancement » les efface. Sur macOS et Windows, la case « Neiger sur batterie » remplace `--on-battery` de la même façon. `--panel-gap` reste une option de lancement.

### Déclencher depuis un terminal ou un raccourci

```bash
busctl --user call org.roland.Yuki /Yuki org.roland.Yuki plow      # chasse-neige
busctl --user call org.roland.Yuki /Yuki org.roland.Yuki snowman   # bonhomme
busctl --user call org.roland.Yuki /Yuki org.roland.Yuki santa     # père Noël
```

### Seulement quand le PC est branché

Ajoute cette ligne à la fin des scripts que tu lances au branchement et au débranchement (par exemple dans les réglages de gestion d'énergie de Plasma) :

```sh
"$HOME/.local/bin/yuki-sync.sh" 3 &
```

`yuki-sync.sh` regarde l'état réel de l'alimentation au moment où il s'exécute, et un verrou empêche deux appels de se chevaucher. Brancher et débrancher très vite ne pose donc pas de problème. Le `3` laisse 3 secondes à l'écran pour finir un éventuel changement de fréquence.

## macOS

Le port macOS a tourné sur un seul vrai Mac pour l'instant : le reste de sa logique est testé avec des données simulées.

```bash
git clone <ce dépôt> ~/src/yuki
~/src/yuki/install-macos.sh
```

Sans les Command Line Tools d'Apple, `git` et `python3` ne sont que des coquilles vides qui ouvrent une fenêtre d'installation, et celle-ci peut échouer (« pas disponible actuellement depuis le serveur de mise à jour »). Yuki n'en a pas besoin : installe Python depuis [python.org](https://www.python.org/downloads/macos/), puis, dans un nouveau terminal :

```bash
mkdir -p ~/src/yuki
curl -fsSL https://github.com/Roaanf/yuki/archive/refs/heads/main.tar.gz | tar xz -C ~/src/yuki --strip-components=1
~/src/yuki/install-macos.sh
```

Le script crée un environnement Python dans `~/Library/Application Support/Yuki/venv`, y installe PyQt6 et pyobjc, puis ajoute un LaunchAgent qui lance Yuki à la connexion. Aucune autorisation système n'est demandée.

- La neige tient sur les fenêtres et sur le Dock, s'il est en bas de l'écran, visible et si sa largeur peut être mesurée. Sinon, elle tombe jusqu'au bas de l'écran, où roule le chasse-neige.
- Yuki se met en pause sur batterie et reprend sur secteur. Pour continuer sur batterie, coche « Neiger sur batterie » dans ⚙ Réglages.
- Rien ne s'affiche sur les apps en plein écran.
- Pour changer les réglages : `YUKI_ARGS="--density 0.5" ~/src/yuki/install-macos.sh`.
- Pour relancer : `~/.local/bin/yuki-restart`. Le journal est dans `~/Library/Logs/yuki.log`.

### Désinstaller sur macOS

Ces commandes arrêtent Yuki, retirent le démarrage automatique et suppriment l'environnement Python, le lien `yuki-restart`, le journal et les réglages du menu :

```bash
launchctl bootout "gui/$(id -u)/org.roland.yuki" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/org.roland.yuki.plist"
rm -rf "$HOME/Library/Application Support/Yuki"
rm -f "$HOME/.local/bin/yuki-restart" "$HOME/Library/Logs/yuki.log"
defaults delete com.yuki.yuki 2>/dev/null
```

PyQt6 et pyobjc étaient installés uniquement dans cet environnement : rien d'autre ne reste sur le système. Tu peux ensuite supprimer le dossier du dépôt.

## Windows

Le port Windows n'a pas encore été essayé sur un vrai PC : sa logique est testée avec des données simulées. Il faut Windows 10 ou 11 en 64 bits et Python 3 depuis [python.org](https://www.python.org/downloads/windows/).

Depuis le dossier du dépôt, dans PowerShell :

```powershell
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
```

Le script crée un environnement Python dans `%LOCALAPPDATA%\Yuki\venv`, y installe PyQt6, lance Yuki et l'ajoute au démarrage de la session. Il ne demande pas de droits administrateur.

- La neige tient sur les fenêtres et sur la barre des tâches, si elle est en bas de l'écran et visible.
- Les écrans avec des échelles différentes (100 %, 125 %, 150 %…) sont pris en charge.
- Sur batterie, l'overlay est retiré et Yuki se met en pause. Pour continuer, coche « Neiger sur batterie » dans ⚙ Réglages. La neige consomme beaucoup : un portable peut y perdre une bonne part de son autonomie.
- Il est aussi retiré au-dessus d'une app ou d'un jeu en plein écran.
- Sur Windows 11, l'icône ❄ peut être rangée dans les icônes masquées, derrière la flèche de la barre des tâches.
- Pour changer les réglages de lancement : `$env:YUKI_ARGS="--density 0.5"`, puis relance l'installation.
- Pour relancer : ajoute `-Restart` à la commande d'installation. Le journal est dans `%LOCALAPPDATA%\Yuki\yuki.log`.
- Pour désinstaller : ajoute `-Uninstall`. Le script arrête Yuki et supprime le démarrage automatique, l'environnement Python, le journal et les réglages du menu.

## Comment ça marche

Le code est dans `yuki/` : `core.py` (simulation et dessin), `view.py` (overlay), `tray.py` (icône), `app.py` (boucle commune) et `backends/` (`kde.py`, `macos.py`, `windows.py`). Chaque backend fournit la liste des fenêtres à `Sim.apply()`.

Sur KDE :

- **L'overlay** est une surface *layer-shell* de la couche « top », créée par la `libLayerShellQtInterface` de Plasma, appelée en ctypes. Sans layer-shell, Yuki se rabat sur une fenêtre « toujours au-dessus ».
- **La position des fenêtres** vient d'un petit script KWin chargé par D-Bus au démarrage et déchargé à la sortie. Il envoie la géométrie, l'ordre d'empilement, l'état maximisé ou plein écran, le bureau virtuel et l'activité de chaque fenêtre.
- **Le rendu** passe par un `QBackingStore` géré à la main : il est recréé quand KWin recrée la surface, par exemple lors d'un changement de fréquence d'écran. Seules les tuiles de 32 px qui ont changé sont redessinées.

## Dépannage

| Symptôme | Cause / solution |
|---|---|
| `Could not load the Qt platform plugin "wayland"` | Le PyQt6 utilisé vient de pip : `pip uninstall PyQt6 PyQt6-Qt6 PyQt6-sip`, puis installe celui de la distribution |
| De la neige qui flotte dans le vide | `python3 yuki.py --debug` liste les fenêtres prises en compte : repère la fenêtre invisible responsable |
| La neige est mal placée sur le panneau | Ajuste `--panel-gap` |
| Plantage | `journalctl --user -u yuki -n 50` et `coredumpctl info` |
