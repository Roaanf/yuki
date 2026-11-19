#!/usr/bin/env python3
"""Neige sur les coins arrondis (fenêtres et Dock macOS). Lancer : python3 tests/test_corners.py"""

import os
import random
import sys
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PyQt6.QtCore import QRect, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QImage, QPainter, QRegion  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from yuki.core import COL, TILE, Sim  # noqa: E402

WIN = ["w", 200, 300, 400, 200, 0, 0, 0]


class Corners(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def sim(self, radius):
        random.seed(1)
        sim = Sim([QRect(0, 0, 1280, 800)])
        sim.window_radius = radius
        sim.apply({"w": [WIN], "alive": ["w"]})
        sim._compute_masks()
        return sim, sim.surfaces["w"]

    def test_flat_windows_are_unchanged(self):
        _, s = self.sim(0.0)
        self.assertEqual(set(s.base), {0.0})
        self.assertEqual(s.cap(0), 1.5)

    def test_edge_follows_the_corner(self):
        _, s = self.sim(24.0)
        self.assertGreater(s.base[0], 10)
        self.assertGreater(s.base[0], s.base[2])
        self.assertEqual(s.base[6], 0.0)               # au-delà du rayon : bord droit
        self.assertEqual(s.base[-1], s.base[0])        # coin droit symétrique
        self.assertLess(s.cap(0), 0.5)                 # trop raide : presque rien ne tient

    def test_flake_lands_on_the_curve_not_above_it(self):
        sim, s = self.sim(24.0)
        x = s.x + 1.5 * COL
        top = s.y + s.base[1]
        self.assertFalse(sim._land(x, s.y - 3, s.y - 1, 1.0))    # au-dessus du coin : rien
        self.assertTrue(sim._land(x, top - 1, top + 1, 1.0))

    def test_drawn_pile_stays_on_the_window(self):
        sim, s = self.sim(24.0)
        s.heights = [s.cap(i) for i in range(len(s.heights))]
        img = QImage(1280, 800, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        sim.paint(p, QRectF(sim.screens[0]))
        p.end()
        # au coin gauche, rien à hauteur du haut de la fenêtre : le tas suit la courbe
        for x in range(s.x, s.x + 6):
            self.assertLess(img.pixel(x, s.y - 1) >> 24, 40, x)
        self.assertGreater(img.pixel(s.x + 200, s.y - 2) >> 24, 200)

    def test_partial_render_matches_full_render(self):
        sim, s = self.sim(24.0)
        view = QRectF(0, 0, 1280, 800)

        def render(img, region=None):
            p = QPainter(img)
            if region is not None:
                p.setClipRegion(region)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                p.fillRect(img.rect(), Qt.GlobalColor.transparent)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            sim.paint(p, view)
            p.end()
        img = QImage(1280, 800, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        sim.take_damage()
        render(img)
        for i in range(90):
            x = 200 + (i * 3 if i > 40 else 0)
            sim.apply({"w": [["w", x, 300, 400, 200, 0, 0, 0]], "alive": ["w"]})
            sim.step(1 / 30)
            _, tiles = sim.take_damage()
            reg = QRegion()
            for tx, ty in tiles:
                reg = reg.united(QRect(tx * TILE, ty * TILE, TILE, TILE))
            render(img, reg)
        full = QImage(1280, 800, QImage.Format.Format_ARGB32_Premultiplied)
        full.fill(0)
        render(full)
        diff = sum(1 for y in range(250, 520) for x in range(150, 900)
                   if (img.pixel(x, y) >> 24) - (full.pixel(x, y) >> 24) not in range(-24, 25))
        self.assertEqual(diff, 0)


if __name__ == "__main__":
    unittest.main()
