#!/usr/bin/env python3
"""
Yuki — neige sur le bureau pour KDE Plasma 6 (Wayland ou X11) et macOS.

- Les flocons tombent par-dessus tout, les clics passent au travers.
- La neige s'accumule sur le haut des fenêtres, des panneaux et du Dock.
- Déplace une fenêtre d'un coup sec : la neige glisse et tombe.
- Ferme ou réduis une fenêtre : sa neige tombe (ou reste au chaud si réduite).

Le code est dans le paquet yuki/ ; ce fichier reste le point d'entrée
(les scripts repèrent le processus avec le motif « python… yuki.py »).
"""

from yuki.app import main

if __name__ == "__main__":
    main()
