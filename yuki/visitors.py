"""Visiteurs : lutin en luge, oiseau postier, retour après une absence."""

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainterPath, QPen

from .shared import COL, GRAVITY, ease

SLED_FRICTION = 32.0     # px/s² sur la neige
BIRD_GAP = 20.0          # s minimum entre deux oiseaux venus pour des notifications
BIRD_ARRIVE = 1.9
BIRD_DROP = BIRD_ARRIVE + 0.7
BIRD_END = BIRD_DROP + 4.1
AWAY_AFTER = 600.0       # s sans rien toucher : on est parti
AWAY_PILE = 3.0          # pendant l'absence, la neige tient 3× plus vite


class VisitorsMixin:
    """Méthodes ajoutées à Sim, sur le modèle de FunMixin."""

    def _visitors_init(self):
        self.sled = None
        self.bird = None
        self.bird_on = 1.0        # l'oiseau vient pour les notifications
        self._bird_last = -1e9
        self.idle = 0.0           # secondes sans rien toucher
        self.idle_external = None  # donné par le backend (clavier compris), sinon on suit le curseur
        self._idle_cursor = None
        self._cursor_idle = 0.0
        self.away = False
        self._away_max = 0.0
        self._welcomed = -1e9

    def _step_visitors(self, dt):
        self._step_idle(dt)
        self._step_sled(dt)
        self._step_bird(dt)

    def _visitors_lively(self):
        return self.sled is not None or self.bird is not None

    # ---- absence ------------------------------------------------------------
    def _step_idle(self, dt):
        if self.idle_external is not None:
            self.idle = self.idle_external
        else:
            if self.cursor != self._idle_cursor:
                self._idle_cursor = self.cursor
                self._cursor_idle = 0.0
            else:
                self._cursor_idle += dt
            self.idle = self._cursor_idle
        if self.idle >= AWAY_AFTER:
            self.away = True
            self._away_max = max(self._away_max, self.idle)
        elif self.away and self.idle < 3:
            self.away = False
            gone, self._away_max = self._away_max, 0.0
            self.welcome_back(gone)

    def welcome_back(self, seconds=1800.0):
        """De retour : la neige a eu le temps de s'entasser, et Neko-chan dort dessus."""
        if self.time - self._welcomed < 30:
            return False          # veille + inactivité : une seule fois
        self._welcomed = self.time
        if self.pile > 0:
            extra = min(1.0, seconds / 1800)
            for s in self.order:
                h, m = s.heights, s.mask
                for i in range(len(h)):
                    if m[i]:
                        want = s.cap(i) * (0.5 + 0.45 * extra) * (0.85 + 0.15 * math.sin(i * 0.7 + s.x))
                        if h[i] < want:
                            h[i] = want
                self._touch(s)
        if self.cat is None:
            self.start_cat(asleep=True)
        return True

    # ---- lutin en luge -------------------------------------------------------
    def start_sled(self, launch=None, skip=None):
        """launch = (x, y, vx, vy) : part en l'air (fenêtre secouée). Sinon, descend d'une fenêtre."""
        if self.sled is not None or not self.screens:
            return False
        sl = {"state": "ride", "sid": None, "rel": 0.0, "dir": 1, "speed": 0.0, "t": 0.0,
              "x": 0.0, "y": 0.0, "vx": 0.0, "vy": 0.0, "skip": skip, "skip_t": 0.4}
        if launch is not None:
            sl["state"] = "air"
            sl["x"], sl["y"], sl["vx"], sl["vy"] = launch
            sl["dir"] = 1 if sl["vx"] >= 0 else -1
        else:
            cands = []
            for s in self.order:
                if s.ground or s.w < 220:
                    continue
                d = random.choice((1, -1))
                rel = s.w - 160 if d > 0 else 160.0
                c = int(rel // COL)
                x, y = s.x + rel, s.y - 10
                if (s.mask[c] and s.heights[c] > 1 and self._screen_at(x, y) is not None
                        and not self._covered(x, y, s)):
                    cands.append((s, rel, d))
            if not cands:
                return False
            s, rel, d = random.choice(cands)
            sl.update(sid=s.sid, rel=rel, dir=d, speed=150.0, x=s.x + rel, y=s.y)
        self.sled = sl
        return True

    def _visitor_shed(self, s, frac, vx, vy):
        """Fenêtre secouée : un lutin part en luge avec la neige (appelé avant qu'elle glisse)."""
        if (self.sled is not None or not self.winter or s.dock or s.ground or frac < 0.5
                or not (vx or vy) or random.random() >= 0.5):
            return
        i = max(range(len(s.heights)), key=lambda k: s.heights[k] if s.mask[k] else -1)
        if s.heights[i] < 2.5 or not s.mask[i]:
            return
        x, y = s.x + (i + 0.5) * COL, s.y + s.base[i] - s.heights[i]
        self.start_sled((x, y, max(-160.0, min(160.0, vx * 0.2)) + random.choice((-1, 1)) * 70,
                         min(-140.0, vy * 0.2 - 160)), skip=s.sid)

    def _sled_height(self, s, rel):
        c = max(0, min(len(s.heights) - 1, int(rel // COL)))
        return s.y + s.base[c] - s.heights[c] * 0.6

    def _step_sled(self, dt):
        sl = self.sled
        if sl is None:
            return
        sl["t"] += dt
        if sl["state"] == "ride":
            s = self.surfaces.get(sl["sid"])
            if s is None:
                sl.update(state="air", vx=sl["dir"] * sl["speed"], vy=0.0, skip=None)
                return
            sl["speed"] = max(0.0, sl["speed"] - SLED_FRICTION * dt)
            sl["rel"] += sl["dir"] * sl["speed"] * dt
            c = int(sl["rel"] // COL)
            if 0 <= c < len(s.heights):
                s.heights[c] *= 1 - min(1.0, 0.8 * dt)      # la luge creuse sa trace
            if not 0 <= sl["rel"] <= s.w:
                sl.update(state="air", vx=sl["dir"] * max(sl["speed"], 40.0), vy=-30.0,
                          skip=s.sid, skip_t=0.25, x=s.x + sl["rel"], y=s.y)
                return
            sl["x"], sl["y"] = s.x + sl["rel"], self._sled_height(s, sl["rel"])
            if sl["speed"] <= 6:
                sl.update(state="stop", t=0.0)
        elif sl["state"] == "air":
            y0 = sl["y"]
            sl["vy"] += GRAVITY * dt
            sl["x"] += sl["vx"] * dt
            sl["y"] += sl["vy"] * dt
            sl["skip_t"] -= dt
            skip = sl["skip"] if sl["skip_t"] > 0 else None
            hit = self._surface_under(sl["x"], y0, sl["y"], skip) if sl["vy"] > 0 else None
            if hit is not None:
                s, rel = hit
                d = (1 if sl["vx"] >= 0 else -1) if abs(sl["vx"]) > 5 else random.choice((1, -1))
                sl.update(state="ride", sid=s.sid, rel=rel, dir=d,
                          speed=max(70.0, abs(sl["vx"]) * 0.7 + 50), y=self._sled_height(s, rel))
                self._splat(sl["x"], s.y - 2, 5)
            elif sl["y"] > self._bottom() + 40:
                self.sled = None
        else:
            s = self.surfaces.get(sl["sid"])
            if s is None or sl["t"] > 3.2:
                self.sled = None
                return
            sl["x"], sl["y"] = s.x + sl["rel"], self._sled_height(s, sl["rel"])

    def _paint_sled(self, p, view):
        sl = self.sled
        if sl is None or not (view.x() - 80 <= sl["x"] <= view.x() + view.width() + 80):
            return
        air = sl["state"] == "air"
        s = None if air else self.surfaces.get(sl["sid"])
        p.save()
        p.setClipRegion(self._allowed_region(view, s), Qt.ClipOperation.IntersectClip)
        d = sl["dir"]
        if sl["state"] == "stop":
            t = sl["t"]
            p.setOpacity(p.opacity() * (1.0 if t < 2.2 else max(0.0, 3.2 - t)))
            p.save()
            p.translate(sl["x"], sl["y"])
            p.scale(d, 1)
            self._paint_sled_body(p)
            p.restore()
            if t < 1.2:                                  # descend, saute de joie
                ex, jump, walking = sl["x"] + d * 14, 3 * abs(math.sin(t * 8)), False
            else:                                        # et s'en va
                ex, jump, walking = sl["x"] + d * (14 + 40 * (t - 1.2)), 0.0, True
            p.translate(ex, sl["y"] - jump)
            self._paint_elf(p, d, t * 9 if walking else 0.0, True)
        else:
            p.translate(sl["x"], sl["y"])
            p.scale(d, 1)
            if air:
                p.rotate(max(-35.0, min(35.0, math.degrees(math.atan2(sl["vy"], abs(sl["vx"]) + 1)))))
            self._paint_sled_body(p)
            self._paint_sled_rider(p, sl["t"], air)
        p.restore()

    @staticmethod
    def _paint_sled_body(p):
        """Origine : point de contact, avant vers +x."""
        p.setBrush(QColor(150, 100, 60))
        p.drawRoundedRect(QRectF(-12, -5.5, 20, 2.6), 1.2, 1.2)
        run = QPen(QColor(205, 50, 60), 1.4)
        run.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(run)
        p.drawLine(QPointF(-7, 0), QPointF(-7, -3))
        p.drawLine(QPointF(5, 0), QPointF(5, -3))
        path = QPainterPath()
        path.moveTo(-11, 0)
        path.lineTo(9, 0)
        path.quadTo(14, 0, 13, -5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)

    @staticmethod
    def _paint_sled_rider(p, t, air):
        skin, dark = QColor(255, 218, 185), QColor(45, 40, 50)
        flutter = math.sin(t * 12) * 1.5
        scarf = QPen(QColor(222, 60, 72), 1.6)
        scarf.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(scarf)
        p.drawLine(QPointF(-4.5, -12), QPointF(-11, -12.5 + flutter))   # écharpe au vent
        arm = QPen(QColor(50, 160, 95), 1.5)
        arm.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(arm)
        if air:                                          # les bras en l'air : wheee !
            p.drawLine(QPointF(-2, -11), QPointF(1, -18))
            p.drawLine(QPointF(-4, -11), QPointF(-6.5, -18))
        else:
            p.drawLine(QPointF(-1.5, -10), QPointF(5, -7))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dark)
        p.drawRoundedRect(QRectF(-2, -7.6, 8, 2.2), 1.1, 1.1)                # jambes devant
        p.setBrush(QColor(50, 160, 95))
        p.drawEllipse(QPointF(-3, -9), 3.6, 4.2)                             # manteau
        p.setBrush(skin)
        p.drawEllipse(QPointF(-2.5, -15), 2.7, 2.6)
        p.setBrush(dark)
        p.drawEllipse(QPointF(-1.3, -15.3), 0.45, 0.55)
        p.setBrush(QColor(255, 140, 160, 170))
        p.drawEllipse(QPointF(-0.6, -14.2), 0.7, 0.45)
        hat = QPainterPath()                                                 # bonnet rabattu par le vent
        hat.moveTo(-5.4, -16)
        hat.quadTo(-4, -20.5, 0.2, -17)
        hat.lineTo(-11, -19.5 + flutter)
        hat.closeSubpath()
        p.fillPath(hat, QColor(222, 60, 72))
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QPointF(-11, -19.5 + flutter), 1.0, 1.0)

    # ---- oiseau postier --------------------------------------------------------
    def notified(self, x, y, w, h):
        """Une notification s'affiche dans (x, y, w, h) : l'oiseau apporte une lettre dessus."""
        if not self.bird_on or self.bird is not None or self.time - self._bird_last < BIRD_GAP:
            return False
        if w <= 0 or h <= 0 or self._screen_at(x + w / 2, y + 1) is None:
            return self.start_bird()
        return self.start_bird((x + w / 2, y))

    def _bird_target(self):
        for s in reversed(self.order):
            if s.ground or s.dock or not any(s.mask):
                continue
            x = s.x + s.w * 0.7
            if self._screen_at(x, s.y + 1) is not None and not self._covered(x, s.y + 1, s):
                return x, s.y
        scr = self.screens[0]
        return scr.x() + scr.width() - 200, scr.y() + scr.height() - 120

    def start_bird(self, target=None):
        if self.bird is not None or not self.screens:
            return False
        tx, ty = target if target is not None else self._bird_target()
        scr = self._screen_at(tx, ty + 1) or self.screens[0]
        right = tx > scr.x() + scr.width() / 2           # arrive par le bord le plus proche
        left_x, right_x = scr.x() - 60, scr.x() + scr.width() + 60
        self.bird = {"t": 0.0, "sx": right_x if right else left_x, "sy": max(scr.y() + 20, ty - 160),
                     "tx": tx, "ty": ty, "ex": left_x if right else right_x, "ey": scr.y() + 30,
                     "x": right_x if right else left_x, "y": ty - 160, "dir": -1 if right else 1}
        self._bird_last = self.time
        return True

    def _step_bird(self, dt):
        b = self.bird
        if b is None:
            return
        b["t"] += dt
        t = b["t"]
        hx, hy = b["tx"], b["ty"] - 26
        if t < BIRD_ARRIVE:
            k = ease(t / BIRD_ARRIVE)
            b["x"] = b["sx"] + (hx - b["sx"]) * k
            b["y"] = b["sy"] + (hy - b["sy"]) * k - 40 * math.sin(math.pi * k)
        elif t < BIRD_DROP:
            b["x"], b["y"] = hx, hy + 2 * math.sin(t * 9)
        else:
            k = ease((t - BIRD_DROP) / 1.8)
            b["dir"] = 1 if b["ex"] > hx else -1
            b["x"] = hx + (b["ex"] - hx) * k
            b["y"] = hy + (b["ey"] - hy) * k - 30 * math.sin(math.pi * k)
        if t > BIRD_END:
            self.bird = None

    def _envelope_pos(self, b):
        """Lettre : dans le bec, puis qui tombe, puis posée ; (x, y, opacité) ou None."""
        t = b["t"]
        if t < BIRD_DROP:
            return b["x"] + b["dir"] * 7, b["y"] + 6, 1.0
        k = min(1.0, (t - BIRD_DROP) / 0.35)
        fade = 1.0 if t < BIRD_END - 0.6 else max(0.0, (BIRD_END - t) / 0.6)
        return b["tx"], b["ty"] - 20 + 17 * k * k, fade

    def _paint_bird(self, p):
        b = self.bird
        if b is None:
            return
        ex, ey, fade = self._envelope_pos(b)
        if fade > 0:
            p.save()
            p.setOpacity(p.opacity() * fade)
            self._paint_envelope(p, ex, ey)
            p.restore()
        if b["t"] > BIRD_END - 0.6 and not -100 < b["x"] - b["ex"] < 100:
            return
        p.save()
        p.translate(b["x"], b["y"])
        p.scale(b["dir"], 1)
        blue, dark = QColor(95, 160, 235), QColor(45, 40, 50)
        tail = QPainterPath()
        tail.moveTo(-6, -1)
        tail.lineTo(-11, -3.5)
        tail.lineTo(-10, 1.2)
        tail.closeSubpath()
        p.fillPath(tail, QColor(70, 125, 205))
        p.setBrush(blue)
        p.drawEllipse(QPointF(0, 0), 7, 5.5)
        p.setBrush(QColor(250, 245, 235))
        p.drawEllipse(QPointF(1.5, 1.8), 4.5, 3.2)
        p.setBrush(blue)
        p.drawEllipse(QPointF(5.5, -3), 3.6, 3.6)
        p.setBrush(dark)
        p.drawEllipse(QPointF(6.9, -3.8), 0.7, 0.75)
        p.setBrush(QColor(255, 140, 165, 180))
        p.drawEllipse(QPointF(7.4, -2.2), 0.9, 0.55)
        beak = QPainterPath()
        beak.moveTo(8.8, -3.3)
        beak.lineTo(12, -2.5)
        beak.lineTo(8.8, -1.7)
        beak.closeSubpath()
        p.fillPath(beak, QColor(255, 165, 60))
        p.setBrush(QColor(40, 60, 120))                            # casquette de facteur
        p.drawRoundedRect(QRectF(3, -7.6, 5.6, 2.2), 1.0, 1.0)
        p.drawRect(QRectF(7.5, -6.2, 2.4, 0.8))
        p.save()
        p.translate(-1, -1)
        p.rotate(math.sin(b["t"] * 22) * 35)                       # battements d'ailes
        p.setBrush(QColor(70, 125, 205))
        p.drawEllipse(QPointF(-1.5, -2.5), 5.5, 3.0)
        p.restore()
        p.restore()

    @staticmethod
    def _paint_envelope(p, x, y):
        p.setBrush(QColor(250, 250, 245))
        p.drawRect(QRectF(x - 4.5, y - 6, 9, 6))
        p.setPen(QPen(QColor(170, 170, 185), 0.6))
        p.drawLine(QPointF(x - 4.5, y - 6), QPointF(x, y - 2.8))
        p.drawLine(QPointF(x + 4.5, y - 6), QPointF(x, y - 2.8))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(230, 70, 90))
        p.drawEllipse(QPointF(x, y - 2.9), 1.1, 1.0)

    # ---- zones à redessiner ---------------------------------------------------
    def _visitor_tiles(self, add, cur):
        sl = self.sled
        if sl is not None:
            reach = 105 if sl["state"] == "stop" else 30     # le lutin repart à pied
            add(cur, sl["x"] - reach, sl["y"] - 34, sl["x"] + reach, sl["y"] + 8)
        b = self.bird
        if b is not None:
            add(cur, b["x"] - 18, b["y"] - 16, b["x"] + 18, b["y"] + 14)
            ex, ey, _ = self._envelope_pos(b)
            add(cur, ex - 7, ey - 9, ex + 7, ey + 3)
