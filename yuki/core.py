"""Simulation et dessin de la neige : aucun code propre à une plateforme ici."""

import math
import random
import sys
import time

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QRegion

from .fun import AUTO, FunMixin
from .shared import (COL, GRAVITY, ICE_MAX, MAX_CLUMPS, MAX_GROUND, MAX_PILE, SHED_SPEED, TILE,
                     ease as _ease)


# --------------------------------------------------------------------------
# Simulation (coordonnées logiques globales, comme KWin)
# --------------------------------------------------------------------------
class Surface:
    """Un bord qui retient la neige : haut d'une fenêtre, d'un panneau, ou le sol."""
    __slots__ = ("sid", "x", "y", "w", "h", "maxh", "heights", "mask", "t", "ground", "dock",
                 "ice", "ice_path", "radius", "base", "radius_key")

    def __init__(self, sid, x, y, w, h, maxh, t, ground=False):
        self.sid, self.x, self.y, self.w, self.h = sid, x, y, w, h
        self.maxh, self.t, self.ground = maxh, t, ground
        self.dock = False
        self.ice = []             # glaçons [position, longueur, longueur max, demi-largeur]
        self.ice_path = None
        n = max(1, int(w // COL))
        self.heights = [0.0] * n
        self.mask = [True] * n
        self.radius = 0.0         # coins arrondis (macOS) : le bord descend près des coins
        self.base = [0.0] * n     # descente du bord au centre de chaque colonne
        self.radius_key = None

    def resize(self, w):
        n = max(1, int(w // COL))
        old = self.heights
        m = len(old)
        if n != m:
            self.heights = [old[min(m - 1, int(i * m / n))] for i in range(n)]
            self.mask = [True] * n
            self.base = [0.0] * n
            self.radius_key = None
        self.w = w
        self.ice = [i for i in self.ice if i[0] < w - 4]
        self.ice_path = None

    def cap(self, i):
        n = len(self.heights)
        d = min(i, n - 1 - i)
        c = min(self.maxh, 1.5 + d * 1.2)   # bouts arrondis
        if self.radius:
            c *= max(0.0, 1 - self.base[i] / self.radius) ** 2   # la neige glisse dans la pente
        return c

    def drop(self, rel):
        """Combien le bord descend à rel (px depuis la gauche) à cause des coins arrondis."""
        r = self.radius
        d = min(rel, self.w - rel)
        if r <= 0 or d >= r:
            return 0.0
        d = max(0.0, d)
        return r - math.sqrt(r * r - (r - d) ** 2)

    def set_radius(self, radius):
        radius = max(0.0, min(radius, self.w / 2))
        if self.radius_key == (radius, self.w):
            return False
        self.radius_key = (radius, self.w)
        self.radius = radius
        n = len(self.heights)
        self.base = [0.0] * n
        if radius:
            for i in range(n):
                if min(i, n - 1 - i) * COL < radius:
                    self.base[i] = self.drop((i + 0.5) * COL)
        return True


# --------------------------------------------------------------------------
# Lutins, bonhommes de neige et traîneau du père Noël
# Ils restent « derrière » les fenêtres maximisées (et derrière les fenêtres
# posées au-dessus de leur bord) : on ne les dessine que là où on les verrait.
# --------------------------------------------------------------------------
BUILD_END = 16.5        # durée d'une construction de bonhomme (s)
LAND_BAND = 16          # hauteur des tranches de l'index des surfaces (px)
FLAKE_SUB = 8           # positions sous-pixel des sprites de flocons, par axe
MAX_SNOWMEN = 5
SNOWMAN_LIFE = 20 * 60  # un bonhomme fond au bout de 20 min


class FestiveMixin:
    """Méthodes ajoutées à Sim (utilise self.surfaces, self.order, self.maximized…)."""

    def _festive_init(self):
        self.snowmen = []         # bonhommes (en construction ou finis)
        self.santa = None
        self.sparkles = []        # [x, y, vx, vy, age, life, couleur]
        self.elves_every = 6.0    # minutes (0 = jamais tout seul)
        self.santa_every = 12.0
        self.elves_timer = 0.0
        self.santa_timer = 0.0

    # ---- visibilité --------------------------------------------------
    def _covered(self, x, y, s=None):
        """Point caché par une fenêtre maximisée ou par une fenêtre au-dessus de s ?"""
        for r in self.maximized:
            if r[0] <= x < r[2] and r[1] <= y < r[3]:
                return True
        if s is not None and s in self.order:
            for a in self.order[self.order.index(s) + 1:]:
                if not a.ground and a.x <= x < a.x + a.w and a.y <= y < a.y + a.h:
                    return True
        return False

    def _allowed_region(self, view, s=None):
        reg = QRegion(view.toAlignedRect())
        for r in self.maximized:
            reg = reg.subtracted(QRegion(QRect(int(r[0]), int(r[1]),
                                               int(r[2] - r[0]), int(r[3] - r[1]))))
        if s is not None and s in self.order:
            for a in self.order[self.order.index(s) + 1:]:
                if not a.ground:
                    reg = reg.subtracted(QRegion(QRect(int(a.x), int(a.y), int(a.w), int(a.h))))
        return reg

    def _pile_at(self, s, rel):
        c = int(rel // COL)
        if 0 <= c < len(s.heights):
            return s.heights[c]
        return 0.0

    # ---- bonhommes de neige -----------------------------------------
    def start_snowman(self, force=False):
        cands = []
        for s in self.order:
            if s.w < 140:
                continue
            for _ in range(12):
                rel = random.uniform(40, s.w - 40)
                c = int(rel // COL)
                lo, hi = max(0, c - 6), min(len(s.heights), c + 7)
                if not all(s.mask[lo:hi]):
                    continue
                snow = sum(s.heights[lo:hi]) / max(1, hi - lo)
                if snow < (0.5 if force else 2.0):
                    continue
                x = s.x + rel
                if any(abs((self.surfaces.get(m["sid"]) or s).x + m["rel"] - x) < 45
                       for m in self.snowmen):
                    continue
                if self._covered(x, s.y - 30, s) or self._covered(x, s.y - 2, s):
                    continue
                if not any(sc.contains(QPointF(x, s.y - 45)) for sc in self.screens):
                    continue
                cands.append((s, rel))
                break
        if not cands:
            return False
        s, rel = random.choice(cands)
        alive = [m for m in self.snowmen if not m.get("melt")]
        if len(alive) >= MAX_SNOWMEN:
            alive[0]["melt"] = 0.0            # le plus vieux fond pour laisser la place
        self.snowmen.append({"sid": s.sid, "rel": rel, "age": 0.0, "dir": random.choice((1, -1)),
                             "melt": None, "hat": random.choice(("top", "beanie")),
                             "scarf": random.choice(((220, 60, 70), (60, 120, 220), (60, 170, 110)))})
        self.elves_timer = 0.0
        return True

    def _burst_snowman(self, m, s):
        x, y = s.x + m["rel"], s.y - self._pile_at(s, m["rel"])
        for k in range(14):
            self.clumps.append([x + random.uniform(-8, 8), y - random.uniform(2, 34),
                                random.uniform(-90, 90), random.uniform(-200, -60),
                                random.uniform(1.4, 2.6), 1.2, s.sid if k % 2 else None])

    def _step_festive(self, dt):
        # déclenchements automatiques
        if self.elves_every > 0 and self.winter:
            self.elves_timer += dt
            if self.elves_timer >= self.elves_every * 60:
                if not self.start_snowman():
                    self.elves_timer = self.elves_every * 60 * 0.8   # réessaie un peu plus tard
        if self.santa_every > 0 and self.santa is None and self.winter:
            self.santa_timer += dt
            if self.santa_timer >= self._santa_period():
                self.start_santa()

        keep = []
        for m in self.snowmen:
            s = self.surfaces.get(m["sid"])
            if s is None:
                if m["sid"] in self.dormant:
                    keep.append(m)            # fenêtre réduite : il attend
                continue                      # fenêtre fermée : disparu
            m["age"] += dt
            if m["age"] < 6.0 and m["age"] > 2.5:   # la boule du bas ramasse de la neige
                c = int(m["rel"] // COL)
                for i in range(max(0, c - 4), min(len(s.heights), c + 5)):
                    s.heights[i] = max(0.0, s.heights[i] - 0.6 * dt)
            if m["age"] > SNOWMAN_LIFE and m["melt"] is None:
                m["melt"] = 0.0
            if m["melt"] is not None:
                m["melt"] += dt
                if m["melt"] > 3.0:
                    continue
            # chasse-neige qui passe dessus : pouf
            hit = False
            for pl in self.plows:
                if pl["sid"] == s.sid and abs(pl["rel"] - m["rel"]) < 6:
                    hit = True
            if hit:
                self._burst_snowman(m, s)
                continue
            keep.append(m)
        self.snowmen = keep

        # traîneau
        if self.santa is not None:
            sa = self.santa
            sa["x"] += sa["dir"] * sa["speed"] * dt
            sa["t"] += dt
            scr = sa["screen"]
            sa["y"] = sa["y0"] + 18 * math.sin(sa["t"] * 1.3)
            if random.random() < 0.9:
                for _ in range(2):
                    self.sparkles.append([sa["x"] - sa["dir"] * 18 + random.uniform(-4, 4),
                                          sa["y"] + 6 + random.uniform(-4, 4),
                                          -sa["dir"] * random.uniform(5, 25), random.uniform(10, 35),
                                          0.0, random.uniform(0.8, 1.6),
                                          random.choice(((255, 230, 120), (255, 255, 255), (255, 190, 220)))])
            left, right = scr.x() - 320, scr.x() + scr.width() + 320
            if (sa["dir"] > 0 and sa["x"] > right) or (sa["dir"] < 0 and sa["x"] < left):
                self.santa = None
        keep = []
        for sp in self.sparkles:
            sp[4] += dt
            sp[0] += sp[2] * dt
            sp[1] += sp[3] * dt
            if sp[4] < sp[5]:
                keep.append(sp)
        self.sparkles = keep

    def start_santa(self):
        if self.santa is not None or not self.screens:
            return False
        scr = random.choice(self.screens)
        d = random.choice((1, -1))
        self.santa = {"screen": scr, "dir": d, "speed": 230.0, "t": 0.0,
                      "x": scr.x() - 300 if d > 0 else scr.x() + scr.width() + 300,
                      "y0": scr.y() + 70 + random.uniform(0, 0.15) * scr.height(), "y": 0.0}
        self.santa["y"] = self.santa["y0"]
        self.santa_timer = 0.0
        return True

    def _festive_tiles(self, add, cur):
        for m in self.snowmen:
            s = self.surfaces.get(m["sid"])
            if s is not None:
                x = s.x + m["rel"]
                add(cur, x - 70, s.y - 60, x + 70, s.y + 6)
        if self.santa is not None:
            sa = self.santa
            add(cur, sa["x"] - 230, sa["y"] - 45, sa["x"] + 230, sa["y"] + 40)
        for sp in self.sparkles:
            add(cur, sp[0] - 3, sp[1] - 3, sp[0] + 3, sp[1] + 3)

    # ---- dessin ---------------------------------------------------------
    def _paint_festive_ground(self, p, view):
        for m in self.snowmen:
            s = self.surfaces.get(m["sid"])
            if s is None:
                continue
            x = s.x + m["rel"]
            if x < view.x() - 80 or x > view.x() + view.width() + 80:
                continue
            p.save()
            p.setClipRegion(self._allowed_region(view, s), Qt.ClipOperation.IntersectClip)
            p.translate(x, s.y - self._pile_at(s, m["rel"]) * 0.6)
            self._paint_build(p, m)
            p.restore()

    def _paint_festive_sky(self, p, view):
        if self.santa is None and not self.sparkles:
            return
        p.save()
        p.setClipRegion(self._allowed_region(view), Qt.ClipOperation.IntersectClip)
        for sp in self.sparkles:
            a = int(230 * (1 - sp[4] / sp[5]))
            r, g, b = sp[6]
            p.setBrush(QColor(r, g, b, a))
            rad = 1.6 * (1 - sp[4] / sp[5]) + 0.5
            p.drawEllipse(QPointF(sp[0], sp[1]), rad, rad)
        if self.santa is not None:
            sa = self.santa
            p.translate(sa["x"], sa["y"])
            if sa["dir"] < 0:
                p.scale(-1, 1)
            p.rotate(4 * math.cos(sa["t"] * 1.3))      # le traîneau suit la vague
            self._paint_sleigh(p, sa["t"])
        p.restore()

    # Bonhomme + lutins, origine = pied du bonhomme, y vers le bas
    def _paint_build(self, p, m):
        t = m["age"]
        melt = m["melt"]
        if melt is not None:
            k = max(0.0, 1 - melt / 3.0)
            p.translate(0, 0)
            p.scale(1 + 0.25 * (1 - k), k)          # il s'affaisse en fondant
        d = m["dir"]
        snow = QColor(250, 252, 255)
        edge = QColor(190, 210, 235)

        def ball(cx, cy, r):
            p.setBrush(edge)
            p.drawEllipse(QPointF(cx, cy + 0.6), r, r)
            p.setBrush(snow)
            p.drawEllipse(QPointF(cx - 0.4, cy - 0.3), r - 0.6, r - 0.6)

        # bas : roulé entre 2.5 et 6 s
        rb = 2 + 7 * _ease((t - 2.5) / 3.5) if t > 2.5 else 0
        roll = (1 - _ease((t - 2.5) / 3.5)) * -12 * d
        if rb > 0:
            ball(roll if t < 6 else 0, -rb, rb)
        # milieu : porté puis posé (6 → 8 s)
        if t > 6:
            k = _ease((t - 6) / 2)
            cx = (1 - k) * (-16 * d)
            cy = (1 - k) * -6 + k * -22.5
            ball(cx, cy, 6.3)
        # tête (8 → 9.5 s)
        headless = m.get("headless")
        if t > 8 and not headless:
            k = _ease((t - 8) / 1.5)
            cx = (1 - k) * (16 * d)
            cy = (1 - k) * -5 + k * -33.3
            ball(cx, cy, 4.8)
        if t > 9.5:
            hy = -33.3
            dark = QColor(40, 40, 50)
            if t > 9.8 and not headless:                  # yeux
                p.setBrush(dark)
                p.drawEllipse(QPointF(-1.7, hy - 1.0), 0.75, 0.75)
                p.drawEllipse(QPointF(1.7, hy - 1.0), 0.75, 0.75)
                p.setBrush(QColor(255, 150, 175, 170))    # joues
                p.drawEllipse(QPointF(-2.9, hy + 1.0), 1.0, 0.6)
                p.drawEllipse(QPointF(2.9, hy + 1.0), 1.0, 0.6)
            if t > 10.2 and not headless:                 # carotte
                nose = QPainterPath()
                nose.moveTo(0, hy - 0.2)
                nose.lineTo(4.5 * d, hy + 0.6)
                nose.lineTo(0, hy + 1.2)
                nose.closeSubpath()
                p.fillPath(nose, QColor(255, 140, 40))
            if t > 10.6:                                  # bras
                pen = QPen(QColor(110, 75, 45), 1.3)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawLine(QPointF(-5.5, -23), QPointF(-13, -29))
                p.drawLine(QPointF(-11, -27.5), QPointF(-12.5, -31))
                p.drawLine(QPointF(5.5, -23), QPointF(13, -28))
                p.drawLine(QPointF(11.5, -27.6), QPointF(14.5, -26))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(dark)                          # boutons
                p.drawEllipse(QPointF(0, -24.5), 0.7, 0.7)
                p.drawEllipse(QPointF(0, -20.5), 0.7, 0.7)
            if t > 11.0:                                  # écharpe
                r, g, b = m["scarf"]
                p.setBrush(QColor(r, g, b))
                p.drawRoundedRect(QRectF(-4.6, hy + 3.2, 9.2, 2.4), 1.2, 1.2)
                p.drawRoundedRect(QRectF(1.5 * d - 1.2, hy + 4.5, 2.4, 5.0), 1.0, 1.0)
            if t > 11.4 and not headless:                 # chapeau
                if m["hat"] == "top":
                    p.setBrush(QColor(35, 35, 45))
                    p.drawRect(QRectF(-4.8, hy - 4.6, 9.6, 1.4))
                    p.drawRect(QRectF(-3.0, hy - 10.0, 6.0, 5.6))
                    p.setBrush(QColor(200, 50, 60))
                    p.drawRect(QRectF(-3.0, hy - 6.2, 6.0, 1.2))
                else:
                    r, g, b = m["scarf"]
                    p.setBrush(QColor(r, g, b))
                    p.drawChord(QRectF(-4.6, hy - 8.0, 9.2, 9.0), 0, 180 * 16)
                    p.setBrush(QColor(255, 255, 255))
                    p.drawEllipse(QPointF(0, hy - 8.2), 1.6, 1.6)
            if 9.6 < t < 12.4:                            # petites étincelles magiques
                k = (t * 7) % 1.0
                p.setBrush(QColor(255, 235, 140, int(220 * (1 - k))))
                for ang in (0.3, 2.4, 4.4):
                    a = ang + t * 2
                    p.drawEllipse(QPointF(math.cos(a) * (10 + 6 * k), hy + math.sin(a) * (8 + 5 * k)),
                                  1.0, 1.0)

        # lutins
        if t < BUILD_END:
            for side in (-1, 1):
                if t < 2.5:                               # arrivent en marchant
                    ex = side * (60 - 46 * _ease(t / 2.5))
                    walking, face, jump = True, -side, 0.0
                elif t < 12.0:                            # au travail
                    base = side * 14
                    if t < 6 and side == -d:              # pousse la boule
                        base = roll + side * (rb + 4)
                    ex = base
                    walking = t < 6 and side == -d
                    face = -side
                    jump = 2.5 * abs(math.sin(t * 6)) if 6 < t < 9.5 else 0.0
                elif t < 14.0:                            # contents : ils sautillent
                    ex, walking, face = side * 14, False, -side
                    jump = 4 * abs(math.sin((t - 12) * 7 + side))
                else:                                     # repartent
                    k = _ease((t - 14) / 2.5)
                    ex, walking, face, jump = side * (14 + 60 * k), True, side, 0.0
                fade = 1.0 if t < 15.5 else max(0.0, 1 - (t - 15.5))
                p.save()
                p.setOpacity(p.opacity() * fade)
                p.translate(ex, -jump)
                self._paint_elf(p, face, t * 9 if walking else 0.0, side > 0)
                p.restore()

    def _paint_elf(self, p, face, step, red):
        if face < 0:
            p.scale(-1, 1)
        hat = QColor(222, 60, 72) if red else QColor(50, 160, 95)
        coat = QColor(50, 160, 95) if red else QColor(222, 60, 72)
        dark = QColor(45, 40, 50)
        skin = QColor(255, 218, 185)
        # jambes
        sw = math.sin(step) * 1.6
        p.setBrush(dark)
        p.drawRoundedRect(QRectF(-2.4 + sw, -4.2, 1.8, 4.2), 0.8, 0.8)
        p.drawRoundedRect(QRectF(0.6 - sw, -4.2, 1.8, 4.2), 0.8, 0.8)
        # tunique
        body = QPainterPath()
        body.moveTo(-3.6, -3.6)
        body.lineTo(3.6, -3.6)
        body.lineTo(2.6, -9.5)
        body.lineTo(-2.6, -9.5)
        body.closeSubpath()
        p.fillPath(body, coat)
        p.setBrush(QColor(240, 200, 60))
        p.drawRect(QRectF(-2.9, -6.2, 5.8, 0.9))           # ceinture
        # tête + oreilles pointues
        p.setBrush(skin)
        ear = QPainterPath()
        ear.moveTo(-2.4, -12.2)
        ear.lineTo(-5.0, -13.6)
        ear.lineTo(-2.6, -11.0)
        ear.closeSubpath()
        p.fillPath(ear, skin)
        p.drawEllipse(QPointF(0, -12.0), 2.7, 2.6)
        p.setBrush(dark)
        p.drawEllipse(QPointF(1.0, -12.3), 0.45, 0.55)
        p.setBrush(QColor(255, 140, 160, 170))
        p.drawEllipse(QPointF(1.9, -11.2), 0.7, 0.45)
        # bonnet pointu qui retombe
        cap = QPainterPath()
        cap.moveTo(-2.9, -13.0)
        cap.quadTo(-1.0, -19.5, 4.5, -17.0)
        cap.lineTo(2.9, -13.4)
        cap.closeSubpath()
        p.fillPath(cap, hat)
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QPointF(4.6, -17.0), 1.0, 1.0)
        p.drawRoundedRect(QRectF(-3.1, -13.8, 6.2, 1.3), 0.6, 0.6)

    def _paint_sleigh(self, p, t):
        gold = QColor(240, 196, 70)
        red = QColor(205, 40, 52)
        brown = QColor(140, 92, 55)
        dark = QColor(45, 40, 50)
        # rennes devant (3), harnais doré
        pen = QPen(gold, 0.9)
        for i in range(3):
            rx = 50 + i * 34
            gal = t * 9 + i * 1.7
            ry = -4 + 2.0 * math.sin(gal)
            p.setPen(pen)
            p.drawLine(QPointF(rx - 34 + 14 if i else 22, ry - 2 if i else -8), QPointF(rx - 7, ry - 3))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(brown)
            # pattes au galop
            for lx, ph in ((-6, 0), (-3, 1.4), (5, 2.8), (8, 4.2)):
                a = math.sin(gal + ph) * 0.6
                p.save()
                p.translate(rx + lx, ry + 2)
                p.rotate(math.degrees(a))
                p.drawRoundedRect(QRectF(-0.8, 0, 1.6, 7), 0.8, 0.8)
                p.restore()
            p.drawEllipse(QPointF(rx, ry), 10, 5)                  # corps
            p.setBrush(QColor(235, 215, 185))
            p.drawEllipse(QPointF(rx - 8, ry + 1), 3, 2.6)         # queue claire
            p.setBrush(brown)
            neck = QPainterPath()
            neck.moveTo(rx + 6, ry - 2)
            neck.lineTo(rx + 12, ry - 10)
            neck.lineTo(rx + 15, ry - 8)
            neck.lineTo(rx + 9, ry + 1)
            neck.closeSubpath()
            p.fillPath(neck, brown)
            p.drawEllipse(QPointF(rx + 15, ry - 10), 3.6, 2.8)     # tête
            p.setBrush(dark)
            p.drawEllipse(QPointF(rx + 18.2, ry - 10.4), 0.9, 0.8) # museau
            p.drawEllipse(QPointF(rx + 15.6, ry - 11.0), 0.5, 0.5) # œil
            ap = QPen(QColor(120, 80, 45), 1.0)
            ap.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(ap)                                            # bois
            p.drawLine(QPointF(rx + 13.5, ry - 12.5), QPointF(rx + 11, ry - 18))
            p.drawLine(QPointF(rx + 12, ry - 16), QPointF(rx + 9, ry - 17))
            p.drawLine(QPointF(rx + 15, ry - 12.8), QPointF(rx + 16, ry - 18.5))
            p.drawLine(QPointF(rx + 15.6, ry - 16.5), QPointF(rx + 18.5, ry - 17.5))
            p.setPen(Qt.PenStyle.NoPen)
        # sac de cadeaux
        p.setBrush(QColor(150, 105, 60))
        p.drawEllipse(QPointF(-14, -12), 8, 8)
        for (bx, by, c) in ((-19, -21, (90, 160, 230)), (-12, -22, (240, 200, 70)), (-16, -17, (230, 90, 140))):
            p.setBrush(QColor(*c))
            p.drawRect(QRectF(bx, by, 5, 5))
        # père Noël
        p.setBrush(red)
        p.drawEllipse(QPointF(0, -10), 6.5, 7)                     # manteau
        p.setBrush(QColor(255, 222, 195))
        p.drawEllipse(QPointF(1, -19), 3.6, 3.6)                   # visage
        p.setBrush(QColor(255, 255, 255))
        beard = QPainterPath()
        beard.moveTo(-2.5, -18.5)
        beard.quadTo(1.5, -9.5, 5.0, -18.0)
        beard.closeSubpath()
        p.fillPath(beard, QColor(255, 255, 255))
        p.setBrush(dark)
        p.drawEllipse(QPointF(2.6, -19.8), 0.5, 0.55)
        p.setBrush(QColor(255, 140, 160, 180))
        p.drawEllipse(QPointF(3.6, -18.4), 0.9, 0.5)
        hatp = QPainterPath()                                       # bonnet
        hatp.moveTo(-3.0, -20.5)
        hatp.quadTo(-2.5, -27.0, -8.5, -24.0)
        hatp.lineTo(4.8, -21.0)
        hatp.closeSubpath()
        p.fillPath(hatp, red)
        p.setBrush(QColor(255, 255, 255))
        p.drawRoundedRect(QRectF(-3.4, -21.8, 8.6, 1.8), 0.9, 0.9)
        p.drawEllipse(QPointF(-8.6, -24.0), 1.4, 1.4)
        wave = math.sin(t * 5) * 18                                 # il fait coucou
        p.save()
        p.translate(4, -12)
        p.rotate(-50 + wave)
        p.setBrush(red)
        p.drawRoundedRect(QRectF(0, -1.3, 8, 2.6), 1.3, 1.3)
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QPointF(8.5, 0), 1.6, 1.6)
        p.restore()
        # traîneau
        sl = QPainterPath()
        sl.moveTo(-22, -6)
        sl.quadTo(-24, 4, -14, 5)
        sl.lineTo(14, 5)
        sl.quadTo(22, 4, 20, -8)
        sl.quadTo(16, -2, 10, -2)
        sl.lineTo(-18, -2)
        sl.closeSubpath()
        p.fillPath(sl, red)
        p.setBrush(gold)
        p.drawRect(QRectF(-17, 0.5, 30, 1.2))
        run = QPen(gold, 1.4)
        run.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(run)
        p.drawLine(QPointF(-12, 5), QPointF(-12, 9))
        p.drawLine(QPointF(10, 5), QPointF(10, 9))
        rp = QPainterPath()
        rp.moveTo(-24, 9)
        rp.lineTo(22, 9)
        rp.quadTo(28, 9, 27, 4)
        rp.quadTo(26, 1, 23, 3)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(rp)
        p.setPen(Qt.PenStyle.NoPen)


class Sim(FestiveMixin, FunMixin):
    def __init__(self, screens, density=1.0, wind=15.0, pile=1.0, melt=0.003):
        self.screens = [QRectF(r) for r in screens]
        self.wind = wind
        self.pile = pile
        self.melt = melt
        self.time = 0.0
        self.fullscreen = []
        self.maximized = []
        self.windows_rects = []
        self.over_max = 0.0       # opacité des flocons au-dessus d'une fenêtre maximisée
        self.over_win = 1.0       # … au-dessus des autres fenêtres
        self.user_paused = False  # pause depuis l'icône
        self.window_radius = 0.0  # coins arrondis des fenêtres et du Dock (réglés par le backend)
        self.dock_radius = 0.0
        self.plows = []           # chasse-neige en cours (un par panneau)
        self.plow_every = 10.0    # minutes (0 = jamais tout seul)
        self.plow_timer = 0.0
        self.puffs = []           # fumée du pot d'échappement [x, y, vx, vy, age, r]
        self._festive_init()
        self._fun_init()
        self.surfaces = {}
        self.dormant = {}
        self.order = []            # du bas vers le haut
        self.masks_dirty = True
        self._tops = {}            # voir _index_tops, refait avec les masques
        self._sprites = {}         # voir _flake_sprite
        self.smooth_acc = 0.0
        self.flakes = []           # [x, y, r, vy, phase, amp, alpha, screen]
        self.clumps = []           # [x, y, vx, vy, r]
        self.density = density
        self.tiles = set()        # dégâts de cette image (tuiles de TILE px, coords globales)
        self._prev_items = set()  # tuiles des flocons dessinés à l'image précédente
        self.full = True          # tout redessiner (démarrage, changement d'écran)
        self.grounds = []
        self.set_screens(screens)
        self.set_season(AUTO)

    def set_screens(self, screens):
        """(Re)configure les écrans : au démarrage et quand on branche/change un écran."""
        self.screens = [QRectF(r) for r in screens]
        now = time.monotonic()
        for g in self.grounds:
            self.surfaces.pop(g.sid, None)
        self.grounds = []
        for i, s in enumerate(self.screens):
            g = Surface("ground%d" % i, s.x(), s.y() + s.height(), s.width(), 0,
                        MAX_GROUND, now, ground=True)
            self.grounds.append(g)
            self.surfaces[g.sid] = g
        self.order = self.grounds + [o for o in self.order if not o.ground]
        self.flakes = []
        for i, s in enumerate(self.screens):
            for _ in range(self._flake_count(s)):
                self.flakes.append(self._new_flake(i, initial=True))
        self.masks_dirty = True
        self.full = True

    def _flake_count(self, s):
        return max(10, int(170 * self.density * (s.width() * s.height()) / (1920 * 1080)))

    def set_density(self, density):
        """Ajuste le nombre de flocons sans toucher à la neige déjà posée."""
        self.density = density
        wanted = [self._flake_count(s) for s in self.screens]
        kept = []
        for f in self.flakes:
            if wanted[f[7]] > 0:
                wanted[f[7]] -= 1
                kept.append(f)
        self.flakes = kept
        for i, missing in enumerate(wanted):
            for _ in range(missing):
                self.flakes.append(self._new_flake(i, initial=True))

    # ---- suivi des zones à redessiner ---------------------------------
    @staticmethod
    def _add_tiles(dst, x0, y0, x1, y1):
        # Division avant int() : int() arrondirait vers 0 sur un écran en coordonnées négatives.
        tx0, tx1 = int(x0 // TILE), int(x1 // TILE)
        ty0, ty1 = int(y0 // TILE), int(y1 // TILE)
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                dst.add((tx, ty))

    def _touch(self, s):
        """Le tas de neige de s a changé : cache invalidé + bande à redessiner."""
        s.ice_path = None
        below = (ICE_MAX + 2 if s.ice else 4) + s.radius   # les cadeaux dépassent un peu du tas
        self._add_tiles(self.tiles, s.x - 2, s.y - s.maxh - 14, s.x + s.w + 2, s.y + below)

    # ---- flocons -------------------------------------------------------
    def _new_flake(self, si, initial=False):
        s = self.screens[si]
        r = random.choice((1.0, 1.3, 1.6, 2.0, 2.4, 3.0))
        y = s.y() + random.random() * s.height() if initial else s.y() - random.uniform(5, 40)
        return [s.x() + random.random() * s.width(), y, r,
                22 + r * 16 + random.uniform(-6, 6),
                random.uniform(0, 6.28), random.uniform(6, 22),
                int(130 + 40 * r), si]

    # ---- mise à jour depuis KWin --------------------------------------
    def apply(self, data):
        now = time.monotonic()
        for o in self.order:
            if not o.ground:
                self._touch(o)        # ancienne position
        alive = set(data.get("alive", []))
        new_order, seen = [], set()
        self.fullscreen = []
        self.maximized = []
        self.windows_rects = []
        for item in data.get("w", []):
            try:
                sid, x, y, w, h = item[:5]
            except ValueError:
                continue
            if len(item) > 6 and item[6]:
                self.fullscreen.append(QRectF(x, y, w, h))
            if len(item) > 7 and item[7]:
                self.maximized.append((x, y, x + w, y + h))
            elif not (len(item) > 5 and item[5]):
                self.windows_rects.append((x, y, x + w, y + h))
            if w < 2 * COL:
                continue
            seen.add(sid)
            s = self.surfaces.get(sid)
            if s is not None and not s.ground:
                dt = min(max(now - s.t, 1 / 240), 0.05)
                dx, dy = x - s.x, y - s.y
                if dx or dy:
                    vx, vy = dx / dt, dy / dt
                    speed = math.hypot(vx, vy)
                    shed = min(0.9, max(0.0, (speed - SHED_SPEED) / 1400))
                    if dy > 0 and speed > SHED_SPEED:
                        shed = max(shed, 0.5)   # tirée vers le bas : ça décolle
                    if shed > 0:
                        self._shed(s, shed, vx, vy)
            else:
                s = self.dormant.pop(sid, None) or Surface(sid, x, y, w, h, MAX_PILE, now)
            s.x, s.y, s.h, s.t = x, y, h, now
            s.dock = bool(len(item) > 5 and item[5])
            if w != s.w:
                s.resize(w)
            s.set_radius(self.dock_radius if s.dock else self.window_radius)
            new_order.append(s)

        for sid, s in self.surfaces.items():
            if s.ground or sid in seen:
                continue
            if sid in alive:
                self.dormant[sid] = s       # réduite / autre bureau : on garde
            else:
                self._shed(s, 1.0, 0, 0)    # fermée : tout tombe
        for sid in list(self.dormant):
            if sid not in alive:
                del self.dormant[sid]

        self.surfaces = {s.sid: s for s in new_order}
        for g in self.grounds:
            self.surfaces[g.sid] = g
        self.order = self.grounds + new_order
        self.masks_dirty = True

    def _shed(self, s, frac, vx, vy):
        self._touch(s)
        if frac >= 0.5 and getattr(self, "snowmen", None):
            for m in [m for m in self.snowmen if m["sid"] == s.sid]:
                self._burst_snowman(m, s)
                self.snowmen.remove(m)
        self._fun_shed(s, frac)
        budget = MAX_CLUMPS - len(self.clumps)
        for i, h in enumerate(s.heights):
            if h < 0.4:
                continue
            take = h * frac
            s.heights[i] = h - take
            if budget <= 0 or not s.mask[i]:
                continue
            k = min(3, 1 + int(take / 3))
            mass = take / k                     # la neige qui tombe = celle qu'on a enlevée
            r = min(2.8, 0.9 + math.sqrt(mass) * 0.7)
            for _ in range(k):
                self.clumps.append([
                    s.x + (i + random.random()) * COL,
                    s.y + s.base[i] - random.random() * h,
                    max(-120.0, min(120.0, vx * 0.15)) + random.uniform(-30, 30),
                    max(-80.0, min(vy * 0.15, 0)) + random.uniform(-40, 10),
                    r, mass, s.sid])        # s.sid : ne retombe pas sur sa fenêtre d'origine
                budget -= 1

    # ---- occlusion : quelles colonnes sont visibles --------------------
    @staticmethod
    def _cols(s, left, right):
        n = len(s.heights)
        c0 = max(0, min(n, math.ceil((left - s.x) / COL - 0.5)))
        c1 = max(0, min(n, math.ceil((right - s.x) / COL - 0.5)))
        return c0, c1

    def _compute_masks(self):
        order = self.order
        for idx, s in enumerate(order):
            n = len(s.heights)
            mask = [False] * n
            ey = s.y - 1
            for scr in self.screens:
                if scr.y() <= ey < scr.y() + scr.height():
                    c0, c1 = self._cols(s, scr.x(), scr.x() + scr.width())
                    mask[c0:c1] = [True] * (c1 - c0)
            for a in order[idx + 1:]:
                if a.ground:
                    continue
                if a.y <= ey < a.y + a.h:
                    c0, c1 = self._cols(s, a.x, a.x + a.w)
                    mask[c0:c1] = [False] * (c1 - c0)
            s.mask = mask
            self._touch(s)
        self._index_tops()
        self.masks_dirty = False

    # ---- physique -------------------------------------------------------
    def _index_tops(self):
        """Surfaces (du haut vers le bas de la pile) dont le tas peut croiser chaque tranche de y."""
        index = {}
        for s in reversed(self.order):
            for k in range(int((s.y - s.maxh - 1) // LAND_BAND), int((s.y + s.radius + 1) // LAND_BAND) + 1):
                index.setdefault(k, []).append(s)
        self._tops = index

    def _land(self, x, y0, y1, amount, skip=None):
        k0, k1 = int((y0 - 1) // LAND_BAND), int((y1 + 1) // LAND_BAND)
        if k0 == k1:
            cands = self._tops.get(k0, ())   # cas courant : aucune surface à cette hauteur
        else:
            cands = reversed(self.order) if k0 < k1 else ()   # plusieurs tranches : ordre exact
        for s in cands:
            if s.sid == skip or not (s.x <= x < s.x + s.w):
                continue
            c = int((x - s.x) / COL)
            if c >= len(s.heights) or not s.mask[c]:
                continue
            sy = s.y + s.base[c] - s.heights[c]
            if y0 - 1 <= sy <= y1 + 1:
                for dc, wgt in ((-1, 0.25), (0, 0.5), (1, 0.25)):
                    i = c + dc
                    if 0 <= i < len(s.heights) and s.mask[i]:
                        s.heights[i] = min(s.cap(i), s.heights[i] + amount * wgt)
                self._add_tiles(self.tiles, s.x + (c - 2) * COL, s.y - s.maxh - 3,
                                s.x + (c + 3) * COL, s.y + 4 + s.radius)
                return True
        return False

    def step(self, dt):
        if self.masks_dirty:
            self._compute_masks()
        self.time += dt
        t = self.time
        wind = self.wind * (0.6 + 0.4 * math.sin(t * 0.13)) + 10 * math.sin(t * 0.37)
        dep = 1.2 * self.pile
        fall, sway = self.theme.fall, self.theme.sway

        bounds = [(s.x(), s.width(), s.y() + s.height() + 5) for s in self.screens]
        land = self._land if self.pile > 0 else None
        cos, t13 = math.cos, t * 1.3
        for k, f in enumerate(self.flakes):
            y0 = f[1]
            f[0] += (wind * (0.4 + f[2] / 4) + sway * f[5] * cos(t13 + f[4])) * dt
            f[1] += f[3] * fall * dt
            sx, sw, sb = bounds[f[7]]
            if f[0] < sx:
                f[0] += sw
            elif f[0] >= sx + sw:
                f[0] -= sw
            if (land is not None and land(f[0], y0, f[1], dep * f[2])) or f[1] > sb:
                self.flakes[k] = self._new_flake(f[7])

        bottom = max(s.y() + s.height() for s in self.screens) + 10
        keep = []
        for c in self.clumps:
            y0 = c[1]
            c[3] += GRAVITY * dt
            c[2] *= 0.985
            c[0] += c[2] * dt
            c[1] += c[3] * dt
            if c[3] > 0 and self._land(c[0], y0, c[1], c[5], c[6]):
                continue
            if c[1] < bottom:
                keep.append(c)
        self.clumps = keep
        self._step_plow(dt)
        self._step_festive(dt)
        self._step_fun(dt)
        self._collect_item_tiles()

        self.smooth_acc += dt
        if self.smooth_acc >= 2.0:
            f = 1 - self.melt * self.smooth_acc
            self.smooth_acc = 0.0
            for s in self.order:
                h = s.heights
                n = len(h)
                if n < 3 or max(h) < 0.05:
                    continue
                self._touch(s)
                sm = [h[0] * f]
                for i in range(1, n - 1):
                    sm.append((0.2 * h[i - 1] + 0.6 * h[i] + 0.2 * h[i + 1]) * f)
                sm.append(h[-1] * f)
                s.heights = sm

    def _collect_item_tiles(self):
        """Tout ce qui bouge : position d'avant + position actuelle."""
        cur = set()
        add = self._add_tiles
        reach = self.theme.reach
        put = cur.add
        for f in self.flakes:            # écrit en ligne : appelé pour chaque flocon à chaque image
            r = f[2] * reach + 1.5
            tx0, tx1 = int((f[0] - r) // TILE), int((f[0] + r) // TILE)
            ty0, ty1 = int((f[1] - r) // TILE), int((f[1] + r) // TILE)
            if tx1 - tx0 > 1 or ty1 - ty0 > 1:
                add(cur, f[0] - r, f[1] - r, f[0] + r, f[1] + r)
                continue
            put((tx0, ty0))
            put((tx1, ty0))
            put((tx0, ty1))
            put((tx1, ty1))
        for c in self.clumps:
            r = c[4] + 1.5
            add(cur, c[0] - r, c[1] - r, c[0] + r, c[1] + r)
        for f in self.puffs:
            r = f[5] + 1.5
            add(cur, f[0] - r, f[1] - r, f[0] + r, f[1] + r)
        for pl in self.plows:
            s = self.surfaces.get(pl["sid"])
            if s is not None:
                x = s.x + pl["rel"]
                add(cur, x - 52, s.y - 46, x + 52, s.y + 4)   # camion (2 sens) + gerbe
        self._festive_tiles(add, cur)
        self._fun_tiles(add, cur)
        self.tiles |= cur | self._prev_items
        self._prev_items = cur

    def take_damage(self):
        """Rend (plein écran ?, tuiles) puis remet à zéro pour l'image suivante."""
        full, tiles = self.full, self.tiles
        self.full, self.tiles = False, set()
        return full, tiles

    def hidden_on(self, view):
        """Vrai si une app plein écran couvre cet écran."""
        c = view.center()
        return any(r.contains(c) for r in self.fullscreen)

    # ---- chasse-neige ---------------------------------------------------
    def _screen_of(self, s):
        cx, cy = s.x + s.w / 2, s.y - 1
        for scr in self.screens:
            if scr.x() <= cx < scr.x() + scr.width() and scr.y() <= cy < scr.y() + scr.height():
                return scr
        return None

    def _plow_targets(self):
        """Un bord par écran : le panneau le plus bas de l'écran, sinon le sol."""
        out = []
        for i, scr in enumerate(self.screens):
            docks = [s for s in self.order if s.dock and s.w > 300 and any(s.mask)
                     and self._screen_of(s) == scr]
            if docks:
                out.append(max(docks, key=lambda s: (s.y, s.w)))
            elif any(self.grounds[i].mask):
                out.append(self.grounds[i])
        return out

    def start_plow(self, only_snowy=False):
        busy = {pl["sid"] for pl in self.plows}
        started = False
        for s in self._plow_targets():
            if s.sid in busy:
                continue
            if only_snowy and sum(s.heights) / len(s.heights) <= 1.5:
                continue
            d = random.choice((1, -1))
            self.plows.append({"sid": s.sid, "dir": d, "rel": -70.0 if d > 0 else s.w + 70.0,
                               "load": 0.0, "dist": 0.0, "dumped": False, "puff": 0.0,
                               "speed": random.uniform(105, 135)})
            started = True
        self.plow_timer = 0.0
        return started

    def _step_plow(self, dt):
        # fumée
        keep = []
        for f in self.puffs:
            f[4] += dt
            f[0] += f[2] * dt
            f[1] += f[3] * dt
            f[5] += 6 * dt
            if f[4] < 1.3:
                keep.append(f)
        self.puffs = keep

        if not self.plows and self.plow_every > 0:
            self.plow_timer += dt
            if self.plow_timer >= self.plow_every * 60:
                self.start_plow(only_snowy=True)
        self.plows = [pl for pl in self.plows if self._step_one_plow(pl, dt)]

    def _step_one_plow(self, pl, dt):
        s = self.surfaces.get(pl["sid"])
        if s is None:
            return False
        d = pl["dir"]
        old = pl["rel"]
        pl["rel"] += d * pl["speed"] * dt
        pl["dist"] += pl["speed"] * dt
        n = len(s.heights)

        # racle les colonnes que la lame vient de passer
        a, b = sorted((old, pl["rel"]))
        c0, c1 = max(0, int(a // COL)), min(n, int(b // COL) + 1)
        for c in range(c0, c1):
            pl["load"] += s.heights[c]
            s.heights[c] = 0.0

        bx, by = s.x + pl["rel"], s.y
        on_panel = 0 <= pl["rel"] <= s.w
        # projection de neige devant la lame
        if on_panel and pl["load"] > 2 and len(self.clumps) < MAX_CLUMPS:
            spray = pl["load"] * min(1.0, 1.4 * dt)
            pl["load"] -= spray
            k = 3
            for _ in range(k):
                self.clumps.append([bx + d * 3, by - 14 - random.random() * 4,
                                    d * (pl["speed"] + random.uniform(40, 150)), random.uniform(-240, -120),
                                    min(2.6, 0.9 + math.sqrt(spray / k) * 0.7), spray / k, None])
        # bout du panneau : tout ce qu'il pousse tombe
        if not on_panel and not pl["dumped"] and ((d > 0 and pl["rel"] > s.w) or (d < 0 and pl["rel"] < 0)):
            pl["dumped"] = True
            load, pl["load"] = pl["load"], 0.0
            k = max(1, min(25, int(load / 2)))
            for _ in range(k):
                self.clumps.append([bx + d * random.uniform(0, 10), by - random.uniform(0, 10),
                                    d * random.uniform(20, 90), random.uniform(-60, 0),
                                    min(2.8, 0.9 + math.sqrt(load / k) * 0.7), load / k, s.sid])
        # fumée
        pl["puff"] += dt
        if pl["puff"] > 0.14:
            pl["puff"] = 0.0
            self.puffs.append([bx - d * 42, by - 30, -d * random.uniform(10, 25),
                               random.uniform(-35, -20), 0.0, 1.8])
        return not ((d > 0 and pl["rel"] > s.w + 80) or (d < 0 and pl["rel"] < -80))

    def _paint_plow(self, p):
        for f in self.puffs:
            a = int(110 * (1 - f[4] / 1.3))
            if a > 0:
                p.setBrush(QColor(210, 210, 220, a))
                p.drawEllipse(QPointF(f[0], f[1]), f[5], f[5])
        for pl in self.plows:
            s = self.surfaces.get(pl["sid"])
            if s is not None:
                self._paint_one_plow(p, pl, s)

    def _paint_one_plow(self, p, pl, s):
        d = pl["dir"]
        p.save()
        scr = self._screen_of(s)
        if scr is not None:
            p.setClipRect(scr)          # ne déborde pas sur l'écran voisin
        p.translate(s.x + pl["rel"], s.y + 0.6 * math.sin(pl["dist"] * 0.35))  # petit rebond
        if d < 0:
            p.scale(-1, 1)

        # neige poussée devant la lame
        if pl["load"] > 0.5 and 0 <= pl["rel"] <= s.w:
            h = min(16.0, 2 + math.sqrt(pl["load"]) * 1.1)
            mound = QPainterPath()
            mound.moveTo(-1, 0)
            mound.cubicTo(-1, -h * 1.1, h * 0.6, -h * 0.9, h * 1.7, 0)
            mound.closeSubpath()
            p.fillPath(mound.translated(0, 1.2), self.SHADOW)
            p.fillPath(mound, self.SNOW)

        dark = QColor(45, 45, 58)
        # châssis
        p.setBrush(dark)
        p.drawRoundedRect(QRectF(-46, -8, 36, 4), 1.5, 1.5)
        # bras de la lame
        p.setBrush(QColor(120, 120, 135))
        p.drawRect(QRectF(-12, -10, 7, 3))
        # lame
        blade = QPainterPath()
        blade.moveTo(1, 0)
        blade.lineTo(-6, 0)
        blade.quadTo(-9, -9, -6, -19)
        blade.lineTo(2, -20)
        blade.quadTo(-2, -10, 1, 0)
        p.fillPath(blade, QColor(255, 196, 41))
        p.setBrush(dark)
        p.drawRect(QRectF(-6, -2, 7, 2))
        for i in range(3):                                     # bandes de sécurité
            p.drawRect(QRectF(-7.5 + i * 0.3, -15 + i * 5, 3, 2))
        # capot
        orange = QColor(255, 122, 61)
        p.setBrush(orange)
        p.drawRoundedRect(QRectF(-42, -21, 31, 15), 4, 4)
        p.setBrush(QColor(255, 160, 110))
        p.drawRoundedRect(QRectF(-24, -19, 11, 3), 1.5, 1.5)   # reflet
        # cabine
        p.setBrush(orange)
        p.drawRoundedRect(QRectF(-43, -34, 18, 16), 4, 4)
        p.setBrush(QColor(196, 233, 255))
        p.drawRoundedRect(QRectF(-38, -31, 11, 9), 2.5, 2.5)
        # petit conducteur ^_^
        p.setBrush(dark)
        p.drawEllipse(QPointF(-34.2, -26.8), 0.9, 0.9)
        p.drawEllipse(QPointF(-30.6, -26.8), 0.9, 0.9)
        p.setBrush(QColor(255, 140, 170, 200))
        p.drawEllipse(QPointF(-35.6, -25.0), 1.1, 0.7)
        p.drawEllipse(QPointF(-29.2, -25.0), 1.1, 0.7)
        # pot d'échappement
        p.setBrush(QColor(110, 110, 125))
        p.drawRoundedRect(QRectF(-46, -30, 2.5, 12), 1, 1)
        # gyrophare qui clignote
        blink = 0.5 + 0.5 * math.sin(self.time * 9)
        p.setBrush(QColor(255, 170, 0, int(50 + 90 * blink)))
        p.drawEllipse(QPointF(-34, -36.5), 5 + 3 * blink, 4 + 2 * blink)
        p.setBrush(QColor(255, 200, 40))
        p.drawRoundedRect(QRectF(-36.5, -38.5, 5, 4), 2, 2)
        # roues qui tournent
        ang = pl["dist"] / 5.0
        for cx in (-37.0, -18.0):
            p.setBrush(dark)
            p.drawEllipse(QPointF(cx, -5), 5, 5)
            p.setBrush(QColor(170, 170, 185))
            p.drawEllipse(QPointF(cx, -5), 2.2, 2.2)
            p.setBrush(dark)
            for k in range(3):
                a = ang + k * 2.094
                p.drawEllipse(QPointF(cx + 1.4 * math.cos(a), -5 + 1.4 * math.sin(a)), 0.6, 0.6)
        p.restore()

    # ---- dessin ---------------------------------------------------------
    SNOW = QColor(255, 255, 255, 240)
    SHADOW = QColor(120, 150, 190, 90)

    @staticmethod
    def _pile_spans(s, damage):
        """Colonnes [a, b) du tas qui touchent les tuiles abîmées, marge comprise.
        damage : {ligne de tuiles: [(x0, x1) globaux]} ou None (tout)."""
        n = len(s.heights)
        if damage is None:
            return ((0, n),)
        left, right = s.x, s.x + s.w
        spans = []
        for ty in range(int((s.y - s.maxh - 3) // TILE), int((s.y + s.radius + 4) // TILE) + 1):
            for x0, x1 in damage.get(ty, ()):
                if x1 <= left or x0 >= right:
                    continue
                # 2 colonnes de marge : dans la zone clippée, le contour partiel = le contour complet
                a = max(0, math.floor((x0 - left) / COL) - 2)
                b = min(n, math.ceil((x1 - left) / COL) + 2)
                spans.append((a, b))
        if len(spans) < 2:
            return spans
        spans.sort()
        out = [list(spans[0])]
        for a, b in spans[1:]:
            if a <= out[-1][1]:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return out

    def _paint_pile(self, p, s, a, b):
        """Rasteriser tout le tas à chaque image coûtait l'essentiel du CPU : seulement [a, b)."""
        h, m, base, x0, y = s.heights, s.mask, s.base, s.x, s.y
        path = QPainterPath()
        i = a
        while i < b:
            if not m[i] or h[i] < 0.35:
                i += 1
                continue
            path.moveTo(x0 + i * COL, y + s.drop(i * COL))
            j = i
            while j < b and m[j] and h[j] >= 0.35:
                path.lineTo(x0 + (j + 0.5) * COL, y + base[j] - h[j])
                j += 1
            path.lineTo(x0 + j * COL, y + s.drop(j * COL))
            if s.radius:
                for k in range(j - 1, i, -1):    # dessous du tas : le long du coin
                    if base[k] or base[k - 1]:
                        path.lineTo(x0 + k * COL, y + s.drop(k * COL))
            path.closeSubpath()
            i = j
        if path.isEmpty():
            return
        p.fillPath(path.translated(0, 1.5), self.SHADOW)
        p.fillPath(path, self.SNOW)
        if self.theme.shape is not None:
            self._paint_speck(p, s, a, b)

    def _flake_sprite(self, dpr, r, kx, ky):
        """Flocon pré-rendu, centré à (kx + 0.5, ky + 0.5) / FLAKE_SUB px d'un pixel entier."""
        key = (dpr, self.theme.clump, r, kx, ky)
        sp = self._sprites.get(key)
        if sp is None:
            half = math.ceil(r * dpr) + 2
            sp = QImage(2 * half + 1, 2 * half + 1, QImage.Format.Format_ARGB32_Premultiplied)
            sp.fill(0)
            q = QPainter(sp)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(QColor(*self.theme.clump))
            q.drawEllipse(QPointF(half + (kx + 0.5) / FLAKE_SUB, half + (ky + 0.5) / FLAKE_SUB),
                          r * dpr, r * dpr)
            q.end()
            sp.setDevicePixelRatio(dpr)
            self._sprites[key] = sp = (sp, half)
        return sp

    def _paint_round_flakes(self, p, bounds, factor):
        """Sprites plutôt que drawEllipse : 2 à 2,5× moins cher, au 1/16 de pixel près."""
        vl, vt, vr, vb = bounds
        dpr = p.device().devicePixelRatio()
        opacity = p.opacity()
        sprite, floor, last = self._flake_sprite, math.floor, -1
        for f in self.flakes:
            x, y = f[0], f[1]
            if not (vl - 4 <= x <= vr + 4 and vt - 4 <= y <= vb + 4):
                continue
            a = int(f[6] * factor(x, y))
            if a <= 3:
                continue
            if a != last:
                p.setOpacity(opacity * a / 255)
                last = a
            dx, dy = (x - vl) * dpr, (y - vt) * dpr
            ix, iy = floor(dx), floor(dy)
            img, half = sprite(dpr, f[2], int((dx - ix) * FLAKE_SUB), int((dy - iy) * FLAKE_SUB))
            p.drawImage(QPointF(vl + (ix - half) / dpr, vt + (iy - half) / dpr), img)
        p.setOpacity(opacity)

    def paint(self, p, view, damage=None):
        """damage : tuiles abîmées déjà posées en clip, voir _pile_spans (None = tout)."""
        p.save()
        p.translate(-view.x(), -view.y())
        p.setPen(Qt.PenStyle.NoPen)
        vl, vt = view.x(), view.y()
        vr, vb = vl + view.width(), vt + view.height()

        for s in self.order:
            if s.x > vr or s.x + s.w < vl or s.y < vt - 1 or s.y > vb + 1:
                continue
            for a, b in self._pile_spans(s, damage):
                self._paint_pile(p, s, a, b)
            if s.ice:
                self._paint_ice(p, s)

        self._paint_festive_ground(p, view)
        self._paint_fun_ground(p, view)
        self._paint_plow(p)
        self._paint_festive_sky(p, view)
        self._paint_fun_sky(p, view)

        def inside(rects):
            return [r for r in rects if r[0] < vr and r[2] > vl and r[1] < vb and r[3] > vt]
        maxr = inside(self.maximized) if self.over_max < 1 else []
        winr = inside(self.windows_rects) if self.over_win < 1 else []

        def factor(x, y):
            for r in maxr:
                if r[0] <= x < r[2] and r[1] <= y < r[3]:
                    return self.over_max
            for r in winr:
                if r[0] <= x < r[2] and r[1] <= y < r[3]:
                    return self.over_win
            return 1.0

        col = QColor(*self.theme.clump)
        if self.theme.shape is not None:
            self._paint_shaped_flakes(p, (vl, vt, vr, vb), factor)
        else:
            self._paint_round_flakes(p, (vl, vt, vr, vb), factor)
        for c in self.clumps:
            if vl - 4 <= c[0] <= vr + 4 and vt - 4 <= c[1] <= vb + 4:
                a = int(240 * factor(c[0], c[1]))
                if a <= 3:
                    continue
                col.setAlpha(a)
                p.setBrush(col)
                p.drawEllipse(QPointF(c[0], c[1]), c[4], c[4])
        p.restore()


class WindowFeed:
    """Transmet à Sim la liste des fenêtres fournie par le backend."""

    def __init__(self, sim, debug=False):
        self.sim = sim
        self.debug = debug
        self.last_debug = None

    def push(self, data):
        try:
            if self.debug:
                lines = ["%-28s %-22s x=%-5d y=%-5d %dx%d" % (
                    (it[8] if len(it) > 8 else "")[:28], (it[9] if len(it) > 9 else "")[:22],
                    it[1], it[2], it[3], it[4]) for it in data.get("w", [])]
                key = "\n".join(l.split(" x=")[0] for l in lines)
                if key != self.last_debug:
                    self.last_debug = key
                    print("\n--- fenêtres (du bas vers le haut) ---\n" + "\n".join(lines), flush=True)
            self.sim.apply(data)
        except Exception as e:  # ne jamais planter sur un message bizarre
            print("yuki: message ignoré:", e, file=sys.stderr)
