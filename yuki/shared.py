"""Constantes communes à la simulation et aux animations."""

COL = 4               # largeur d'une colonne de neige (px logiques)
MAX_PILE = 14.0       # hauteur max sur une fenêtre
MAX_GROUND = 22.0     # hauteur max au sol
GRAVITY = 900.0
SHED_SPEED = 220.0    # vitesse (px/s) à partir de laquelle la neige glisse
MAX_CLUMPS = 1500
TILE = 32             # taille des tuiles pour ne redessiner que ce qui bouge
ICE_MAX = 16.0        # longueur max d'un glaçon


def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)
