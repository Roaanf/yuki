#!/usr/bin/env python3
"""Banc d'essai headless : temps de simulation et de rendu par image, sans toucher à la session.

Reproduit deux écrans (2560x1440 à 100 % et 1707x1067 à 150 %), quelques fenêtres, de la neige
déjà posée, et le rendu partiel de SnowView (tuiles abîmées seulement).

    python3 tools/bench.py                      # hiver, density 1
    python3 tools/bench.py --season printemps --frames 300
    python3 tools/bench.py --profile            # cProfile (gonfle les temps ~2x)
    python3 tools/bench.py --dump /tmp/x        # écrit x_0.png, x_1.png pour comparer deux versions
"""

import argparse
import cProfile
import datetime
import os
import pstats
import random
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PyQt6.QtCore import QRect, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QGuiApplication, QImage, QPainter, QRegion  # noqa: E402

from yuki.core import Sim  # noqa: E402
from yuki.fun import SEASON_NAMES  # noqa: E402
from yuki.view import SnowView, tile_rows  # noqa: E402

SCREENS = [(QRect(0, 0, 2560, 1440), 1.0), (QRect(2560, 0, 1707, 1067), 1.5)]
WINDOWS = [["dock0", 8, 1392, 2544, 48, 1, 0, 0], ["dock1", 2568, 1019, 1691, 48, 1, 0, 0],
           ["a", 100, 200, 1200, 800, 0, 0, 0], ["b", 900, 120, 1300, 900, 0, 0, 0],
           ["c", 2700, 80, 1200, 700, 0, 0, 0]]


class View:
    """Ce que fait SnowView.render, sur une QImage au lieu d'un QBackingStore."""
    _region = SnowView._region

    def __init__(self, sim, rect, dpr):
        self.sim, self.view = sim, QRectF(rect)
        self.w, self.h = rect.width(), rect.height()
        self.img = QImage(int(self.w * dpr), int(self.h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        self.img.setDevicePixelRatio(dpr)
        self.img.fill(0)
        self.rects = 0

    def width(self):
        return self.w

    def height(self):
        return self.h

    def render(self, full, rows):
        if full:
            area, runs = QRegion(0, 0, self.w, self.h), None
        else:
            area, runs = self._region(rows)
            if area.isEmpty():
                return
        self.rects += area.rectCount()
        p = QPainter(self.img)
        p.setClipRegion(area)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(0, 0, self.w, self.h, Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.sim.paint(p, self.view, runs)
        p.end()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--density", type=float, default=1.0)
    ap.add_argument("--season", choices=[k for k in SEASON_NAMES if k != "auto"], default="hiver")
    ap.add_argument("--frames", type=int, default=600)
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--dump", help="préfixe des PNG écrits à la fin")
    args = ap.parse_args()

    app = QGuiApplication(sys.argv)  # noqa: F841
    random.seed(1)
    sim = Sim([r for r, _ in SCREENS], density=args.density, wind=15, pile=0.35, melt=0.006)
    sim.over_win, sim.over_max = 0.6, 0.1
    sim.clock = lambda: datetime.datetime(2026, 12, 1, 12)
    sim.set_season(SEASON_NAMES[args.season])
    sim.apply({"w": WINDOWS, "alive": [w[0] for w in WINDOWS]})
    sim.step(1 / 60)
    for s in sim.order:                        # neige déjà posée
        s.heights = [s.cap(i) * random.uniform(0.4, 1.0) for i in range(len(s.heights))]
    views = [View(sim, r, d) for r, d in SCREENS]

    def frame(i):
        sim.set_cursor(1000 + 300 * (i % 40), 700)
        t0 = time.perf_counter()
        sim.step(1 / 60)
        t1 = time.perf_counter()
        full, tiles = sim.take_damage()
        rows = None if full else tile_rows(tiles)
        for v in views:
            v.render(full, rows)
        return t1 - t0, time.perf_counter() - t1

    for i in range(30):
        frame(i)
    for v in views:
        v.rects = 0
    prof = cProfile.Profile() if args.profile else None
    if prof:
        prof.enable()
    st = rt = 0.0
    for i in range(args.frames):
        a, b = frame(i)
        st += a
        rt += b
    if prof:
        prof.disable()
        pstats.Stats(prof).sort_stats("tottime").print_stats(20)
    n = args.frames
    print("%d flocons  step %.2f ms  rendu %.2f ms  total %.2f ms/image  (%.0f rectangles/vue)" % (
        len(sim.flakes), st / n * 1e3, rt / n * 1e3, (st + rt) / n * 1e3,
        sum(v.rects for v in views) / n / len(views)))
    if args.dump:
        for k, v in enumerate(views):
            v.img.save("%s_%d.png" % (args.dump, k))


if __name__ == "__main__":
    main()
