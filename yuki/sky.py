"""Ciel et météo : aurores et étoiles filantes, lucioles, averses, givre sur les bords de l'écran.

Ce qui est grand et change lentement (aurore, givre) est pré-rendu dans des images en cache :
la neige qui passe devant ne fait que recopier un bout d'image, au lieu de tout rasteriser.
"""

import math
import random

from PyQt6.QtCore import QLineF, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                         QRadialGradient, QRegion, QTransform)

from .fun import AUTO, AUTUMN
from .shared import COL, GRAVITY, LAND_BAND

AURORA_STEP = 0.6        # s entre deux images de l'aurore
AURORA_LEN = 240.0       # durée d'un épisode (s)
AURORA_FADE = 25.0
AURORA_BAND = 0.36       # part haute de l'écran où elle danse
AURORA_CURTAINS = (      # couleur, frange du haut, amplitude, vitesse, hauteur de base
    ((90, 255, 170), (190, 120, 255), 0.10, 0.07, 0.30),
    ((80, 220, 255), (120, 255, 200), 0.07, 0.05, 0.46),
)
STAR_LIFE = 0.75
FIREFLIES_1080P = 16
RAIN_FADE = 10.0
RAIN_1080P = 80
MAX_DROPS = 40
MAX_SPLASHES = 80          # chacune fait redessiner le tas en dessous
FROST_CELL = 8           # px le long du bord
FROST_THICK = 100        # épaisseur des bandes pré-rendues (px)
FROST_DEPTH = 46.0       # profondeur typique, plus dans les coins
FROST_GROW = 0.45        # px/s pendant qu'on est absent
FROST_MELT = 9.0         # px/s quand on revient
FROST_WIPE = 34.0        # rayon effacé par le curseur
FROST_TICK = 1.0
FROST_EDGES = ("top", "bottom", "left", "right")


def _toward(value, target, step):
    return min(target, value + step) if value < target else max(target, value - step)


def _frost_transform(edge):
    """(le long du bord, profondeur) -> coordonnées dans la bande de ce bord."""
    if edge == "top":
        return QTransform()
    if edge == "bottom":
        return QTransform(1, 0, 0, -1, 0, FROST_THICK)
    if edge == "left":
        return QTransform(0, 1, 1, 0, 0, 0)
    return QTransform(0, 1, -1, 0, FROST_THICK, 0)


class SkyMixin:
    """Méthodes ajoutées à Sim, sur le modèle de FunMixin."""

    def _sky_init(self):
        self.night = False
        self.summer_night = False
        self.aurora_every = 20.0  # minutes (0 = jamais toute seule), soirs d'hiver
        self.aurora_timer = 0.0
        self.aurora_left = 0.0
        self.aurora_clock = 0.0
        self.aurora_phase = 0
        self.aurora_level = 0.0   # niveau figé à chaque pas, pour que le cache reste valable
        self._aurora_img = {}
        self._sky_band = QRegion()
        self.stars = []           # [x, y, vx, vy, âge, durée]
        self.star_timer = 30.0
        self.fireflies = []       # [x, y, vx, vy, phase, période, écran]
        self.firefly_force = 0.0
        self.firefly_level = 0.0
        self.snow_level = 1.0     # les nuits d'été, les lucioles remplacent la neige
        self.rain_every = 12.0    # minutes, en automne
        self.rain_timer = 0.0
        self.rain_left = self.rain_len = 0.0
        self.streaks = []         # [x, y, vx, vy, longueur, écran]
        self.splashes = []        # [x, y, vx, vy, âge]
        self.drops = []           # [sid, rx, ry, r, pause, vitesse, traînée, âge, durée]
        self.drop_timer = 0.0
        self.frost_after = 2.0    # minutes sans rien toucher avant le givre (0 = jamais)
        self.frost_force = 0.0
        self.frost = []           # par écran : {bord: {"d", "T", "ver", "max"}}
        self.frost_clock = 0.0
        self._frost_img = {}
        self._frost_cursor = None
        self._glow = {}

    def _sky_screens(self):
        self._aurora_img = {}
        self._frost_img = {}
        self.fireflies = []
        self.streaks = []
        self.frost = [self._frost_screen(scr) for scr in self.screens]
        self._sky_band = self._band_region()

    def _sky_calendar(self, now):
        self.night = now.hour >= 18 or now.hour < 6
        self.summer_night = (self.season_choice == AUTO and now.month in (6, 7, 8)
                             and (now.hour >= 21 or now.hour < 5))

    def _step_sky(self, dt):
        self._step_aurora(dt)
        self._step_stars(dt)
        self._step_fireflies(dt)
        self._step_rain(dt)
        self._step_frost(dt)

    def _sky_lively(self):
        return bool(self.stars)     # la pluie reste à 30 images/s : ça suffit, et c'est 2× moins cher

    # ---- outils ---------------------------------------------------------
    def _desktop_region(self, view):
        """Là où on voit le fond d'écran : la vue moins toutes les fenêtres et panneaux."""
        reg = QRegion(view.toAlignedRect())
        for s in self.order:
            if not s.ground:
                reg = reg.subtracted(QRegion(QRect(int(s.x), int(s.y), int(s.w), int(s.h))))
        return reg

    def _band_region(self):
        reg = QRegion()
        for scr in self.screens:
            reg = reg.united(QRegion(QRect(int(scr.x()), int(scr.y()), int(scr.width()),
                                           int(scr.height() * AURORA_BAND) + 1)))
        for s in self.order:
            if not s.ground:
                reg = reg.subtracted(QRegion(QRect(int(s.x), int(s.y), int(s.w), int(s.h))))
        return reg

    def _sky_windows_changed(self):
        """Les fenêtres ont bougé : l'aurore (derrière elles) doit être redessinée là où ça change."""
        band = self._band_region()
        if self.aurora_level > 0:
            r = band.xored(self._sky_band).boundingRect()
            if not r.isEmpty():
                self._add_tiles(self.tiles, r.x(), r.y(), r.x() + r.width(), r.y() + r.height())
        self._sky_band = band

    def _glow_sprite(self, dpr):
        img = self._glow.get(dpr)
        if img is None:
            size = math.ceil(20 * dpr)
            img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(0)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            g = QRadialGradient(size / 2, size / 2, size / 2)
            g.setColorAt(0, QColor(255, 255, 200, 255))
            g.setColorAt(0.12, QColor(240, 255, 160, 235))
            g.setColorAt(0.35, QColor(200, 255, 110, 110))
            g.setColorAt(1, QColor(180, 255, 90, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QRectF(0, 0, size, size))
            p.end()
            img.setDevicePixelRatio(dpr)
            self._glow[dpr] = img
        return img

    # ---- aurore boréale ---------------------------------------------------
    def start_aurora(self):
        if self.aurora_left > 0:
            return False
        self.aurora_left = AURORA_LEN
        self.aurora_timer = 0.0
        return True

    def _step_aurora(self, dt):
        if self.aurora_left <= 0:
            if self.aurora_every > 0 and self.winter and self.night:
                self.aurora_timer += dt
                if self.aurora_timer >= self.aurora_every * 60:
                    self.start_aurora()
            if self.aurora_level == 0:
                return
        self.aurora_left = max(0.0, self.aurora_left - dt)
        self.aurora_clock += dt
        if self.aurora_clock < AURORA_STEP and self.aurora_left > 0:
            return
        self.aurora_clock = 0.0
        elapsed = AURORA_LEN - self.aurora_left
        level = max(0.0, min(1.0, elapsed / AURORA_FADE, self.aurora_left / AURORA_FADE))
        self.aurora_phase += 1
        self.aurora_level = level
        if level == 0:
            self._aurora_img = {}
        r = self._sky_band.boundingRect()
        if not r.isEmpty():
            self._add_tiles(self.tiles, r.x(), r.y(), r.x() + r.width(), r.y() + r.height())

    def _aurora_image(self, i, scr, dpr):
        key = (i, dpr)
        hit = self._aurora_img.get(key)
        if hit is not None and hit[0] == self.aurora_phase:
            return hit[1]
        w, h = scr.width(), scr.height() * AURORA_BAND
        img = QImage(math.ceil(w * dpr), math.ceil(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(0)
        p = QPainter(img)
        p.setPen(Qt.PenStyle.NoPen)
        t = self.aurora_phase * AURORA_STEP + i * 37.0
        level, step = self.aurora_level, 5
        for k, (col, fringe, amp, speed, base) in enumerate(AURORA_CURTAINS):
            for x in range(0, int(w) + step, step):
                u = x / w
                top = h * (base + amp * math.sin(u * 6.0 + t * speed + k * 1.7)
                           + 0.05 * math.sin(u * 17 + t * speed * 1.9))
                ray = 0.55 + 0.45 * math.sin(u * 90 + t * 0.9 + k * 3) * math.sin(u * 23 - t * 0.4)
                a = level * ray * (0.6 + 0.4 * math.sin(u * 3.1 + t * 0.05 + k))
                if a < 0.03:
                    continue
                length = min(h * (0.35 + 0.2 * ray), h - top)   # fondu fini avant le bas de l'image
                g = QLinearGradient(0, top, 0, top + length)
                g.setColorAt(0, QColor(*fringe, int(45 * a)))
                g.setColorAt(0.18, QColor(*col, int(70 * a)))
                g.setColorAt(1, QColor(*col, 0))
                p.fillRect(QRectF(x, top, step, length), QBrush(g))
        p.end()
        self._aurora_img[key] = (self.aurora_phase, img)
        return img

    def _paint_aurora(self, p, view, desktop):
        if self.aurora_level <= 0:
            return
        dpr = p.device().devicePixelRatio()
        for i, scr in enumerate(self.screens):
            if not scr.intersects(view):
                continue
            p.save()
            p.setClipRegion(desktop, Qt.ClipOperation.IntersectClip)
            p.drawImage(QPointF(scr.x(), scr.y()), self._aurora_image(i, scr, dpr))
            p.restore()

    # ---- étoiles filantes -------------------------------------------------
    def start_star(self):
        if not self.screens:
            return False
        scr = random.choice(self.screens)
        d = random.choice((1, -1))
        ang = math.radians(random.uniform(18, 34))
        speed = random.uniform(850, 1050)
        self.stars.append([scr.x() + random.uniform(0.15, 0.85) * scr.width(),
                           scr.y() + random.uniform(0.04, 0.22) * scr.height(),
                           d * speed * math.cos(ang), speed * math.sin(ang), 0.0, STAR_LIFE])
        return True

    def _step_stars(self, dt):
        if self.aurora_every > 0 and self.winter and self.night or self.aurora_left > 0:
            self.star_timer -= dt
            if self.star_timer <= 0:
                self.star_timer = random.uniform(12, 40) if self.aurora_left > 0 else random.uniform(40, 120)
                self.start_star()
        if not self.stars:
            return
        for st in self.stars:
            st[0] += st[2] * dt
            st[1] += st[3] * dt
            st[4] += dt
        self.stars = [st for st in self.stars if st[4] < st[5]]

    @staticmethod
    def _star_tail(st):
        return st[0] - st[2] * 0.09, st[1] - st[3] * 0.09

    def _paint_stars(self, p, desktop):
        if not self.stars:
            return
        p.save()
        p.setClipRegion(desktop, Qt.ClipOperation.IntersectClip)
        for st in self.stars:
            a = math.sin(math.pi * st[4] / st[5])
            tx, ty = self._star_tail(st)
            g = QLinearGradient(tx, ty, st[0], st[1])
            g.setColorAt(0, QColor(255, 255, 255, 0))
            g.setColorAt(1, QColor(255, 250, 230, int(230 * a)))
            pen = QPen(QBrush(g), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(tx, ty), QPointF(st[0], st[1]))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 240, int(255 * a)))
            p.drawEllipse(QPointF(st[0], st[1]), 1.4, 1.4)
        p.restore()

    # ---- lucioles ----------------------------------------------------------
    def start_fireflies(self):
        self.firefly_force = 180.0
        return True

    def _step_fireflies(self, dt):
        self.firefly_force = max(0.0, self.firefly_force - dt)
        auto = self.summer_night
        active = self.firefly_force > 0 or auto
        snow = 0.0 if auto else 1.0
        self.snow_level = _toward(self.snow_level, snow, 0.1 * dt)
        self.firefly_level = _toward(self.firefly_level, 1.0 if active else 0.0, 0.25 * dt)
        if self.firefly_level <= 0:
            self.fireflies = []
            return
        if not self.fireflies:
            for si, scr in enumerate(self.screens):
                n = max(4, int(FIREFLIES_1080P * scr.width() * scr.height() / (1920 * 1080)))
                for _ in range(n):
                    self.fireflies.append([scr.x() + random.random() * scr.width(),
                                           scr.y() + random.uniform(0.4, 0.95) * scr.height(),
                                           random.uniform(-12, 12), random.uniform(-6, 6),
                                           random.random(), random.uniform(2.5, 5.0), si])
        t = self.time
        for f in self.fireflies:
            scr = self.screens[f[6]]
            f[2] = max(-20.0, min(20.0, f[2] + random.uniform(-1, 1) * 40 * dt))
            f[3] = max(-12.0, min(12.0, f[3] + random.uniform(-1, 1) * 30 * dt))
            f[0] += f[2] * dt
            f[1] += (f[3] + 6 * math.sin(t * 1.7 + f[4] * 6)) * dt
            lo, hi = scr.y() + 0.35 * scr.height(), scr.y() + scr.height() - 10
            if not lo <= f[1] <= hi:
                f[1] = max(lo, min(hi, f[1]))
                f[3] = -f[3]
            if not scr.x() + 5 <= f[0] <= scr.x() + scr.width() - 5:
                f[0] = max(scr.x() + 5, min(scr.x() + scr.width() - 5, f[0]))
                f[2] = -f[2]

    def _firefly_glow(self, f):
        return max(0.0, math.sin(math.tau * (self.time / f[5] + f[4]))) ** 3

    def _paint_fireflies(self, p, bounds, factor):
        if not self.fireflies:
            return
        vl, vt, vr, vb = bounds
        dpr = p.device().devicePixelRatio()
        img = self._glow_sprite(dpr)
        half = img.width() / dpr / 2
        opacity = p.opacity()
        for f in self.fireflies:
            if not (vl - 10 <= f[0] <= vr + 10 and vt - 10 <= f[1] <= vb + 10):
                continue
            a = self.firefly_level * self._firefly_glow(f) * factor(f[0], f[1])
            if a < 0.02:
                continue
            p.setOpacity(opacity * a)
            p.drawImage(QPointF(f[0] - half, f[1] - half), img)
        p.setOpacity(opacity)

    # ---- averses ------------------------------------------------------------
    def start_rain(self, length=60.0):
        if self.rain_left > 0:
            return False
        self.rain_left = self.rain_len = length
        self.rain_timer = 0.0
        return True

    def _rain_hit(self, x, y0, y1):
        """Haut d'un tas touché par une goutte entre y0 et y1 (même index que _land)."""
        for k in range(int((y0 - 1) // LAND_BAND), int((y1 + 1) // LAND_BAND) + 1):
            for s in self._tops.get(k, ()):
                if not s.x <= x < s.x + s.w:
                    continue
                c = int((x - s.x) / COL)
                if c < len(s.heights) and s.mask[c]:
                    top = s.y + s.base[c] - s.heights[c]
                    if y0 - 1 <= top <= y1 + 1:
                        return top
        return None

    def _step_rain(self, dt):
        if self.rain_left <= 0 and self.rain_every > 0 and self.season == AUTUMN:
            self.rain_timer += dt
            if self.rain_timer >= self.rain_every * 60:
                self.start_rain(random.uniform(70, 120))
        k = 0.0
        if self.rain_left > 0:
            self.rain_left = max(0.0, self.rain_left - dt)
            k = max(0.0, min(1.0, (self.rain_len - self.rain_left) / RAIN_FADE, self.rain_left / RAIN_FADE))
        if k > 0:
            counts = [0] * len(self.screens)
            for st in self.streaks:
                counts[st[5]] += 1
            for si, scr in enumerate(self.screens):
                want = int(RAIN_1080P * k * scr.width() * scr.height() / (1920 * 1080))
                for _ in range(want - counts[si]):
                    vy = random.uniform(700, 950)
                    self.streaks.append([scr.x() + random.random() * scr.width(),
                                         scr.y() - random.random() * 80, self.wind * 1.5 + 40, vy,
                                         random.uniform(10, 16), si])
            self.drop_timer -= dt
            if self.drop_timer <= 0:
                self.drop_timer = random.uniform(0.15, 0.5) / max(k, 0.3)   # k ≈ 0 au début de l'averse
                self._new_drop()
        if self.streaks:
            keep = []
            bottoms = [scr.y() + scr.height() + 20 for scr in self.screens]
            for st in self.streaks:
                y0 = st[1]
                st[0] += st[2] * dt
                st[1] += st[3] * dt
                top = self._rain_hit(st[0], y0, st[1])
                if top is not None:
                    if len(self.splashes) < MAX_SPLASHES and random.random() < 0.35:
                        for side in (-1, 1):
                            self.splashes.append([st[0], top, side * random.uniform(20, 60),
                                                  random.uniform(-110, -60), 0.0])
                elif st[1] < bottoms[st[5]]:
                    keep.append(st)
            self.streaks = keep
        if self.splashes:
            for sp in self.splashes:
                sp[3] += GRAVITY * 0.6 * dt
                sp[0] += sp[2] * dt
                sp[1] += sp[3] * dt
                sp[4] += dt
            self.splashes = [sp for sp in self.splashes if sp[4] < 0.25]
        self._step_drops(dt)

    def _new_drop(self):
        if len(self.drops) >= MAX_DROPS:
            return
        wins = [s for s in self.order if not s.ground and not s.dock and s.w > 60 and s.h > 60]
        if not wins:
            return
        s = random.choice(wins)
        rx, ry = random.uniform(6, s.w - 6), random.uniform(4, s.h * 0.6)
        x, y = s.x + rx, s.y + ry
        if self._covered(x, y, s) or self._screen_at(x, y) is None:
            return
        self.drops.append([s.sid, rx, ry, random.uniform(2.0, 3.6), random.uniform(0.5, 2.0),
                           0.0, 0.0, 0.0, random.uniform(6.0, 12.0)])

    def _step_drops(self, dt):
        if not self.drops:
            return
        keep = []
        for d in self.drops:
            s = self.surfaces.get(d[0])
            if s is None:
                if d[0] in self.dormant:
                    keep.append(d)
                continue
            d[7] += dt
            if d[4] > 0:
                d[4] -= dt
                d[6] = max(0.0, d[6] - 4 * dt)
            else:
                d[5] = min(90.0, d[5] + 140 * dt)
                d[2] += d[5] * dt
                d[6] = min(28.0, d[6] + d[5] * dt)
                if random.random() < 1.4 * dt:      # ça accroche, puis ça repart
                    d[4], d[5] = random.uniform(0.2, 1.2), 0.0
            if d[2] < s.h - 3 and d[7] < d[8]:
                keep.append(d)
        self.drops = keep

    def _above_region(self, view, s):
        """La vue moins les fenêtres posées au-dessus de s (s elle-même reste visible)."""
        reg = QRegion(view.toAlignedRect())
        if s in self.order:
            for a in self.order[self.order.index(s) + 1:]:
                if not a.ground:
                    reg = reg.subtracted(QRegion(QRect(int(a.x), int(a.y), int(a.w), int(a.h))))
        return reg

    def _paint_drops(self, p, view):
        if not self.drops:
            return
        by_sid = {}
        for d in self.drops:
            by_sid.setdefault(d[0], []).append(d)
        for sid, drops in by_sid.items():
            s = self.surfaces.get(sid)
            if s is None or not QRectF(s.x, s.y, s.w, s.h).intersects(view):
                continue
            p.save()
            p.setClipRegion(self._above_region(view, s), Qt.ClipOperation.IntersectClip)
            for d in drops:
                x, y, r = s.x + d[1], s.y + d[2], d[3]
                fade = max(0.0, min(1.0, d[7] / 0.3, (d[8] - d[7]) / 1.5))
                if d[6] > 1:
                    p.setBrush(QColor(210, 230, 255, int(60 * fade)))
                    p.drawRoundedRect(QRectF(x - r * 0.28, y - d[6], r * 0.56, d[6]), r * 0.28, r * 0.28)
                p.setBrush(QColor(90, 120, 160, int(80 * fade)))
                p.drawEllipse(QPointF(x, y + 0.4), r + 0.4, r * 1.15 + 0.4)
                p.setBrush(QColor(195, 220, 245, int(120 * fade)))
                p.drawEllipse(QPointF(x, y), r, r * 1.15)
                p.setBrush(QColor(255, 255, 255, int(210 * fade)))
                p.drawEllipse(QPointF(x - r * 0.35, y - r * 0.4), r * 0.3, r * 0.25)
            p.restore()

    def _paint_rain(self, p, bounds, factor):
        if not (self.streaks or self.splashes):
            return
        vl, vt, vr, vb = bounds
        by_alpha = {}                   # un seul drawLines par opacité : quelques centaines de traits
        for st in self.streaks:
            if not (vl - 20 <= st[0] <= vr + 20 and vt - 20 <= st[1] <= vb + 20):
                continue
            a = int(150 * factor(st[0], st[1]))
            if a > 3:
                k = st[4] / st[3]
                by_alpha.setdefault(a, []).append(QLineF(st[0] - st[2] * k, st[1] - st[4], st[0], st[1]))
        pen = QPen(QColor(200, 220, 245), 1.1)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        for a, lines in by_alpha.items():
            pen.setColor(QColor(200, 220, 245, a))
            p.setPen(pen)
            p.drawLines(lines)
        p.setPen(Qt.PenStyle.NoPen)
        for sp in self.splashes:
            p.setBrush(QColor(210, 230, 255, int(200 * (1 - sp[4] / 0.25))))
            p.drawEllipse(QPointF(sp[0], sp[1]), 0.9, 0.9)

    # ---- givre ----------------------------------------------------------------
    @staticmethod
    def _frost_screen(scr):
        out = {}
        for edge in FROST_EDGES:
            length = scr.width() if edge in ("top", "bottom") else scr.height()
            n = max(2, int(length // FROST_CELL) + 1)
            v = [random.random() for _ in range(n)]
            for _ in range(3):                  # profil lissé : des avancées et des creux
                v = [(v[max(0, i - 1)] + 2 * v[i] + v[min(n - 1, i + 1)]) / 4 for i in range(n)]
            targets = []
            for i in range(n):
                corner = min(i, n - 1 - i) * FROST_CELL
                boost = 1 + 1.4 * max(0.0, 1 - corner / 260)
                targets.append(min(FROST_THICK - 8, FROST_DEPTH * (0.35 + 0.65 * v[i]) * boost))
            out[edge] = {"d": [0.0] * n, "T": targets, "ver": 0, "max": 0.0}
        return out

    def start_frost(self):
        self.frost_force = 90.0
        return True

    def _frost_growing(self):
        return self.frost_force > 0 or (self.winter and self.frost_after > 0
                                        and self.idle >= self.frost_after * 60)

    def _frost_damage(self, si, edge, i0, i1):
        scr = self.screens[si]
        a0, a1 = i0 * FROST_CELL - 14, (i1 + 1) * FROST_CELL + 14
        x, y, w, h = scr.x(), scr.y(), scr.width(), scr.height()
        if edge == "top":
            self._add_tiles(self.tiles, x + a0, y, x + a1, y + FROST_THICK)
        elif edge == "bottom":
            self._add_tiles(self.tiles, x + a0, y + h - FROST_THICK, x + a1, y + h)
        elif edge == "left":
            self._add_tiles(self.tiles, x, y + a0, x + FROST_THICK, y + a1)
        else:
            self._add_tiles(self.tiles, x + w - FROST_THICK, y + a0, x + w, y + a1)

    def _frost_changed(self, si, edge, cells, i0, i1):
        cells["ver"] += 1
        cells["max"] = max(cells["d"])
        self._frost_damage(si, edge, i0, i1)

    def _step_frost(self, dt):
        self.frost_force = max(0.0, self.frost_force - dt)
        growing = self._frost_growing()
        any_frost = any(c["max"] > 0 for sc in self.frost for c in sc.values())
        if not growing and not any_frost:
            return
        self.frost_clock += dt
        if self.frost_clock >= FROST_TICK:
            step, self.frost_clock = self.frost_clock, 0.0
            for si, sc in enumerate(self.frost):
                for edge, cells in sc.items():
                    d, T = cells["d"], cells["T"]
                    lo = hi = None
                    for i in range(len(d)):
                        if growing:
                            new = min(T[i], d[i] + FROST_GROW * step * (0.6 + 0.8 * ((i * 7919) % 13) / 12))
                        else:
                            new = max(0.0, d[i] - FROST_MELT * step)
                        if new != d[i]:
                            d[i] = new
                            lo = i if lo is None else lo
                            hi = i
                    if lo is not None:
                        self._frost_changed(si, edge, cells, lo, hi)
        if any_frost:
            self._wipe_frost()

    def _wipe_frost(self):
        if self.cursor is None or self.cursor == self._frost_cursor:
            return
        self._frost_cursor = self.cursor
        cx, cy = self.cursor
        r = FROST_WIPE
        for si, scr in enumerate(self.screens):
            x, y, w, h = scr.x(), scr.y(), scr.width(), scr.height()
            if not (x <= cx < x + w and y <= cy < y + h):
                continue
            for edge, dist, along in (("top", cy - y, cx - x), ("bottom", y + h - cy, cx - x),
                                      ("left", cx - x, cy - y), ("right", x + w - cx, cy - y)):
                cells = self.frost[si][edge]
                if dist - r >= cells["max"]:
                    continue
                d = cells["d"]
                lo = hi = None
                for i in range(max(0, int((along - r) // FROST_CELL)),
                               min(len(d), int((along + r) // FROST_CELL) + 1)):
                    o = (i + 0.5) * FROST_CELL - along
                    if abs(o) >= r:
                        continue
                    allowed = max(0.0, dist - math.sqrt(r * r - o * o))
                    if d[i] > allowed:
                        d[i] = allowed
                        lo = i if lo is None else lo
                        hi = i
                if lo is not None:
                    self._frost_changed(si, edge, cells, lo, hi)

    def _frost_image(self, si, edge, scr, cells, dpr):
        key = (si, edge, dpr)
        hit = self._frost_img.get(key)
        if hit is not None and hit[0] == cells["ver"]:
            return hit[1]
        along = scr.width() if edge in ("top", "bottom") else scr.height()
        w, h = (along, FROST_THICK) if edge in ("top", "bottom") else (FROST_THICK, along)
        img = QImage(math.ceil(w * dpr), math.ceil(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setTransform(_frost_transform(edge))
        d = cells["d"]
        path = QPainterPath()
        path.moveTo(0, 0)
        for i, depth in enumerate(d):
            jag = 1 + 0.18 * ((i * 7) % 3 - 1)          # bord dentelé, fixe
            path.lineTo((i + 0.5) * FROST_CELL, depth * jag)
        path.lineTo(along, 0)
        path.closeSubpath()
        g = QLinearGradient(0, 0, 0, FROST_THICK * 0.8)
        g.setColorAt(0, QColor(235, 245, 255, 150))
        g.setColorAt(1, QColor(210, 232, 255, 25))
        p.fillPath(path, QBrush(g))
        pen = QPen(QColor(255, 255, 255, 115), 0.8)
        p.setPen(pen)
        for i in range(1, len(d), 3):                  # cristaux en fougère
            depth = d[i]
            if depth < 10:
                continue
            x0 = (i + 0.5) * FROST_CELL
            sway = ((i * 13) % 7 - 3) * 1.5
            tip = QPointF(x0 + sway, depth * 0.85)
            p.drawLine(QPointF(x0, 0), tip)
            for k in (0.35, 0.6):
                bx, by = x0 + sway * k, depth * 0.85 * k
                arm = depth * 0.18
                p.drawLine(QPointF(bx, by), QPointF(bx - arm, by + arm * 0.7))
                p.drawLine(QPointF(bx, by), QPointF(bx + arm, by + arm * 0.7))
        p.end()
        self._frost_img[key] = (cells["ver"], img)
        return img

    def _paint_frost(self, p, view):
        if not self.frost:
            return
        dpr = p.device().devicePixelRatio()
        for si, scr in enumerate(self.screens):
            if si >= len(self.frost) or not scr.intersects(view):
                continue
            x, y, w, h = scr.x(), scr.y(), scr.width(), scr.height()
            pos = {"top": (x, y), "bottom": (x, y + h - FROST_THICK), "left": (x, y),
                   "right": (x + w - FROST_THICK, y)}
            for edge, cells in self.frost[si].items():
                if cells["max"] <= 0.3:
                    self._frost_img.pop((si, edge, dpr), None)
                    continue
                p.drawImage(QPointF(*pos[edge]), self._frost_image(si, edge, scr, cells, dpr))

    # ---- zones à redessiner ---------------------------------------------------
    def _sky_tiles(self, add, cur):
        for st in self.stars:
            tx, ty = self._star_tail(st)
            add(cur, min(tx, st[0]) - 3, min(ty, st[1]) - 3, max(tx, st[0]) + 3, max(ty, st[1]) + 3)
        for f in self.fireflies:
            add(cur, f[0] - 10, f[1] - 10, f[0] + 10, f[1] + 10)
        for st in self.streaks:
            k = st[4] / st[3]
            add(cur, min(st[0], st[0] - st[2] * k) - 2, st[1] - st[4] - 2,
                max(st[0], st[0] - st[2] * k) + 2, st[1] + 2)
        for sp in self.splashes:
            add(cur, sp[0] - 2, sp[1] - 2, sp[0] + 2, sp[1] + 2)
        for d in self.drops:
            s = self.surfaces.get(d[0])
            if s is not None:
                x, y, r = s.x + d[1], s.y + d[2], d[3]
                add(cur, x - r - 2, y - d[6] - r - 2, x + r + 2, y + r * 1.2 + 2)
