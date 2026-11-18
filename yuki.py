#!/usr/bin/env python3
"""
Yuki — neige sur le bureau pour KDE Plasma 6 (Wayland ou X11).

- Les flocons tombent par-dessus tout, les clics passent au travers.
- La neige s'accumule sur le haut des fenêtres et des panneaux.
- Déplace une fenêtre d'un coup sec : la neige glisse et tombe.
- Ferme ou réduis une fenêtre : sa neige tombe (ou reste au chaud si réduite).

Comment ça marche :
- l'overlay est une surface « layer-shell » (via libLayerShellQtInterface, fournie par Plasma) ;
- un petit script KWin, chargé à la volée par D-Bus, envoie la position des fenêtres ;
- le script est déchargé à la sortie (Ctrl+C).

Dépendances : python3 + PyQt6 de ta distro (pas celui de pip : il faut le même Qt que Plasma).
"""

import argparse
import ctypes
import json
import math
import os
import random
import signal
import sys
import tempfile
import time

from PyQt6.QtCore import (QEvent, QElapsedTimer, QRect, QObject, QPointF, QRectF, Qt, QTimer,
                          pyqtClassInfo, pyqtSlot)
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from PyQt6.QtGui import (QAction, QBackingStore, QColor, QGuiApplication, QIcon, QPainter, QPen,
                         QPixmap,
                         QPainterPath, QPlatformSurfaceEvent, QRegion, QSurface,
                         QSurfaceFormat, QWindow)

APP_ID = "yuki-snow"
SERVICE = "org.roland.Yuki"
OBJ_PATH = "/Yuki"
IFACE = "org.roland.Yuki"
SCRIPT_NAME = "yuki-snow"

COL = 4               # largeur d'une colonne de neige (px logiques)
MAX_PILE = 14.0       # hauteur max sur une fenêtre
MAX_GROUND = 22.0     # hauteur max au sol
GRAVITY = 900.0
SHED_SPEED = 220.0    # vitesse (px/s) à partir de laquelle la neige glisse
MAX_CLUMPS = 1500
TILE = 32             # taille des tuiles pour ne redessiner que ce qui bouge


# --------------------------------------------------------------------------
# Script KWin (Plasma 6). __PID__, __MANAGE__, __VG__ sont remplacés.
# --------------------------------------------------------------------------
KWIN_SCRIPT = r"""
var PID = __PID__;
var MANAGE_SELF = __MANAGE__;
var VG = __VG__;
var PANEL_GAP = __GAP__;
var DEBUG = __DEBUG__;
var removed = {};
var lastSent = "";

function onCurrentDesktop(w) {
    if (w.onAllDesktops) return true;
    var cur = workspace.currentDesktop;
    var ds = w.desktops || [];
    for (var i = 0; i < ds.length; i++) {
        if (ds[i] && cur && ds[i].id === cur.id) return true;
    }
    return false;
}
function onCurrentActivity(w) {
    var acts = w.activities;
    if (!acts || acts.length === 0) return true;      // sur toutes les activités
    var cur = workspace.currentActivity;
    if (!cur) return true;
    for (var i = 0; i < acts.length; i++) if (acts[i] === cur) return true;
    return false;
}
function kindOk(w) {
    return !w.desktopWindow && (w.normalWindow || w.dialog || w.dock || w.utility);
}
function isVisible(w) {
    return kindOk(w) && !w.minimized && !w.hidden && w.readyForPainting !== false
        && (w.opacity === undefined || w.opacity > 0.05)
        && onCurrentDesktop(w) && onCurrentActivity(w);
}
function manageSelf(w) {
    if (!MANAGE_SELF || w.pid !== PID || !w.normalWindow) return;
    try {
        w.keepAbove = true;
        w.skipTaskbar = true;
        w.skipPager = true;
        w.skipSwitcher = true;
        w.onAllDesktops = true;
        w.noBorder = true;
        w.frameGeometry = {x: VG.x, y: VG.y, width: VG.width, height: VG.height};
    } catch (e) {}
}
// Panneau du bas : sa surface est plus grande que ce qu'on voit (marge du mode flottant).
// Haut visible = bas de la zone des fenêtres maximisées, moins l'écart s'il flotte.
// Plasma « déflotte » le panneau quand une fenêtre le touche : on refait le même test.
function panelTop(p, order) {
    var area;
    try { area = workspace.clientArea(KWin.MaximizeArea, p); } catch (e) { return null; }
    var g = p.frameGeometry;
    var ab = area.y + area.height;
    if (!(ab > g.y && ab < g.y + g.height)) return null;   // pas un panneau du bas
    var touched = false;
    for (var i = 0; i < order.length && !touched; i++) {
        var w = order[i];
        if (!w || w.pid === PID || w.dock || !isVisible(w) || w.fullScreen) continue;
        if (!(w.normalWindow || w.dialog)) continue;
        var f = w.frameGeometry;
        if (f.x < g.x + g.width && f.x + f.width > g.x && f.y + f.height >= ab - PANEL_GAP - 1)
            touched = true;
    }
    var floating = PANEL_GAP > 0 && !touched;
    return {y: floating ? ab - PANEL_GAP : ab, floating: floating};
}

function isMaximized(w) {
    if (w.dock || !w.normalWindow) return false;
    var a;
    try { a = workspace.clientArea(KWin.MaximizeArea, w); } catch (e) { return false; }
    var g = w.frameGeometry;
    return Math.abs(g.x - a.x) <= 2 && Math.abs(g.y - a.y) <= 2 &&
           Math.abs(g.width - a.width) <= 2 && Math.abs(g.height - a.height) <= 2;
}

function push() {
    var order = workspace.stackingOrder;
    if (!order || order.length === undefined) order = workspace.windowList();
    var vis = [], alive = [];
    for (var i = 0; i < order.length; i++) {
        var w = order[i];
        if (!w || w.pid === PID || !kindOk(w)) continue;
        var id = String(w.internalId);
        if (removed[id]) continue;
        alive.push(id);
        if (!isVisible(w)) continue;
        var g = w.frameGeometry;
        var gx = g.x, gy = g.y, gw = g.width, gh = g.height;
        if (w.dock) {
            var top = panelTop(w, order);
            if (top !== null) {
                if (top.floating) { gx += PANEL_GAP; gw -= 2 * PANEL_GAP; }
                gh = gy + gh - top.y; gy = top.y;
            }
        }
        vis.push([id, Math.round(gx), Math.round(gy), Math.round(gw), Math.round(gh),
                  w.dock ? 1 : 0, w.fullScreen ? 1 : 0, isMaximized(w) ? 1 : 0]);
        if (DEBUG) vis[vis.length - 1].push(String(w.caption), String(w.resourceClass));
    }
    var msg = JSON.stringify({w: vis, alive: alive});
    if (msg === lastSent) return;
    lastSent = msg;
    callDBus("org.roland.Yuki", "/Yuki", "org.roland.Yuki", "update", msg);
}
function hook(w) {
    if (!w) return;
    manageSelf(w);
    var sigs = ["frameGeometryChanged", "minimizedChanged", "desktopsChanged",
                "fullScreenChanged", "hiddenChanged"];
    for (var i = 0; i < sigs.length; i++) {
        var s = w[sigs[i]];
        if (s && s.connect) s.connect(push);
    }
}
function connectWs(name, fn) {
    var s = workspace[name];
    if (s && s.connect) s.connect(fn);
}

var initial = workspace.windowList();
for (var i = 0; i < initial.length; i++) hook(initial[i]);
connectWs("windowAdded", function (w) { hook(w); push(); });
connectWs("windowRemoved", function (w) { if (w) removed[String(w.internalId)] = true; push(); });
connectWs("windowActivated", push);
connectWs("currentDesktopChanged", push);
connectWs("currentActivityChanged", push);
connectWs("stackingOrderChanged", push);
push();
"""


# --------------------------------------------------------------------------
# Simulation (coordonnées logiques globales, comme KWin)
# --------------------------------------------------------------------------
class Surface:
    """Un bord qui retient la neige : haut d'une fenêtre, d'un panneau, ou le sol."""
    __slots__ = ("sid", "x", "y", "w", "h", "maxh", "heights", "mask", "t", "ground", "dock",
                 "path")

    def __init__(self, sid, x, y, w, h, maxh, t, ground=False):
        self.sid, self.x, self.y, self.w, self.h = sid, x, y, w, h
        self.maxh, self.t, self.ground = maxh, t, ground
        self.dock = False
        self.path = None          # contour de neige en cache (None = à recalculer)
        n = max(1, int(w // COL))
        self.heights = [0.0] * n
        self.mask = [True] * n

    def resize(self, w):
        n = max(1, int(w // COL))
        old = self.heights
        m = len(old)
        if n != m:
            self.heights = [old[min(m - 1, int(i * m / n))] for i in range(n)]
            self.mask = [True] * n
        self.w = w

    def cap(self, i):
        n = len(self.heights)
        d = min(i, n - 1 - i)
        return min(self.maxh, 1.5 + d * 1.2)   # bouts arrondis


# --------------------------------------------------------------------------
# Lutins, bonhommes de neige et traîneau du père Noël
# Ils restent « derrière » les fenêtres maximisées (et derrière les fenêtres
# posées au-dessus de leur bord) : on ne les dessine que là où on les verrait.
# --------------------------------------------------------------------------
BUILD_END = 16.5        # durée d'une construction de bonhomme (s)
MAX_SNOWMEN = 5
SNOWMAN_LIFE = 20 * 60  # un bonhomme fond au bout de 20 min


def _ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


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
        if self.elves_every > 0:
            self.elves_timer += dt
            if self.elves_timer >= self.elves_every * 60:
                if not self.start_snowman():
                    self.elves_timer = self.elves_every * 60 * 0.8   # réessaie un peu plus tard
        if self.santa_every > 0 and self.santa is None:
            self.santa_timer += dt
            if self.santa_timer >= self.santa_every * 60:
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
                s.path = None
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
        if t > 8:
            k = _ease((t - 8) / 1.5)
            cx = (1 - k) * (16 * d)
            cy = (1 - k) * -5 + k * -33.3
            ball(cx, cy, 4.8)
        if t > 9.5:
            hy = -33.3
            dark = QColor(40, 40, 50)
            if t > 9.8:                                   # yeux
                p.setBrush(dark)
                p.drawEllipse(QPointF(-1.7, hy - 1.0), 0.75, 0.75)
                p.drawEllipse(QPointF(1.7, hy - 1.0), 0.75, 0.75)
                p.setBrush(QColor(255, 150, 175, 170))    # joues
                p.drawEllipse(QPointF(-2.9, hy + 1.0), 1.0, 0.6)
                p.drawEllipse(QPointF(2.9, hy + 1.0), 1.0, 0.6)
            if t > 10.2:                                  # carotte
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
            if t > 11.4:                                  # chapeau
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


class Sim(FestiveMixin):
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
        self.plows = []           # chasse-neige en cours (un par panneau)
        self.plow_every = 10.0    # minutes (0 = jamais tout seul)
        self.plow_timer = 0.0
        self.puffs = []           # fumée du pot d'échappement [x, y, vx, vy, age, r]
        self._festive_init()
        self.surfaces = {}
        self.dormant = {}
        self.order = []            # du bas vers le haut
        self.masks_dirty = True
        self.smooth_acc = 0.0
        self.flakes = []           # [x, y, r, vy, phase, amp, alpha, screen]
        self.clumps = []           # [x, y, vx, vy, r]
        self.density = density
        self.tiles = set()        # dégâts de cette image (tuiles de TILE px, coords globales)
        self._prev_items = set()  # tuiles des flocons dessinés à l'image précédente
        self.full = True          # tout redessiner (démarrage, changement d'écran)
        self.grounds = []
        self.set_screens(screens)

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
            count = int(170 * self.density * (s.width() * s.height()) / (1920 * 1080))
            for _ in range(max(10, count)):
                self.flakes.append(self._new_flake(i, initial=True))
        self.masks_dirty = True
        self.full = True

    # ---- suivi des zones à redessiner ---------------------------------
    @staticmethod
    def _add_tiles(dst, x0, y0, x1, y1):
        tx0, tx1 = int(x0) // TILE, int(x1) // TILE
        ty0, ty1 = int(y0) // TILE, int(y1) // TILE
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                dst.add((tx, ty))

    def _touch(self, s):
        """Le tas de neige de s a changé : cache invalidé + bande à redessiner."""
        s.path = None
        self._add_tiles(self.tiles, s.x - 2, s.y - s.maxh - 3, s.x + s.w + 2, s.y + 4)

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
                    s.y - random.random() * h,
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
        self.masks_dirty = False

    # ---- physique -------------------------------------------------------
    def _land(self, x, y0, y1, amount, skip=None):
        for s in reversed(self.order):
            if s.sid == skip or not (s.x <= x < s.x + s.w):
                continue
            c = int((x - s.x) / COL)
            if c >= len(s.heights) or not s.mask[c]:
                continue
            sy = s.y - s.heights[c]
            if y0 - 1 <= sy <= y1 + 1:
                for dc, wgt in ((-1, 0.25), (0, 0.5), (1, 0.25)):
                    i = c + dc
                    if 0 <= i < len(s.heights) and s.mask[i]:
                        s.heights[i] = min(s.cap(i), s.heights[i] + amount * wgt)
                s.path = None
                self._add_tiles(self.tiles, s.x + (c - 2) * COL, s.y - s.maxh - 3,
                                s.x + (c + 3) * COL, s.y + 4)
                return True
        return False

    def step(self, dt):
        if self.masks_dirty:
            self._compute_masks()
        self.time += dt
        t = self.time
        wind = self.wind * (0.6 + 0.4 * math.sin(t * 0.13)) + 10 * math.sin(t * 0.37)
        dep = 1.2 * self.pile

        for k, f in enumerate(self.flakes):
            y0 = f[1]
            f[0] += (wind * (0.4 + f[2] / 4) + f[5] * math.cos(t * 1.3 + f[4])) * dt
            f[1] += f[3] * dt
            s = self.screens[f[7]]
            if f[0] < s.x():
                f[0] += s.width()
            elif f[0] >= s.x() + s.width():
                f[0] -= s.width()
            if (self.pile > 0 and self._land(f[0], y0, f[1], dep * f[2])) \
                    or f[1] > s.y() + s.height() + 5:
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
        for f in self.flakes:
            r = f[2] + 1.5
            add(cur, f[0] - r, f[1] - r, f[0] + r, f[1] + r)
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
                s.path = None
        self._festive_tiles(add, cur)
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
        if c1 > c0:
            s.path = None

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

    def paint(self, p, view):
        p.save()
        p.translate(-view.x(), -view.y())
        p.setPen(Qt.PenStyle.NoPen)
        vl, vt = view.x(), view.y()
        vr, vb = vl + view.width(), vt + view.height()

        for s in self.order:
            if s.x > vr or s.x + s.w < vl or s.y < vt - 1 or s.y > vb + 1:
                continue
            if s.path is None:
                h, m, n = s.heights, s.mask, len(s.heights)
                path = QPainterPath()
                i = 0
                while i < n:
                    if not m[i] or h[i] < 0.35:
                        i += 1
                        continue
                    path.moveTo(s.x + i * COL, s.y)
                    j = i
                    while j < n and m[j] and h[j] >= 0.35:
                        path.lineTo(s.x + (j + 0.5) * COL, s.y - h[j])
                        j += 1
                    path.lineTo(s.x + j * COL, s.y)
                    path.closeSubpath()
                    i = j
                s.path = (path, path.translated(0, 1.5))
            path, shadow = s.path
            if not path.isEmpty():
                p.fillPath(shadow, self.SHADOW)
                p.fillPath(path, self.SNOW)

        self._paint_festive_ground(p, view)
        self._paint_plow(p)
        self._paint_festive_sky(p, view)

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

        col = QColor(255, 255, 255)
        last = -1
        for f in self.flakes:
            if vl - 4 <= f[0] <= vr + 4 and vt - 4 <= f[1] <= vb + 4:
                a = int(f[6] * factor(f[0], f[1]))
                if a <= 3:
                    continue
                if a != last:
                    col.setAlpha(a)
                    p.setBrush(col)
                    last = a
                p.drawEllipse(QPointF(f[0], f[1]), f[2], f[2])
        for c in self.clumps:
            if vl - 4 <= c[0] <= vr + 4 and vt - 4 <= c[1] <= vb + 4:
                a = int(240 * factor(c[0], c[1]))
                if a <= 3:
                    continue
                col.setAlpha(a)
                p.setBrush(col)
                p.drawEllipse(QPointF(c[0], c[1]), c[4], c[4])
        p.restore()


# --------------------------------------------------------------------------
# Fenêtre overlay
# --------------------------------------------------------------------------
class SnowView(QWindow):
    """Overlay transparent. On gère nous-mêmes le QBackingStore (au lieu de QRasterWindow) :
    quand KWin/LayerShellQt recrée la surface (changement de fréquence d'écran, etc.),
    l'ancien backing store pointe vers une fenêtre Wayland détruite et Qt plante.
    Ici on le jette dès que la surface va disparaître et on en recrée un neuf."""

    def __init__(self, sim, rect):
        super().__init__()
        self.sim = sim
        self.view = QRectF(rect)
        self.store = None
        self.blank = False
        self.frozen = False
        self.needs_full = True
        self.setSurfaceType(QSurface.SurfaceType.RasterSurface)
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        self.setFormat(fmt)
        self.setFlags(Qt.WindowType.FramelessWindowHint
                      | Qt.WindowType.WindowTransparentForInput
                      | Qt.WindowType.WindowStaysOnTopHint
                      | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setTitle("Yuki")
        self.setGeometry(rect)

    def event(self, e):
        t = e.type()
        if t == QEvent.Type.PlatformSurface:
            if e.surfaceEventType() == QPlatformSurfaceEvent.SurfaceEventType.SurfaceAboutToBeDestroyed:
                self.store = None
            self.needs_full = True
        elif t == QEvent.Type.Expose:
            self.needs_full = True
            self.render()
        elif t == QEvent.Type.Resize:
            self.needs_full = True
            if self.store is not None:
                self.store.resize(self.size())
        return super().event(e)

    def _region(self, tiles):
        """Tuiles abîmées -> QRegion locale, en fusionnant les tuiles voisines d'une même ligne."""
        vx, vy = int(self.view.x()), int(self.view.y())
        tx0, ty0 = vx // TILE, vy // TILE
        tx1, ty1 = (vx + self.width()) // TILE, (vy + self.height()) // TILE
        rows = {}
        for tx, ty in tiles:
            if tx0 <= tx <= tx1 and ty0 <= ty <= ty1:
                rows.setdefault(ty, []).append(tx)
        rects = []
        for ty in sorted(rows):                 # ordre « y puis x » exigé par setRects
            xs = sorted(rows[ty])
            start = prev = xs[0]
            for tx in xs[1:] + [None]:
                if tx is not None and tx == prev + 1:
                    prev = tx
                    continue
                rects.append(QRect(start * TILE - vx, ty * TILE - vy,
                                   (prev - start + 1) * TILE, TILE))
                if tx is not None:
                    start = prev = tx
        reg = QRegion()
        if rects:
            reg.setRects(rects)
        return reg.intersected(QRect(0, 0, self.width(), self.height()))

    def render(self, full=True, tiles=()):
        if self.frozen or not self.isExposed() or self.width() <= 0 or self.height() <= 0:
            return
        if self.store is None:
            self.store = QBackingStore(self)
            self.store.resize(self.size())
            self.needs_full = True
        whole = QRegion(0, 0, self.width(), self.height())
        if full or self.needs_full:
            area = whole
        else:
            area = self._region(tiles)
            if area.isEmpty():
                return                       # rien n'a bougé ici : pas de dessin, pas d'envoi
        self.needs_full = False
        self.store.beginPaint(area)
        dev = self.store.paintDevice()
        if dev is None:
            self.store.endPaint()
            return
        p = QPainter(dev)
        p.setClipRegion(area)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(0, 0, self.width(), self.height(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if not (self.sim.user_paused or self.sim.hidden_on(self.view)):
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.sim.paint(p, self.view)
        p.end()
        self.store.endPaint()
        self.store.flush(area)

    def dispose(self):
        self.frozen = True
        self.store = None
        self.hide()
        self.destroy()
        self.deleteLater()


def load_layer_shell():
    try:
        lib = ctypes.CDLL("libLayerShellQtInterface.so.6")
        lib._ZN12LayerShellQt6Window3getEP7QWindow  # noqa: B018 (vérifie le symbole)
        return lib
    except (OSError, AttributeError):
        return None


def make_layer_surface(lib, win):
    """Transforme la QWindow en surface layer-shell (couche « top », plein écran)."""
    from PyQt6 import sip
    get = lib._ZN12LayerShellQt6Window3getEP7QWindow
    get.restype = ctypes.c_void_p
    get.argtypes = [ctypes.c_void_p]
    ptr = get(sip.unwrapinstance(win))
    if not ptr:
        return False

    def call(name, *args, optional=False):
        try:
            fn = getattr(lib, name)
        except AttributeError:
            if optional:
                return
            raise
        fn.restype = None
        fn.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * len(args)
        fn(ptr, *args)

    call("_ZN12LayerShellQt6Window8setLayerENS0_5LayerE", 2)                       # LayerTop
    call("_ZN12LayerShellQt6Window10setAnchorsE6QFlagsINS0_6AnchorEE", 1 | 2 | 4 | 8)
    call("_ZN12LayerShellQt6Window16setExclusiveZoneEi", -1)                      # passe sur les panneaux
    call("_ZN12LayerShellQt6Window24setKeyboardInteractivityENS0_21KeyboardInteractivityE", 0)
    call("_ZN12LayerShellQt6Window26setWantsToBeOnActiveScreenEb", 0, optional=True)
    return True


# --------------------------------------------------------------------------
# Icône ❄ dans la zone de notification
# --------------------------------------------------------------------------
def snowflake_icon():
    icon = QIcon()
    for size in (16, 22, 32, 48, 64):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(size / 2, size / 2)
        k = size / 64
        for color, width in ((QColor(70, 130, 200), 9 * k), (QColor(225, 245, 255), 5 * k)):
            pen = QPen(color, max(1.2, width))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for i in range(6):
                p.save()
                p.rotate(i * 60)
                p.drawLine(QPointF(0, 0), QPointF(0, -26 * k))
                p.drawLine(QPointF(0, -15 * k), QPointF(-8 * k, -22 * k))
                p.drawLine(QPointF(0, -15 * k), QPointF(8 * k, -22 * k))
                p.restore()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 170, 200))
        p.drawEllipse(QPointF(0, 0), 5 * k, 5 * k)     # petit cœur rose au centre
        p.end()
        icon.addPixmap(pm)
    return icon


def make_tray(app, sim):
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("yuki: pas de zone de notification, pas d'icône", file=sys.stderr)
        return None
    tray = QSystemTrayIcon(snowflake_icon(), app)
    tray.setToolTip("Yuki ❄")
    menu = QMenu()

    def act(text, fn):
        a = QAction(text, menu)
        a.triggered.connect(fn)
        menu.addAction(a)
        return a

    def plow():
        if not sim.start_plow():
            msg = ("Le chasse-neige est déjà en route~" if sim.plows
                   else "Pas de panneau visible à déneiger")
            tray.showMessage("Yuki", msg, tray.icon(), 3000)

    def snowman():
        if not sim.start_snowman(force=True):
            tray.showMessage("Yuki", "Pas d'endroit visible avec assez de neige pour un bonhomme 🥲",
                             tray.icon(), 3000)

    def santa():
        if not sim.start_santa():
            tray.showMessage("Yuki", "Le père Noël est déjà en vol !", tray.icon(), 3000)

    act("🚜  Chasse-neige", plow)
    act("⛄  Bonhomme de neige", snowman)
    act("🎅  Père Noël", santa)
    menu.addSeparator()
    pause = act("⏸  Pause", lambda: toggle_pause())
    menu.addSeparator()
    act("✖  Quitter", app.quit)

    def toggle_pause():
        sim.user_paused = not sim.user_paused
        pause.setText("▶  Reprendre" if sim.user_paused else "⏸  Pause")
        tray.setToolTip("Yuki ❄ (en pause)" if sim.user_paused else "Yuki ❄")

    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: toggle_pause()
                           if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()
    tray._menu = menu      # garde une référence
    return tray


# --------------------------------------------------------------------------
# D-Bus
# --------------------------------------------------------------------------
@pyqtClassInfo("D-Bus Interface", IFACE)
class BusObject(QObject):
    def __init__(self, sim, debug=False):
        super().__init__()
        self.sim = sim
        self.debug = debug
        self.last_debug = None

    @pyqtSlot(result=bool)
    def snowman(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki snowman"""
        return self.sim.start_snowman(force=True)

    @pyqtSlot(result=bool)
    def santa(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki santa"""
        return self.sim.start_santa()

    @pyqtSlot(result=bool)
    def plow(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki plow"""
        return self.sim.start_plow()

    @pyqtSlot(str)
    def update(self, payload):
        try:
            data = json.loads(payload)
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


def is_error(msg):
    return msg.type() == QDBusMessage.MessageType.ErrorMessage


def load_kwin_script(bus, path):
    scripting = QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus)
    if not scripting.isValid():
        return "KWin introuvable sur D-Bus (tu es bien dans Plasma ?)"
    scripting.call("unloadScript", SCRIPT_NAME)
    r = scripting.call("loadScript", path, SCRIPT_NAME)
    if is_error(r):
        return "loadScript: " + r.errorMessage()
    sid = r.arguments()[0]
    for p in ("/Scripting/Script%d" % sid, "/%d" % sid):
        s = QDBusInterface("org.kde.KWin", p, "org.kde.kwin.Script", bus)
        if s.isValid() and not is_error(s.call("run")):
            return None
    return "impossible de lancer le script KWin (id %s)" % sid


def unload_kwin_script(bus):
    QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus) \
        .call("unloadScript", SCRIPT_NAME)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Neige kawaii sur Plasma ❄")
    ap.add_argument("--density", type=float, default=1.0, help="quantité de flocons (1.0)")
    ap.add_argument("--wind", type=float, default=15.0, help="vent moyen en px/s (15, négatif = vers la gauche)")
    ap.add_argument("--pile", type=float, default=1.0, help="vitesse d'accumulation (1.0, 0 = pas d'accumulation)")
    ap.add_argument("--melt", type=float, default=0.003, help="fonte par seconde (0.003)")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--over-max", type=float, default=0.0,
                    help="opacité des flocons sur une fenêtre maximisée (0 = cachés, 1 = normal)")
    ap.add_argument("--over-windows", type=float, default=1.0,
                    help="opacité des flocons sur les autres fenêtres (1.0 ; 0.3 = discret)")
    ap.add_argument("--elves-every", type=float, default=6.0,
                    help="des lutins font un bonhomme de neige toutes les N minutes (6 ; 0 = jamais)")
    ap.add_argument("--santa-every", type=float, default=12.0,
                    help="le père Noël passe toutes les N minutes (12 ; 0 = jamais)")
    ap.add_argument("--plow-every", type=float, default=10.0,
                    help="un chasse-neige passe sur le panneau toutes les N minutes s'il y a de la neige (10 ; 0 = jamais)")
    ap.add_argument("--no-tray", action="store_true",
                    help="pas d'icône ❄ dans la barre des tâches")
    ap.add_argument("--debug", action="store_true",
                    help="affiche les fenêtres sur lesquelles la neige peut tenir")
    ap.add_argument("--panel-gap", type=int, default=8,
                    help="écart du panneau flottant en px (8 ; 0 si ton panneau ne flotte pas)")
    args = ap.parse_args()

    if os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "wayland")
    app = QApplication(sys.argv)          # QApplication : nécessaire pour l'icône de la barre
    app.setQuitOnLastWindowClosed(False)

    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        sys.exit("yuki: pas de bus D-Bus de session")
    if not bus.registerService(SERVICE):
        sys.exit("yuki: déjà lancé ? (" + SERVICE + " est pris)")

    lib = load_layer_shell() if app.platformName() == "wayland" else None
    if not lib:
        print("yuki: layer-shell indisponible, mode fenêtre « au-dessus »", file=sys.stderr)

    def current_rects():
        if lib:
            return [s.geometry() for s in app.screens()]
        return [app.primaryScreen().virtualGeometry()]

    rects = current_rects()
    sim = Sim(rects, density=args.density, wind=args.wind, pile=args.pile, melt=args.melt)
    sim.over_max = max(0.0, min(1.0, args.over_max))
    sim.over_win = max(0.0, min(1.0, args.over_windows))
    sim.plow_every = max(0.0, args.plow_every)
    sim.elves_every = max(0.0, args.elves_every)
    sim.santa_every = max(0.0, args.santa_every)
    bus_obj = BusObject(sim, args.debug)
    bus.registerObject(OBJ_PATH, bus_obj, QDBusConnection.RegisterOption.ExportAllSlots)

    views = []

    def build_views():
        # Les anciennes surfaces sont détruites AVANT d'en recréer : peindre sur une
        # surface dont l'écran a changé fait planter QtWayland.
        for v in views:
            v.dispose()
        views.clear()
        rs = current_rects()
        if [QRectF(r) for r in rs] != sim.screens:
            sim.set_screens(rs)
        if lib:
            for scr, rect in zip(app.screens(), rs):
                v = SnowView(sim, rect)
                v.setScreen(scr)
                if not make_layer_surface(lib, v):
                    sys.exit("yuki: layer-shell a échoué")
                views.append(v)
        else:
            views.append(SnowView(sim, rs[0]))
        for v in views:
            v.show()

    build_views()

    # Écran branché/débranché, résolution ou fréquence changée (kscreen-doctor…) :
    # on suspend le dessin et on reconstruit les surfaces une fois que ça s'est calmé.
    rebuild = QTimer()
    rebuild.setSingleShot(True)
    rebuild.setInterval(1200)
    rebuild.timeout.connect(build_views)

    def screens_changed(*_):
        for v in views:
            v.frozen = True
        rebuild.start()

    def watch(scr):
        for sig in (scr.geometryChanged, scr.refreshRateChanged,
                    scr.logicalDotsPerInchChanged, scr.physicalSizeChanged):
            sig.connect(screens_changed)

    for scr in app.screens():
        watch(scr)
    app.screenAdded.connect(lambda scr: (watch(scr), screens_changed()))
    app.screenRemoved.connect(screens_changed)
    app.primaryScreenChanged.connect(screens_changed)

    vg = rects[0] if not lib else app.primaryScreen().virtualGeometry()
    script = (KWIN_SCRIPT
              .replace("__PID__", str(os.getpid()))
              .replace("__MANAGE__", "false" if lib else "true")
              .replace("__GAP__", str(args.panel_gap))
              .replace("__DEBUG__", "true" if args.debug else "false")
              .replace("__VG__", json.dumps({"x": vg.x(), "y": vg.y(),
                                             "width": vg.width(), "height": vg.height()})))
    fd, path = tempfile.mkstemp(prefix="yuki-kwin-", suffix=".js")
    with os.fdopen(fd, "w") as fh:
        fh.write(script)
    err = load_kwin_script(bus, path)
    if err:
        print("yuki:", err, "— la neige tombera mais ne tiendra pas sur les fenêtres",
              file=sys.stderr)

    def cleanup():
        unload_kwin_script(bus)
        try:
            os.unlink(path)
        except OSError:
            pass
    app.aboutToQuit.connect(cleanup)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())

    clock = QElapsedTimer()
    clock.start()

    def tick():
        dt = min(clock.restart() / 1000.0, 0.1)
        live = [v for v in views if not v.frozen]
        if not live:
            return
        hidden = [sim.user_paused or sim.hidden_on(v.view) for v in live]
        if not all(hidden):
            sim.step(dt)
        full, tiles = sim.take_damage()
        for v, h in zip(live, hidden):
            if h != v.blank:
                v.render(full=True)                 # passage plein écran : on vide / on remet tout
            elif not h:
                v.render(full=full, tiles=tiles)
            v.blank = h

    tray = None if args.no_tray else make_tray(app, sim)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(max(5, int(1000 / max(1, args.fps))))
    print("yuki: ❄ il neige ! Ctrl+C pour arrêter.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
