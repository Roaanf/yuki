"""Bataille de boules de neige, souffle du curseur, cadeaux, feux d'artifice, glaçons, chat, saisons."""

import collections
import datetime
import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainterPath, QPen, QTransform

from .shared import COL, GRAVITY, ICE_MAX, MAX_CLUMPS, ease

AUTO, WINTER, SPRING, AUTUMN = 0, 1, 2, 3
SEASON_NAMES = {"auto": AUTO, "hiver": WINTER, "printemps": SPRING, "automne": AUTUMN}

FIGHT_WALK = 2.0
FIGHT_END = 18.0
DANCE_END = 20.5
LEAVE_END = 23.0
BALL_GRAVITY = 620.0
BALL_WINDUP = 0.32

BLOW_RADIUS = 80.0
BLOW_MIN_SPEED = 120.0
SWEEP_MIN_SPEED = 350.0
CURSOR_MAX_SPEED = 2500.0   # au-delà : saut d'un écran à l'autre, pas un geste

MAX_GIFTS = 8
GIFT_LIFE = 5 * 60
GIFT_COLORS = ((226, 64, 78), (70, 150, 230), (60, 175, 110), (240, 190, 60), (235, 110, 170))
MAX_SPARKS = 900
FIREWORK_COLORS = ((255, 90, 110), (255, 205, 90), (120, 200, 255), (150, 240, 160), (240, 140, 255))
ICE_EVERY = 2.0
CAT_SPEED = 42.0
PLAY_RADIUS = 170.0         # le curseur lent à moins de ça devient un jouet
PLAY_SPEED = 400.0
POUNCE_T = 0.45
CAT_COATS = (((240, 165, 90), (190, 110, 55)), ((120, 125, 140), (75, 78, 92)),
             ((250, 240, 225), (205, 180, 150)), ((60, 58, 68), (30, 28, 36)))

Theme = collections.namedtuple("Theme", "shape colors pile shadow clump fall sway size reach")


def _flat(path):
    """Polygone de ~17 points : 2× plus rapide à remplir qu'une courbe aplatie à chaque flocon.
    Agrandi avant d'aplatir, sinon Qt ne garde que 5 points à cette taille."""
    return QTransform.fromScale(1 / 8, 1 / 8).map(QTransform.fromScale(8, 8).map(path).toFillPolygon())


def _petal():
    path = QPainterPath()
    path.moveTo(0, -1)
    path.cubicTo(0.95, -0.7, 0.8, 0.7, 0, 1)
    path.cubicTo(-0.8, 0.7, -0.95, -0.7, 0, -1)
    return _flat(path)


def _leaf():
    path = QPainterPath()
    path.moveTo(0, -1.15)
    path.quadTo(1.05, -0.2, 0, 1.1)
    path.quadTo(-1.05, -0.2, 0, -1.15)
    return _flat(path)


THEMES = {
    WINTER: Theme(None, ((255, 255, 255),), (255, 255, 255, 240), (120, 150, 190, 90),
                  (255, 255, 255), 1.0, 1.0, 1.0, 1.0),
    SPRING: Theme(_petal(), ((255, 183, 205), (255, 208, 224), (250, 160, 190)),
                  (255, 208, 224, 235), (205, 120, 150, 90), (255, 190, 212), 0.62, 1.7, 1.9, 2.4),
    AUTUMN: Theme(_leaf(), ((214, 107, 42), (232, 152, 48), (178, 68, 40), (201, 128, 45)),
                  (206, 126, 58, 235), (105, 58, 30, 90), (214, 124, 50), 0.72, 1.9, 2.3, 2.9),
}


def season_for(day):
    if 3 <= day.month <= 5:
        return SPRING
    if day.month in (9, 10):
        return AUTUMN
    return WINTER                # l'été compris : Yuki, c'est la neige


class FunMixin:
    """Méthodes ajoutées à Sim, sur le modèle de FestiveMixin."""

    def _fun_init(self):
        self.clock = datetime.datetime.now
        self.season_choice = AUTO
        self.season = None
        self.theme = THEMES[WINTER]
        self.xmas = False
        self.calendar_timer = 0.0
        self.fireworks_gap = None
        self.fireworks_timer = 0.0

        self.fight = None
        self.fight_every = 8.0    # minutes (0 = jamais tout seul)
        self.fight_timer = 0.0
        self.balls = []
        self.drips = []           # traces collées aux fenêtres [x, y, age, durée, r]

        self.cursor = None
        self.cursor_prev = None
        self.cursor_v = (0.0, 0.0)
        self.cursor_speed = 0.0
        self.cursor_force = 1.0

        self.gifts = []
        self.falling_gifts = []
        self.gift_timer = 0.0
        self.rockets = []
        self.rocket_queue = []
        self.fw_sparks = []       # [x, y, vx, vy, age, durée, couleur]
        self.ice_timer = 0.0
        self.shards = []          # glaçons et gouttes [x, y, vy, longueur, largeur, surface d'origine]

        self.cat = None
        self.cat_every = 9.0
        self.cat_timer = 0.0
        self.prints = []          # empreintes [sid, rel, hauteur, âge]

    @property
    def winter(self):
        return self.season == WINTER

    # ---- saisons et calendrier ----------------------------------------
    def set_season(self, choice):
        self.season_choice = int(choice)
        self._check_calendar()

    def _check_calendar(self):
        now = self.clock()
        self._sky_calendar(now)
        self.xmas = now.month == 12 and now.day in (24, 25)
        season = self.season_choice or season_for(now)
        if season != self.season:
            self.season = season
            self.theme = THEMES[season]
            self.SNOW = QColor(*self.theme.pile)
            self.SHADOW = QColor(*self.theme.shadow)
            self.SPECK = QColor(*self.theme.colors[2 % len(self.theme.colors)])
            self.full = True
        if now.month == 12 and now.day == 31 and now.hour >= 18:
            self.fireworks_gap = (40.0, 70.0, 3)
        elif now.month == 1 and now.day == 1 and now.hour == 0 and now.minute < 20:
            self.fireworks_gap = (4.0, 7.0, 5)
        else:
            self.fireworks_gap = None

    def _santa_period(self):
        return self.santa_every * 60 / (4 if self.xmas else 1)

    def _step_fun(self, dt):
        self.calendar_timer -= dt
        if self.calendar_timer <= 0:
            self.calendar_timer = 5.0
            self._check_calendar()
        self._step_cursor(dt)
        self._step_fight(dt)
        self._step_balls(dt)
        self._step_gifts(dt)
        self._step_fireworks(dt)
        self._step_ice(dt)
        self._step_cat(dt)

    # ---- outils ---------------------------------------------------------
    def _screen_at(self, x, y):
        for scr in self.screens:
            if scr.x() <= x < scr.x() + scr.width() and scr.y() <= y < scr.y() + scr.height():
                return scr
        return None

    def _bottom(self):
        return max(s.y() + s.height() for s in self.screens) + 10

    def _surface_under(self, x, y0, y1, skip=None):
        """Comme _land, mais sans rien déposer : (surface, position relative) ou None."""
        for s in reversed(self.order):
            if s.sid == skip or not s.x <= x < s.x + s.w:
                continue
            c = int((x - s.x) / COL)
            if c < len(s.heights) and s.mask[c] and y0 - 1 <= s.y + s.base[c] - s.heights[c] <= y1 + 1:
                return s, x - s.x
        return None

    def _splat(self, x, y, k=7):
        for _ in range(min(k, MAX_CLUMPS - len(self.clumps))):
            self.clumps.append([x, y, random.uniform(-85, 85), random.uniform(-160, -30),
                                random.uniform(1.0, 1.8), 0.15, None])

    def _sparkle(self, x, y, color, k=10):
        for _ in range(k):
            a = random.uniform(0, math.tau)
            v = random.uniform(15, 65)
            self.sparkles.append([x, y, math.cos(a) * v, math.sin(a) * v, 0.0,
                                  random.uniform(0.5, 1.1), color])

    # ---- curseur --------------------------------------------------------
    def set_cursor(self, x, y):
        self.cursor = (x, y)

    def _step_cursor(self, dt):
        if self.cursor is None or dt <= 0:
            return
        x, y = self.cursor
        px, py = self.cursor_prev or self.cursor
        self.cursor_prev = self.cursor
        k = min(1.0, dt * 12)     # lissé : KWin envoie moins de positions qu'il n'y a d'images
        vx = self.cursor_v[0] + ((x - px) / dt - self.cursor_v[0]) * k
        vy = self.cursor_v[1] + ((y - py) / dt - self.cursor_v[1]) * k
        speed = math.hypot(vx, vy)
        if speed > CURSOR_MAX_SPEED:
            vx, vy, speed = vx * CURSOR_MAX_SPEED / speed, vy * CURSOR_MAX_SPEED / speed, CURSOR_MAX_SPEED
        self.cursor_v, self.cursor_speed = (vx, vy), speed
        if self.cursor_force <= 0 or speed < BLOW_MIN_SPEED:
            return
        self._blow_flakes(x, y, vx, vy, speed, dt)
        if speed >= SWEEP_MIN_SPEED:
            self._sweep_pile(x, y, vx, speed)

    def _blow_flakes(self, x, y, vx, vy, speed, dt):
        ux, uy = vx / speed, vy / speed
        strength = 0.5 * self.cursor_force * min(speed, 1800.0)
        for f in self.flakes:
            dx, dy = f[0] - x, f[1] - y
            if abs(dx) > BLOW_RADIUS or abs(dy) > BLOW_RADIUS:
                continue
            d = math.hypot(dx, dy)
            if d >= BLOW_RADIUS:
                continue
            push = strength * (1 - d / BLOW_RADIUS) * dt
            nx, ny = (dx / d, dy / d) if d > 0.01 else (ux, uy)
            f[0] += (nx * 0.6 + ux * 0.4) * push
            f[1] += (ny * 0.6 + uy * 0.4) * push

    def _sweep_pile(self, x, y, vx, speed):
        for s in reversed(self.order):
            if not s.x <= x < s.x + s.w:
                continue
            c = int((x - s.x) / COL)
            top = s.y + s.base[c] if c < len(s.heights) else s.y
            if c >= len(s.heights) or not s.mask[c] or not top - s.heights[c] - 8 <= y <= top + 3:
                continue
            share = min(0.75, self.cursor_force * speed / 2500)
            for i in range(max(0, c - 2), min(len(s.heights), c + 3)):
                take = s.heights[i] * share
                if take < 0.15 or not s.mask[i] or len(self.clumps) >= MAX_CLUMPS:
                    continue
                s.heights[i] -= take
                self.clumps.append([s.x + (i + random.random()) * COL, s.y + s.base[i] - s.heights[i] - 1,
                                    vx * 0.3 + random.uniform(-35, 35),
                                    -speed * 0.1 - random.uniform(40, 120),
                                    min(2.6, 0.9 + math.sqrt(take) * 0.7), take, s.sid])
            self._add_tiles(self.tiles, s.x + (c - 4) * COL, s.y - s.maxh - 3,
                            s.x + (c + 5) * COL, s.y + 4)
            return

    # ---- bataille de boules de neige ------------------------------------
    def _fight_spots(self, force):
        need = 0.0 if force else 1.5
        spots = []
        for s in self.order:
            if s.w < 90:
                continue
            for _ in range(6):
                rel = random.uniform(30, s.w - 30)
                c = int(rel // COL)
                lo, hi = max(0, c - 4), min(len(s.heights), c + 5)
                if not all(s.mask[lo:hi]) or sum(s.heights[lo:hi]) / max(1, hi - lo) < need:
                    continue
                x = s.x + rel
                scr = self._screen_at(x, s.y - 30)
                if scr is None or self._covered(x, s.y - 20, s) or self._covered(x, s.y - 2, s):
                    continue
                spots.append((s, rel, x, scr))
        return spots

    def start_fight(self, force=False):
        if self.fight is not None or not self.screens:
            return False
        spots = self._fight_spots(force)
        pairs = [(a, b) for i, a in enumerate(spots) for b in spots[i + 1:]
                 if a[3] == b[3] and 160 <= abs(a[2] - b[2]) <= 1000]
        self.fight_timer = 0.0
        if not pairs:
            return False
        a, b = random.choice(pairs)
        d = 1 if b[2] > a[2] else -1
        sides = [{"sid": a[0].sid, "rel": a[1], "dir": d, "hits": 0},
                 {"sid": b[0].sid, "rel": b[1], "dir": -d, "hits": 0}]
        elves = [{"side": i, "off": off, "cool": random.uniform(0.2, 1.6), "wind": 0.0, "hit": 0.0}
                 for i in (0, 1) for off in (-13.0, 11.0)]
        self.fight = {"age": 0.0, "sides": sides, "elves": elves, "cheered": False}
        return True

    def _elf_pos(self, fi, e):
        side = fi["sides"][e["side"]]
        s = self.surfaces[side["sid"]]
        off = e["off"]
        if fi["age"] < FIGHT_WALK:
            off -= side["dir"] * 52 * (1 - ease(fi["age"] / FIGHT_WALK))
        elif fi["age"] > DANCE_END:
            off -= side["dir"] * 60 * ease((fi["age"] - DANCE_END) / (LEAVE_END - DANCE_END))
        return s.x + side["rel"] + off, s.y - self._pile_at(s, side["rel"] + off) * 0.6

    def _throw(self, fi, e):
        side = fi["sides"][e["side"]]
        x, y = self._elf_pos(fi, e)
        x += side["dir"] * 3
        y -= 15
        foes = [o for o in fi["elves"] if o["side"] != e["side"]]
        tx, ty = self._elf_pos(fi, random.choice(foes))
        ty -= 7
        heads = self._snowman_heads()
        if heads and random.random() < 0.2:
            tx, ty = random.choice(heads)[:2]
        tx += random.gauss(0, 7)
        ty += random.gauss(0, 5)
        t = max(0.5, min(1.5, math.hypot(tx - x, ty - y) / 380))
        self.balls.append({"x": x, "y": y, "vx": (tx - x) / t,
                           "vy": (ty - y - 0.5 * BALL_GRAVITY * t * t) / t, "t": 0.0, "end": t,
                           "foe": 1 - e["side"], "stick": random.random() < 0.35})

    def _snowman_heads(self):
        heads = []
        for m in self.snowmen:
            s = self.surfaces.get(m["sid"])
            if s is not None and m["age"] > 11.5 and m["melt"] is None and not m.get("headless"):
                heads.append((s.x + m["rel"], s.y - self._pile_at(s, m["rel"]) * 0.6 - 33.3, m))
        return heads

    def _step_fight(self, dt):
        if self.fight is None:
            if self.fight_every > 0 and self.winter and not self.balls:
                self.fight_timer += dt
                if self.fight_timer >= self.fight_every * 60 and not self.start_fight():
                    self.fight_timer = self.fight_every * 60 * 0.8   # réessaie un peu plus tard
            return
        fi = self.fight
        fi["age"] += dt
        if fi["age"] > LEAVE_END or any(side["sid"] not in self.surfaces for side in fi["sides"]):
            self.fight = None
            return
        fighting = FIGHT_WALK < fi["age"] < FIGHT_END
        for e in fi["elves"]:
            if e["hit"] > 0:
                e["hit"] = max(0.0, e["hit"] - dt)
            elif e["wind"] > 0:
                e["wind"] -= dt
                if e["wind"] <= 0:
                    e["wind"] = 0.0
                    e["cool"] = random.uniform(1.0, 2.4)
                    if fighting:
                        self._throw(fi, e)
            elif fighting:
                e["cool"] -= dt
                if e["cool"] <= 0:
                    e["wind"] = BALL_WINDUP
        if fi["age"] >= FIGHT_END and not fi["cheered"]:
            fi["cheered"] = True
            for i in self._fight_winners(fi):
                side = fi["sides"][i]
                s = self.surfaces[side["sid"]]
                self._sparkle(s.x + side["rel"], s.y - 22, (255, 225, 120), 14)

    @staticmethod
    def _fight_winners(fi):
        a, b = fi["sides"][0]["hits"], fi["sides"][1]["hits"]
        return [i for i, won in enumerate((a >= b, b >= a)) if won]

    def _ball_hit(self, b):
        x, y = b["x"], b["y"]
        fi = self.fight
        if fi is not None:
            for e in fi["elves"]:
                if e["side"] != b["foe"] or e["hit"] > 0:
                    continue
                ex, ey = self._elf_pos(fi, e)
                if abs(x - ex) < 6 and ey - 15 < y < ey + 1:
                    e["hit"] = 1.1
                    e["wind"] = 0.0
                    fi["sides"][1 - b["foe"]]["hits"] += 1
                    return True
        for hx, hy, m in self._snowman_heads():
            if (x - hx) ** 2 + (y - hy) ** 2 < 36:
                m["headless"] = True
                self.clumps.append([hx, hy, b["vx"] * 0.25, -120.0, 3.6, 1.5, None])
                return True
        return False

    def _step_balls(self, dt):
        if not self.balls and not self.drips:
            return
        bottom = self._bottom()
        keep = []
        for b in self.balls:
            y0 = b["y"]
            b["vy"] += BALL_GRAVITY * dt
            b["x"] += b["vx"] * dt
            b["y"] += b["vy"] * dt
            b["t"] += dt
            x, y = b["x"], b["y"]
            if self._ball_hit(b):
                self._splat(x, y)
            elif b["stick"] and b["t"] >= b["end"] and self._on_window(x, y):
                self.drips.append([x, y, 0.0, random.uniform(4.0, 7.0), random.uniform(3.0, 4.6)])
                self._splat(x, y, 3)
            elif b["vy"] > 0 and self._land(x, y0, y, 0.6):
                self._splat(x, y, 5)
            elif y < bottom:
                keep.append(b)
        self.balls = keep
        for d in self.drips:
            d[2] += dt
            d[1] += 18 * (1 - d[2] / d[3]) * dt
        self.drips = [d for d in self.drips if d[2] < d[3]]

    def _on_window(self, x, y):
        return any(r[0] <= x < r[2] and r[1] <= y < r[3] for r in self.windows_rects)

    # ---- cadeaux ----------------------------------------------------------
    def _step_gifts(self, dt):
        sa = self.santa
        if sa is not None and self.winter:
            scr = sa["screen"]
            self.gift_timer -= dt
            if self.gift_timer <= 0 and scr.x() < sa["x"] < scr.x() + scr.width():
                lo, hi = (0.5, 1.2) if self.xmas else (1.4, 3.2)
                self.gift_timer = random.uniform(lo, hi)
                self.falling_gifts.append({"x": sa["x"] - sa["dir"] * 14, "y": sa["y"] - 8,
                                           "vx": sa["dir"] * sa["speed"] * 0.18, "vy": 0.0,
                                           "c": random.randrange(len(GIFT_COLORS)), "skip": None})
        bottom = self._bottom()
        keep = []
        for g in self.falling_gifts:
            y0 = g["y"]
            g["vy"] = min(125.0, g["vy"] + 190 * dt)      # parachute : chute limitée
            g["vx"] *= 1 - min(1.0, 0.7 * dt)
            g["x"] += g["vx"] * dt
            g["y"] += g["vy"] * dt
            hit = self._surface_under(g["x"], y0, g["y"], g["skip"]) if g["vy"] > 0 else None
            if hit is not None:
                self._rest_gift(g, *hit)
            elif g["y"] < bottom:
                keep.append(g)
        self.falling_gifts = keep

        keep = []
        for g in self.gifts:
            s = self.surfaces.get(g["sid"])
            if s is None:
                if g["sid"] in self.dormant:
                    keep.append(g)
                continue
            g["age"] += dt
            plowed = any(pl["sid"] == s.sid and abs(pl["rel"] - g["rel"]) < 10 for pl in self.plows)
            if plowed or g["age"] > GIFT_LIFE:
                self._pop_gift(g, s)
            else:
                keep.append(g)
        self.gifts = keep

    def _rest_gift(self, g, s, rel):
        if len(self.gifts) >= MAX_GIFTS:
            old = self.gifts.pop(0)
            target = self.surfaces.get(old["sid"])
            if target is not None:
                self._pop_gift(old, target)
        self.gifts.append({"sid": s.sid, "rel": rel, "lift": self._pile_at(s, rel) * 0.7,
                           "age": 0.0, "c": g["c"]})
        self._gift_tiles(s.x + rel, s.y)

    def _gift_tiles(self, x, y):
        self._add_tiles(self.tiles, x - 10, y - 34, x + 10, y + 3)

    def _pop_gift(self, g, s):
        x, y = s.x + g["rel"], s.y - g["lift"]
        self._sparkle(x, y - 5, GIFT_COLORS[g["c"]])
        self._gift_tiles(s.x + g["rel"], s.y)

    # ---- feux d'artifice ---------------------------------------------------
    def start_fireworks(self, salvo=5):
        if not self.screens:
            return False
        for _ in range(salvo):
            self.rocket_queue.append([random.uniform(0, 0.55 * salvo), random.choice(self.screens)])
        return True

    def _step_fireworks(self, dt):
        if self.fireworks_gap is not None:
            self.fireworks_timer -= dt
            if self.fireworks_timer <= 0:
                lo, hi, salvo = self.fireworks_gap
                self.fireworks_timer = random.uniform(lo, hi)
                self.start_fireworks(salvo)
        if self.rocket_queue:
            waiting = []
            for item in self.rocket_queue:
                item[0] -= dt
                if item[0] > 0:
                    waiting.append(item)
                else:
                    self._launch_rocket(item[1])
            self.rocket_queue = waiting
        keep = []
        for r in self.rockets:
            r["x"] += r["vx"] * dt
            r["y"] += r["vy"] * dt
            r["vy"] += 120 * dt
            r["fuse"] -= dt
            if len(self.fw_sparks) < MAX_SPARKS:
                self.fw_sparks.append([r["x"], r["y"], random.uniform(-8, 8), random.uniform(10, 30),
                                       0.0, 0.35, (255, 225, 170)])
            if r["fuse"] <= 0:
                self._burst_rocket(r)
            else:
                keep.append(r)
        self.rockets = keep
        for sp in self.fw_sparks:
            sp[4] += dt
            sp[2] *= 1 - min(1.0, 0.9 * dt)
            sp[3] += 65 * dt
            sp[0] += sp[2] * dt
            sp[1] += sp[3] * dt
        if self.fw_sparks:
            self.fw_sparks = [sp for sp in self.fw_sparks if sp[4] < sp[5]]

    def _launch_rocket(self, scr):
        h = scr.height()
        fuse = random.uniform(0.85, 1.25)
        target = scr.y() + random.uniform(0.14, 0.42) * h
        y = scr.y() + h + 4
        self.rockets.append({"x": scr.x() + random.uniform(0.12, 0.88) * scr.width(), "y": y,
                             "vx": random.uniform(-25, 25),
                             "vy": (target - y) / fuse - 60 * fuse, "fuse": fuse,
                             "c": random.choice(FIREWORK_COLORS)})

    def _burst_rocket(self, r):
        n = min(46, MAX_SPARKS - len(self.fw_sparks))
        for i in range(n):
            a = math.tau * i / max(1, n) + random.uniform(-0.05, 0.05)
            v = random.uniform(55, 175)
            self.fw_sparks.append([r["x"], r["y"], math.cos(a) * v, math.sin(a) * v, 0.0,
                                   random.uniform(0.9, 1.7), r["c"]])

    # ---- glaçons ------------------------------------------------------------
    def _step_ice(self, dt):
        self._step_shards(dt)
        self.ice_timer += dt
        if self.ice_timer < ICE_EVERY:
            return
        self.ice_timer = 0.0
        rate = max(0.3, self.melt / 0.003)
        for s in self.order:
            if s.ground or not (self.winter or s.ice):
                continue
            before = [i[1] for i in s.ice]
            if self.winter:
                self._grow_ice(s, rate)
            else:
                for i in s.ice:
                    i[1] -= 0.6
                s.ice = [i for i in s.ice if i[1] > 0.4]
            if [i[1] for i in s.ice] != before:
                self._touch(s)

    def _grow_ice(self, s, rate):
        n = len(s.heights)
        if len(s.ice) < min(14, int(s.w / 70)) and random.random() < 0.25 * rate:
            c = random.randrange(n)
            rel = (c + 0.5) * COL
            if s.mask[c] and not s.base[c] and s.heights[c] > 2.5 and all(abs(i[0] - rel) > 12 for i in s.ice):
                s.ice.append([rel, 0.8, random.uniform(5.0, ICE_MAX), random.uniform(1.6, 3.0)])
        for i in s.ice:
            c = min(n - 1, int(i[0] // COL))
            if i[1] < i[2] and s.heights[c] > 0.6:
                i[1] = min(i[2], i[1] + 0.22 * rate)
            if i[1] > 4 and s.mask[c] and random.random() < 0.02:
                self.shards.append([s.x + i[0], s.y + i[1], 30.0, 2.0, 0.8, s.sid])

    def _drop_ice(self, s):
        for rel, length, _, width in s.ice:
            self.shards.append([s.x + rel, s.y, 40.0, length, width, s.sid])
        s.ice = []
        s.ice_path = None

    def _step_shards(self, dt):
        if not self.shards:
            return
        bottom = self._bottom()
        keep = []
        for sh in self.shards:
            y0 = sh[1] + sh[3]
            sh[2] += GRAVITY * 0.7 * dt
            sh[1] += sh[2] * dt
            tip = sh[1] + sh[3]
            if self._surface_under(sh[0], y0, tip, sh[5]) is not None:
                if sh[3] > 4:
                    self._splat(sh[0], tip, 3)
            elif sh[1] < bottom:
                keep.append(sh)
        self.shards = keep

    def _fun_shed(self, s, frac):
        """La neige de s décolle : glaçons et cadeaux ne tiennent plus non plus."""
        if frac >= 0.2 and s.ice:
            self._drop_ice(s)
        if frac < 0.5:
            return
        for g in [g for g in self.gifts if g["sid"] == s.sid]:
            self.gifts.remove(g)
            self._gift_tiles(s.x + g["rel"], s.y)
            self.falling_gifts.append({"x": s.x + g["rel"], "y": s.y - g["lift"] - 3,
                                       "vx": random.uniform(-30, 30), "vy": -50.0, "c": g["c"],
                                       "skip": s.sid})

    # ---- Neko-chan ---------------------------------------------------------
    @staticmethod
    def _open_run(s):
        """Plus longue suite de colonnes visibles, en px depuis le bord de s."""
        best, i, n, m = (0, 0), 0, len(s.mask), s.mask
        while i < n:
            if not m[i]:
                i += 1
                continue
            j = i
            while j < n and m[j]:
                j += 1
            if j - i > best[1] - best[0]:
                best = (i, j)
            i = j
        return best[0] * COL, best[1] * COL

    def start_cat(self, asleep=False):
        """asleep : déjà endormi sur le tas le plus épais (retour d'absence)."""
        if self.cat is not None:
            return False
        self.cat_timer = 0.0
        cands = []
        for s in self.order:
            lo, hi = self._open_run(s)
            if hi - lo < 130:
                continue
            spots = []
            for _ in range(14):
                rel = random.uniform(lo + 40, hi - 40)
                x = s.x + rel
                if self._screen_at(x, s.y - 14) is not None and not self._covered(x, s.y - 10, s):
                    spots.append((self._pile_at(s, rel), rel))
            if spots:
                cands += [(s, lo, hi, max(spots)[1])] * (2 if s.dock or s.ground else 1)
        if not cands:
            return False
        s, lo, hi, target = random.choice(cands)
        d = random.choice((1, -1))
        fur, dark = random.choice(CAT_COATS)
        rel = lo - 24 if d > 0 else hi + 24
        self.cat = {"sid": s.sid, "rel": rel, "target": target, "dir": d, "state": "walk",
                    "leaving": False, "t": 0.0, "fur": fur, "dark": dark, "sleep": 0.0,
                    "lo": lo, "hi": hi, "sx": s.x, "sy": s.y, "step": 0.0, "side": 0,
                    "x": s.x + rel, "y": s.y, "vx": 0.0, "vy": 0.0, "from": None}
        if asleep:
            self.cat.update(rel=target, state="sleep", sleep=random.uniform(90, 200), sx=s.x, sy=s.y,
                            x=s.x + target, y=s.y - self._pile_at(s, target) * 0.6)
        return True

    def _cat_leave(self, c, s):
        lo, hi = self._open_run(s)
        c["lo"], c["hi"] = lo, hi
        c["leaving"] = True
        c["state"] = "walk"
        c["dir"] = 1 if hi - c["rel"] < c["rel"] - lo else -1
        c["target"] = hi + 24 if c["dir"] > 0 else lo - 24

    def _cat_jump(self, c):
        c["state"] = "jump"
        c["from"] = c["sid"]
        c["sid"] = None
        c["vx"] = random.choice((1, -1)) * random.uniform(50, 130)
        c["vy"] = -300.0
        c["dir"] = 1 if c["vx"] > 0 else -1

    def _cat_startled(self, c, s):
        if abs(s.x - c["sx"]) + abs(s.y - c["sy"]) > 3:
            return True
        if any(pl["sid"] == s.sid and abs(pl["rel"] - c["rel"]) < 70 for pl in self.plows):
            return True
        if self.cursor is not None and self.cursor_speed > 600:
            return math.hypot(self.cursor[0] - c["x"], self.cursor[1] - c["y"] + 8) < 30
        return False

    def _step_cat(self, dt):
        self._step_prints(dt)
        c = self.cat
        if c is None:
            if self.cat_every > 0:
                self.cat_timer += dt
                if self.cat_timer >= self.cat_every * 60 and not self.start_cat():
                    self.cat_timer = self.cat_every * 60 * 0.8
            return
        c["t"] += dt
        if c["state"] == "jump":
            self._step_cat_jump(c, dt)
            return
        s = self.surfaces.get(c["sid"])
        if s is None:
            self._cat_jump(c)
            return
        if c["state"] == "sleep":
            c["x"], c["y"] = s.x + c["rel"], s.y - self._pile_at(s, c["rel"]) * 0.6
            c["sleep"] -= dt
            c["tease"] = c.get("tease", 0.0) + dt if self._cat_sees_cursor(c, s, 50) else 0.0
            if self._cat_startled(c, s):
                self._cat_jump(c)
            elif c["tease"] > 1.5:             # on l'a taquiné assez longtemps : il se réveille
                self._cat_play(c, s)
            elif c["sleep"] <= 0:
                self._cat_leave(c, s)
            return
        if c["state"] == "play":
            if self._cat_startled(c, s):
                self._cat_jump(c)
            else:
                self._step_cat_play(c, s, dt)
            return
        if not c["leaving"] and self._cat_sees_cursor(c, s):
            self._cat_play(c, s)
            return
        step = CAT_SPEED * (1.5 if c["leaving"] else 1.0) * dt
        c["rel"] += c["dir"] * step
        c["x"], c["y"] = s.x + c["rel"], s.y - self._pile_at(s, c["rel"]) * 0.6
        self._cat_prints(c, s, step)
        if c["dir"] * (c["rel"] - c["target"]) >= 0:
            if c["leaving"]:
                self.cat = None
            else:
                c["state"] = "sleep"
                c["sleep"] = random.uniform(150, 360)
                c["sx"], c["sy"] = s.x, s.y

    def _cat_prints(self, c, s, step):
        c["step"] += step
        if c["step"] >= 13:
            c["step"] = 0.0
            c["side"] = 1 - c["side"]
            self._add_print(s, c["rel"], c["side"])

    def _cat_sees_cursor(self, c, s, reach=PLAY_RADIUS):
        if self.cursor is None or self.cursor_speed >= PLAY_SPEED:
            return False
        x, y = self.cursor
        return (abs(x - c["x"]) < reach and s.y - 90 <= y <= s.y + 12
                and c["lo"] - 10 <= x - s.x <= c["hi"] + 10)

    def _cat_play(self, c, s):
        c.update(state="play", play=0.0, bored=0.0, hop=None, cool=0.0, crouch=0.0, lift=0.0,
                 moving=False, sx=s.x, sy=s.y, tease=0.0)

    def _step_cat_play(self, c, s, dt):
        """Suit le curseur, se tapit, puis bondit dessus."""
        c["play"] += dt
        sees = self._cat_sees_cursor(c, s, PLAY_RADIUS + 60)
        c["bored"] = 0.0 if sees else c["bored"] + dt
        c["moving"] = False
        if c["hop"] is None and (c["bored"] > 2.5 or c["play"] > 45):
            c["crouch"] = 0.0
            self._cat_leave(c, s)
            return
        lo, hi = c["lo"] + 4, c["hi"] - 4
        hop = c["hop"]
        if hop is not None:
            hop["t"] += dt
            k = min(1.0, hop["t"] / POUNCE_T)
            c["rel"] = hop["from"] + (hop["to"] - hop["from"]) * k
            c["lift"] = 16 * math.sin(math.pi * k)
            if k >= 1:
                c["hop"], c["lift"], c["cool"] = None, 0.0, 1.0
                if self._pile_at(s, c["rel"]) > 1:
                    self._splat(s.x + c["rel"], s.y - 3, 3)
        elif c["cool"] > 0:
            c["cool"] -= dt
        elif sees:
            target = max(lo, min(hi, self.cursor[0] - s.x))
            dx = target - c["rel"]
            if abs(dx) > 18:
                c["crouch"] = 0.0
                c["dir"] = 1 if dx > 0 else -1
                step = min(abs(dx) - 17, CAT_SPEED * 2.2 * dt)
                c["rel"] += c["dir"] * step
                c["moving"] = True
                self._cat_prints(c, s, step)
            elif self.cursor_speed < 80:
                c["crouch"] += dt
                if c["crouch"] > 0.45:
                    c["crouch"] = 0.0
                    c["dir"] = 1 if dx >= 0 else -1
                    c["hop"] = {"t": 0.0, "from": c["rel"], "to": target}
        c["x"], c["y"] = s.x + c["rel"], s.y - self._pile_at(s, c["rel"]) * 0.6

    def _step_cat_jump(self, c, dt):
        y0 = c["y"]
        c["vy"] += GRAVITY * dt
        c["x"] += c["vx"] * dt
        c["y"] += c["vy"] * dt
        hit = self._surface_under(c["x"], y0, c["y"], c["from"]) if c["vy"] > 0 else None
        if hit is not None:
            s, rel = hit
            c["sid"], c["rel"], c["y"] = s.sid, rel, s.y
            self._splat(c["x"], c["y"] - 2, 4)
            self._cat_leave(c, s)
        elif c["y"] > self._bottom() + 40:
            self.cat = None

    def _add_print(self, s, rel, side):
        c = int(rel // COL)
        if not 0 <= c < len(s.heights) or s.heights[c] < 1.5 or not s.mask[c]:
            return
        lift = s.heights[c] * 0.6
        self.prints.append([s.sid, rel, lift, 0.0, side])
        self._print_tiles(s, rel)
        if len(self.prints) > 40:
            self._drop_print(self.prints.pop(0))

    def _print_tiles(self, s, rel):
        self._add_tiles(self.tiles, s.x + rel - 5, s.y - 14, s.x + rel + 5, s.y + 2)

    def _drop_print(self, pr):
        s = self.surfaces.get(pr[0])
        if s is not None:
            self._print_tiles(s, pr[1])

    def _step_prints(self, dt):
        if not self.prints:
            return
        keep = []
        for pr in self.prints:
            pr[3] += dt
            if pr[3] < 25:
                keep.append(pr)
            else:
                self._drop_print(pr)
        self.prints = keep

    # ---- zones à redessiner ----------------------------------------------
    def _fun_tiles(self, add, cur):
        fi = self.fight
        if fi is not None:
            for side in fi["sides"]:
                s = self.surfaces.get(side["sid"])
                if s is not None:
                    x = s.x + side["rel"]
                    add(cur, x - 90, s.y - 42, x + 90, s.y + 6)
        for b in self.balls:
            add(cur, b["x"] - 5, b["y"] - 5, b["x"] + 5, b["y"] + 5)
        for d in self.drips:
            add(cur, d[0] - d[4] - 3, d[1] - 24, d[0] + d[4] + 3, d[1] + d[4] + 3)
        for g in self.falling_gifts:
            add(cur, g["x"] - 12, g["y"] - 24, g["x"] + 12, g["y"] + 8)
        for r in self.rockets:
            add(cur, r["x"] - 3, r["y"] - 4, r["x"] + 3, r["y"] + 8)
        for sp in self.fw_sparks:
            add(cur, sp[0] - 3, sp[1] - 3, sp[0] + 3, sp[1] + 3)
        for sh in self.shards:
            add(cur, sh[0] - sh[4] - 2, sh[1] - 2, sh[0] + sh[4] + 2, sh[1] + sh[3] + 2)
        c = self.cat
        if c is not None:
            add(cur, c["x"] - 42, c["y"] - 50, c["x"] + 42, c["y"] + 6)

    # ---- dessin ---------------------------------------------------------
    def _paint_shaped_flakes(self, p, bounds, factor):
        vl, vt, vr, vb = bounds
        theme = self.theme
        base = p.transform()
        t = self.time
        color = QColor()
        p.setPen(Qt.PenStyle.NoPen)
        for f in self.flakes:
            if not (vl - 8 <= f[0] <= vr + 8 and vt - 8 <= f[1] <= vb + 8):
                continue
            a = int(min(255, f[6] + 50) * factor(f[0], f[1]))
            if a <= 3:
                continue
            r, g, b = theme.colors[int(f[4] * 100) % len(theme.colors)]
            color.setRgb(r, g, b, a)
            size = f[2] * theme.size
            spin = t * (0.7 + f[2] * 0.2) + f[4]
            tr = QTransform(base)
            tr.translate(f[0], f[1])
            tr.rotateRadians(spin)
            tr.scale(size, size * (0.45 + 0.55 * abs(math.cos(spin * 1.4))))
            p.setTransform(tr)
            p.setBrush(color)
            p.drawPolygon(theme.shape)
        p.setTransform(base)

    def _paint_speck(self, p, s, a, b):
        speck = QPainterPath()
        for i in range(a + a % 2, b, 2):
            h = s.heights[i]
            if h >= 1.5 and s.mask[i]:     # placement fixe : le tas ne scintille pas
                x = s.x + (i + 0.5) * COL + ((i * 13) % 5 - 2) * 0.6
                y = s.y + s.base[i] - 0.8 - (h - 1.6) * ((i * 37) % 100) / 100
                speck.addEllipse(QPointF(x, y), 2.0, 1.1)
        p.fillPath(speck, self.SPECK)

    def _paint_ice(self, p, s):
        if s.ice_path is None:
            body, shine = QPainterPath(), QPainterPath()
            for rel, length, _, w in s.ice:
                c = int(rel // COL)
                x = s.x + rel
                if c >= len(s.mask) or not s.mask[c] or self._covered(x, s.y + length - 1, s):
                    continue
                body.moveTo(x - w, s.y)
                body.quadTo(x - w * 0.3, s.y + length * 0.55, x, s.y + length)
                body.quadTo(x + w * 0.3, s.y + length * 0.55, x + w, s.y)
                body.closeSubpath()
                shine.moveTo(x - w * 0.45, s.y)
                shine.lineTo(x - w * 0.05, s.y + length * 0.7)
                shine.lineTo(x + w * 0.05, s.y)
                shine.closeSubpath()
            s.ice_path = (body, shine)
        body, shine = s.ice_path
        p.fillPath(body, QColor(190, 228, 255, 210))
        p.fillPath(shine, QColor(255, 255, 255, 210))

    def _paint_fun_ground(self, p, view):
        self._paint_prints(p)
        self._paint_gifts(p)
        self._paint_fight(p, view)
        self._paint_cat_layer(p, view)

    def _paint_prints(self, p):
        for sid, rel, lift, _, side in self.prints:
            s = self.surfaces.get(sid)
            if s is None:
                continue
            p.setBrush(self.SHADOW)
            x, y = s.x + rel, s.y - lift + (0.8 if side else -0.8)
            p.drawEllipse(QPointF(x, y), 1.5, 1.0)
            for dx in (-1.5, 0.0, 1.5):
                p.drawEllipse(QPointF(x + dx + 1.8, y - 0.9), 0.45, 0.4)

    def _paint_gifts(self, p):
        for g in self.gifts:
            s = self.surfaces.get(g["sid"])
            if s is None:
                continue
            x, y = s.x + g["rel"], s.y - g["lift"]
            if not self._covered(x, y - 4, s):
                self._paint_gift(p, x, y, g["c"])

    @staticmethod
    def _paint_gift(p, x, y, c):
        box = QColor(*GIFT_COLORS[c])
        ribbon = QColor(255, 245, 210)
        p.setBrush(box)
        p.drawRoundedRect(QRectF(x - 5, y - 9, 10, 9), 1.2, 1.2)
        p.setBrush(ribbon)
        p.drawRect(QRectF(x - 1, y - 9, 2, 9))
        p.drawEllipse(QPointF(x - 2.3, y - 10.4), 2.2, 1.4)
        p.drawEllipse(QPointF(x + 2.3, y - 10.4), 2.2, 1.4)

    def _paint_fight(self, p, view):
        fi = self.fight
        if fi is None:
            return
        age = fi["age"]
        winners = self._fight_winners(fi) if age >= FIGHT_END else []
        for i, side in enumerate(fi["sides"]):
            s = self.surfaces.get(side["sid"])
            if s is None:
                continue
            x = s.x + side["rel"]
            if x < view.x() - 100 or x > view.x() + view.width() + 100:
                continue
            p.save()
            p.setClipRegion(self._allowed_region(view, s), Qt.ClipOperation.IntersectClip)
            for e in fi["elves"]:
                if e["side"] == i:
                    self._paint_fighter(p, fi, e, side, i in winners, age)
            p.restore()

    def _paint_fighter(self, p, fi, e, side, won, age):
        x, y = self._elf_pos(fi, e)
        d = side["dir"]
        walking = age < FIGHT_WALK or age > DANCE_END
        face = -d if age > DANCE_END else d
        jump = 4 * abs(math.sin(age * 7 + e["off"])) if won and FIGHT_END <= age <= DANCE_END else 0.0
        p.save()
        if age > LEAVE_END - 1:
            p.setOpacity(p.opacity() * max(0.0, LEAVE_END - age))
        p.translate(x, y - jump)
        if e["wind"] > 0:
            k = 1 - e["wind"] / BALL_WINDUP
            p.setBrush(QColor(250, 252, 255))
            p.drawEllipse(QPointF(-d * (2 + 3 * k), -17 - 2 * k), 2.4, 2.4)
        if e["hit"] > 0:
            p.setBrush(QColor(255, 225, 110))
            for k in range(3):
                a = age * 6 + k * 2.09
                p.drawEllipse(QPointF(math.cos(a) * 7, -21 + math.sin(a) * 2), 0.9, 0.9)
            p.rotate(-d * min(1.0, e["hit"] * 2) * 36)
        self._paint_elf(p, face, age * 9 if walking else 0.0, e["side"] == 0)
        p.restore()

    def _paint_cat_layer(self, p, view):
        c = self.cat
        if c is None or not (view.x() - 50 <= c["x"] <= view.x() + view.width() + 50):
            return
        s = self.surfaces.get(c["sid"]) if c["sid"] else None
        p.save()
        p.setClipRegion(self._allowed_region(view, s), Qt.ClipOperation.IntersectClip)
        if s is not None and c["state"] == "walk":     # apparaît depuis le bord de sa surface
            edge = min(c["rel"] - c["lo"] + 24, c["hi"] + 24 - c["rel"])
            p.setOpacity(p.opacity() * max(0.0, min(1.0, edge / 24)))
        p.translate(c["x"], c["y"] - c.get("lift", 0.0))
        if c["dir"] < 0:
            p.scale(-1, 1)
        if c.get("crouch"):                              # tapi, il remue le derrière avant de bondir
            k = min(1.0, c["crouch"] / 0.45)
            p.rotate(math.sin(c["t"] * 25) * 2 * k)
            p.scale(1, 1 - 0.14 * k)
        if c["state"] == "sleep":
            self._paint_cat_asleep(p, c)
        else:
            self._paint_cat_up(p, c)
        p.restore()

    @staticmethod
    def _cat_ears(p, fur, pts):
        ear = QPainterPath()
        for a, b, tip in pts:
            ear.moveTo(*a)
            ear.lineTo(*tip)
            ear.lineTo(*b)
            ear.closeSubpath()
        p.fillPath(ear, fur)

    def _paint_cat_up(self, p, c):
        fur, dark = QColor(*c["fur"]), QColor(*c["dark"])
        t = c["t"]
        jumping = c["state"] == "jump" or c.get("hop") is not None
        moving = c["state"] != "play" or c.get("moving")
        tail = QPainterPath()
        tail.moveTo(-10, -9)
        tail.quadTo(-17, -13, -15 + 2 * math.sin(t * 3), -21)
        pen = QPen(dark, 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(tail)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dark)
        phase = t * 9
        for lx, ph in ((-8, 0.0), (-5, 2.6), (4, 1.3), (7, 3.9)):
            swing = 0.9 if jumping else math.sin(phase + ph) * 0.5 if moving else 0.0
            p.save()
            p.translate(lx, -5)
            p.rotate(math.degrees(swing if lx > 0 else -swing if jumping else swing))
            p.drawRoundedRect(QRectF(-1.0, 0, 2.0, 5.2), 1.0, 1.0)
            p.restore()
        p.setBrush(fur)
        p.drawRoundedRect(QRectF(-12.0, -12.0, 21.0, 8.5), 4.2, 4.2)
        p.drawEllipse(QPointF(10, -14), 5.2, 4.7)
        self._cat_ears(p, fur, (((6.2, -16.5), (9.6, -18), (6.6, -21.5)),
                                ((10.8, -18), (14, -16.3), (13.8, -21.5))))
        p.setBrush(QColor(45, 40, 50))
        p.drawEllipse(QPointF(11.8, -14.8), 0.75, 0.9)
        p.setBrush(QColor(255, 140, 165, 190))
        p.drawEllipse(QPointF(13, -12.7), 1.2, 0.75)

    def _paint_cat_asleep(self, p, c):
        fur, dark = QColor(*c["fur"]), QColor(*c["dark"])
        t = c["t"]
        p.save()
        p.scale(1, 1 + 0.035 * math.sin(t * 1.7))      # il respire
        p.setBrush(fur)
        p.drawEllipse(QPointF(-1, -6.2), 12.5, 6.6)
        p.drawEllipse(QPointF(7.5, -6.8), 5.0, 4.5)
        self._cat_ears(p, fur, (((3.8, -9.5), (7.0, -10.9), (4.3, -14.2)),
                                ((8.2, -11), (11.3, -9), (11.3, -14))))
        tail = QPainterPath()
        tail.moveTo(-12, -4)
        tail.quadTo(-3, 1.5, 7, -1.8)
        pen = QPen(dark, 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(tail)
        eye = QPainterPath()
        eye.moveTo(6.4, -7)
        eye.quadTo(8, -5.8, 9.6, -7)
        p.setPen(QPen(QColor(45, 40, 50), 0.8))
        p.drawPath(eye)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 140, 165, 180))
        p.drawEllipse(QPointF(10.5, -5.1), 1.2, 0.7)
        p.restore()
        for i in range(3):
            k = (t * 0.35 + i / 3) % 1.0
            size = 1.5 + k * 1.7
            x, y = 12 + k * 9, -15 - k * 20
            z = QPainterPath()
            z.moveTo(x - size, y - size)
            z.lineTo(x + size, y - size)
            z.lineTo(x - size, y + size)
            z.lineTo(x + size, y + size)
            p.setPen(QPen(QColor(150, 180, 230, int(220 * (1 - k))), 0.9))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(z)
        p.setPen(Qt.PenStyle.NoPen)

    def _paint_fun_sky(self, p, view):
        if not (self.balls or self.drips or self.falling_gifts or self.rockets or self.fw_sparks
                or self.shards):
            return
        p.save()
        p.setClipRegion(self._allowed_region(view), Qt.ClipOperation.IntersectClip)
        for sp in self.fw_sparks:
            k = 1 - sp[4] / sp[5]
            r, g, b = sp[6]
            p.setBrush(QColor(r, g, b, int(255 * math.sqrt(k))))
            rad = 1.0 + 1.5 * k
            p.drawEllipse(QPointF(sp[0], sp[1]), rad, rad)
        p.setBrush(QColor(255, 235, 190))
        for r in self.rockets:
            p.drawEllipse(QPointF(r["x"], r["y"]), 1.8, 2.8)
        for sh in self.shards:
            self._paint_shard(p, sh)
        for d in self.drips:
            self._paint_drip(p, d)
        p.setBrush(QColor(250, 252, 255))
        for b in self.balls:
            p.drawEllipse(QPointF(b["x"], b["y"]), 2.6, 2.6)
        for g in self.falling_gifts:
            self._paint_parachute(p, g)
        p.restore()

    @staticmethod
    def _paint_shard(p, sh):
        x, y, _, length, w, _ = sh
        if length <= 2:
            p.setBrush(QColor(170, 215, 250, 210))
            p.drawEllipse(QPointF(x, y), 1.0, 1.5)
            return
        path = QPainterPath()
        path.moveTo(x - w, y)
        path.lineTo(x, y + length)
        path.lineTo(x + w, y)
        path.closeSubpath()
        p.fillPath(path, QColor(190, 228, 255, 220))

    @staticmethod
    def _paint_drip(p, d):
        x, y, age, life, r = d
        k = 1 - age / life
        trail = min(20.0, age * 18)
        color = QColor(255, 255, 255, int(225 * min(1.0, k * 2)))
        p.setBrush(color)
        p.drawRoundedRect(QRectF(x - r * 0.45, y - trail, r * 0.9, trail), r * 0.4, r * 0.4)
        p.drawEllipse(QPointF(x, y), r, r * 0.9)
        p.drawEllipse(QPointF(x - r * 0.9, y - r * 0.5), r * 0.45, r * 0.45)
        p.drawEllipse(QPointF(x + r * 0.9, y - r * 0.2), r * 0.35, r * 0.35)

    def _paint_parachute(self, p, g):
        x, y = g["x"], g["y"]
        p.setPen(QPen(QColor(240, 240, 245, 200), 0.6))
        p.drawLine(QPointF(x - 8, y - 18), QPointF(x - 3, y - 9))
        p.drawLine(QPointF(x + 8, y - 18), QPointF(x + 3, y - 9))
        p.setPen(Qt.PenStyle.NoPen)
        dome = QPainterPath()
        dome.moveTo(x - 9, y - 18)
        dome.quadTo(x, y - 30, x + 9, y - 18)
        dome.closeSubpath()
        p.fillPath(dome, QColor(255, 250, 240, 235))
        self._paint_gift(p, x, y, g["c"])
