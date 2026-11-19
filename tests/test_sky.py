#!/usr/bin/env python3
"""Aurores, lucioles, averses, givre, luge, oiseau postier, retour d'absence, jeux de Neko-chan
et images par seconde au calme.

Lancer : python3 tests/test_sky.py
"""

import argparse
import datetime
import os
import random
import sys
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from test_fun import make, move_window, run  # noqa: E402
from yuki import fun, sky, visitors  # noqa: E402
from yuki.settings import Settings  # noqa: E402
from yuki.view import SnowView, tile_rows  # noqa: E402

VIEW = QRectF(0, 0, 1440, 900)


def at(month, day, hour):
    return lambda: datetime.datetime(2026, month, day, hour, 0)


def paint(sim, img=None):
    img = img or QImage(1440, 900, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    sim.paint(p, VIEW)
    p.end()
    return img


def alpha(img, x, y):
    return img.pixel(x, y) >> 24


def worst_gap(a, b):
    """Plus grand écart d'un canal entre deux images. Qt arrondit la couverture différemment
    selon la boîte du clip (±2) : un oubli de redessin, lui, donne des écarts de 100 et plus."""
    if a == b:
        return 0
    pa, pb = a.constBits(), b.constBits()
    pa.setsize(a.sizeInBytes())
    pb.setsize(b.sizeInBytes())
    return max(abs(x - y) for x, y in zip(bytes(pa), bytes(pb)))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        random.seed(11)


class CalmFps(Base):
    def test_calm_frame_rate_follows_the_settings(self):
        args = argparse.Namespace(density=1.0, wind=15.0, pile=1.0, melt=0.003, fps=60, over_max=0.0,
                                  over_windows=1.0, plow_every=10.0, elves_every=6.0, santa_every=12.0,
                                  fight_every=8.0, cat_every=9.0, cursor=1.0, season=0.0, fps_calm=30,
                                  aurora_every=20.0, rain_every=12.0, frost_after=2.0, bird=1.0)
        from PyQt6.QtCore import QSettings
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(args, QSettings(os.path.join(tmp, "y.conf"), QSettings.Format.IniFormat))
            self.assertEqual((s.frame_ms, s.calm_ms), (16, 33))
            s.set("fps_calm", 0.0)
            self.assertEqual(s.calm_ms, 16)
            s.set("fps", 15.0)
            s.set("fps_calm", 30.0)
            self.assertEqual(s.calm_ms, s.frame_ms)        # jamais plus vite qu'en action

    def test_falling_snow_is_calm_but_a_plow_is_not(self):
        sim = make()
        run(sim, 1)
        self.assertFalse(sim.lively())
        self.assertTrue(sim.start_plow())
        sim.step(1 / 30)
        self.assertTrue(sim.lively())

    def test_fast_cursor_is_lively(self):
        sim = make()
        for i in range(10):
            sim.set_cursor(100 + 40 * i, 400)
            sim.step(1 / 30)
        self.assertTrue(sim.lively())


class NekoPlays(Base):
    def walking_cat(self):
        sim = make()
        sim.surfaces["1"].heights = [3.0] * len(sim.surfaces["1"].heights)
        self.assertTrue(sim.start_cat())
        c = sim.cat
        s = sim.surfaces[c["sid"]]
        c["rel"] = (c["lo"] + c["hi"]) / 2
        c["x"] = s.x + c["rel"]
        return sim, c, s

    def test_slow_cursor_nearby_is_chased_then_pounced(self):
        sim, c, s = self.walking_cat()
        cx = s.x + c["rel"] + 90
        hopped = False
        for _ in range(150):
            sim.set_cursor(cx, s.y - 20)
            sim.step(1 / 30)
            hopped = hopped or (sim.cat is not None and sim.cat.get("hop") is not None)
        self.assertEqual(sim.cat["state"], "play")
        self.assertLess(abs(sim.cat["x"] - cx), 20)
        self.assertTrue(hopped)

    def test_fast_swipe_startles_it(self):
        sim, c, s = self.walking_cat()
        sim.set_cursor(s.x + c["rel"] + 60, s.y - 20)
        run(sim, 0.5)
        self.assertEqual(sim.cat["state"], "play")
        x, y = sim.cursor
        for i in range(10):                        # balayage rapide qui passe sur lui
            sim.set_cursor(x + 90 - 30 * i, sim.cat["y"] - 8)
            sim.step(1 / 60)
        self.assertEqual(sim.cat["state"], "jump")

    def test_gets_bored_when_the_cursor_leaves(self):
        sim, c, s = self.walking_cat()
        sim.set_cursor(s.x + c["rel"] + 60, s.y - 20)
        run(sim, 0.5)
        sim.set_cursor(5, 5)
        run(sim, 3)
        self.assertTrue(sim.cat["leaving"])

    def test_sleeping_cat_must_be_teased(self):
        sim, c, s = self.walking_cat()
        c.update(state="sleep", sleep=300.0, sx=s.x, sy=s.y)
        sim.set_cursor(c["x"] + 30, s.y - 10)
        run(sim, 1.0)
        self.assertEqual(sim.cat["state"], "sleep")
        run(sim, 1.0)
        self.assertEqual(sim.cat["state"], "play")


class Aurora(Base):
    def test_winter_evenings_bring_it_and_it_stays_behind_windows(self):
        sim = make(now=datetime.datetime(2026, 12, 10, 21, 0))
        sim.aurora_every = 0.01
        run(sim, 2)
        self.assertGreater(sim.aurora_left, 0)
        run(sim, sky.AURORA_FADE + 1)
        self.assertEqual(sim.aurora_level, 1.0)
        img = paint(sim)
        sky_pixels = sum(alpha(img, x, y) > 0 for x in range(0, 1440, 10) for y in range(0, 320, 10)
                         if not (100 <= x < 600 and y >= 200))
        self.assertGreater(sky_pixels, 100)
        inside = [alpha(img, x, 250) for x in range(150, 550, 10)]   # fenêtre « 1 » (y ≥ 200)
        self.assertEqual(max(inside), 0)

    def test_not_at_noon_and_it_ends(self):
        sim = make()
        sim.aurora_every = 0.01
        run(sim, 2)
        self.assertEqual(sim.aurora_left, 0)
        self.assertTrue(sim.start_aurora())
        self.assertFalse(sim.start_aurora())
        run(sim, sky.AURORA_LEN + 2)
        self.assertEqual(sim.aurora_level, 0)
        self.assertEqual(sim._aurora_img, {})

    def test_moving_a_window_redraws_the_sky_it_uncovers(self):
        sim = make()
        sim.start_aurora()
        run(sim, sky.AURORA_FADE + 1)
        sim.take_damage()
        move_window(sim, "1", 100, 500)
        tiles = sim.take_damage()[1]
        self.assertIn((200 // 32, 250 // 32), tiles)

    def test_shooting_star_crosses_and_fades(self):
        sim = make()
        self.assertTrue(sim.start_star())
        self.assertTrue(sim.lively())
        run(sim, sky.STAR_LIFE + 0.1)
        self.assertEqual(sim.stars, [])


class Fireflies(Base):
    def test_summer_nights_swap_snow_for_fireflies(self):
        sim = make(now=datetime.datetime(2026, 7, 14, 22, 30))
        self.assertTrue(sim.summer_night)
        run(sim, 12)
        self.assertEqual(sim.snow_level, 0.0)
        self.assertGreater(len(sim.fireflies), 4)
        self.assertFalse(sim.lively())
        before = sum(sum(s.heights) for s in sim.order)
        run(sim, 3)
        self.assertLessEqual(sum(sum(s.heights) for s in sim.order), before)   # elle ne fait que fondre

    def test_summer_days_keep_the_snow(self):
        sim = make(now=datetime.datetime(2026, 7, 14, 15, 0))
        run(sim, 3)
        self.assertEqual((sim.snow_level, sim.fireflies), (1.0, []))

    def test_menu_fireflies_glow(self):
        sim = make()
        sim.start_fireflies()
        run(sim, 5)
        self.assertEqual(sim.snow_level, 1.0)      # avec la neige, quand on les appelle
        lit = 0
        for _ in range(30):
            run(sim, 0.2)
            img = paint(sim)
            lit = max(lit, sum(alpha(img, int(f[0]), int(f[1])) > 60 for f in sim.fireflies))
        self.assertGreater(lit, 0)


class Rain(Base):
    def autumn(self):
        sim = make(now=datetime.datetime(2026, 10, 5, 12, 0))
        self.assertEqual(sim.season, fun.AUTUMN)
        return sim

    def test_autumn_showers_wet_the_windows(self):
        sim = self.autumn()
        sim.rain_every = 0.01
        run(sim, 20)
        self.assertGreater(sim.rain_left, 0)
        self.assertGreater(len(sim.streaks), 30)
        self.assertTrue(sim.drops)
        self.assertFalse(sim.lively())
        img = paint(sim)
        self.assertGreater(sum(alpha(img, x, y) > 0 for x in range(0, 1440, 6) for y in range(0, 900, 6)), 50)

    def test_drops_slide_down_and_stay_on_their_window(self):
        sim = self.autumn()
        sim.drops = [["1", 100.0, 20.0, 3.0, 0.0, 0.0, 0.0, 0.0, 30.0]]
        run(sim, 2)
        self.assertGreater(sim.drops[0][2], 20.0)
        move_window(sim, "1", 300, 250)
        d = sim.drops[0]
        self.assertEqual(sim.surfaces["1"].x + d[1], 400.0)

    def test_rain_stops(self):
        sim = self.autumn()
        sim.rain_every = 0
        sim.start_rain(15.0)
        run(sim, 17)
        self.assertEqual((sim.rain_left, sim.streaks), (0, []))

    def test_streaks_splash_on_piles(self):
        sim = self.autumn()
        s = sim.surfaces["1"]
        sim.streaks = [[s.x + 200, s.y - 30, 0.0, 900.0, 12.0, 0]]
        run(sim, 0.1)
        self.assertEqual(sim.streaks, [])
        self.assertTrue(sim.splashes)


class Frost(Base):
    def test_grows_when_away_and_corners_first(self):
        sim = make()
        sim.idle_external = 200.0
        run(sim, 240)
        top = sim.frost[0]["top"]["d"]
        n = len(top)
        self.assertGreater(sum(top[:6]) / 6, sum(top[n // 3:2 * n // 3]) / (n // 3))
        img = paint(sim)
        self.assertGreater(alpha(img, 3, 3), 50)

    def test_cursor_wipes_it_and_coming_back_melts_it(self):
        sim = make()
        sim.idle_external = 200.0
        run(sim, 90)
        d = sim.frost[0]["top"]["d"]
        before = d[2]
        sim.set_cursor(20, 5)
        sim.step(1 / 30)
        self.assertLess(d[2], before)
        self.assertEqual(sim.frost[0]["top"]["d"][100], d[100])
        sim.idle_external = 0.0
        run(sim, 15)
        self.assertEqual(max(sim.frost[0]["top"]["d"]), 0.0)

    def test_typing_without_moving_the_mouse_is_not_away(self):
        sim = make()
        sim.idle_external = 30.0          # le backend voit le clavier
        run(sim, 150)
        self.assertEqual(max(c["max"] for c in sim.frost[0].values()), 0.0)

    def test_without_backend_the_cursor_counts(self):
        sim = make()
        sim.frost_after = 0.05
        run(sim, 10)
        self.assertGreater(sim.idle, 5)
        self.assertGreater(max(c["max"] for c in sim.frost[0].values()), 0.0)


class Sled(Base):
    def test_rides_off_the_window_lands_and_walks_away(self):
        sim = make()
        self.assertTrue(sim.start_sled())
        states = set()
        for _ in range(int(20 / (1 / 30))):
            sim.step(1 / 30)
            if sim.sled is None:
                break
            states.add(sim.sled["state"])
        self.assertIsNone(sim.sled)
        self.assertLessEqual({"ride", "air", "stop"}, states)

    def test_shaking_a_snowy_window_launches_one(self):
        sim = make()
        random.seed(1)
        for i in range(12):
            if sim.sled:
                break
            move_window(sim, "1", 100 + (40 if i % 2 else -40), 200)
            sim.step(1 / 60)
        self.assertIsNotNone(sim.sled)
        self.assertEqual(sim.sled["state"], "air")

    def test_closing_a_window_does_not(self):
        sim = make()
        for _ in range(20):
            sim.apply({"w": [list(w) for w in [["2", 800, 300, 500, 300, 0, 0, 0]]], "alive": ["2"]})
            sim.apply({"w": [["1", 100, 200, 500, 300, 0, 0, 0], ["2", 800, 300, 500, 300, 0, 0, 0]],
                       "alive": ["1", "2"]})
        self.assertIsNone(sim.sled)


class Bird(Base):
    def test_brings_a_letter_to_the_notification(self):
        sim = make()
        self.assertTrue(sim.notified(1100, 700, 300, 90))
        self.assertFalse(sim.notified(1100, 600, 300, 90))       # une seule tournée à la fois
        run(sim, visitors.BIRD_DROP + 0.5)
        ex, ey, fade = sim._envelope_pos(sim.bird)
        self.assertEqual((ex, ey, fade), (1250, 697, 1.0))
        img = paint(sim)
        self.assertGreater(alpha(img, 1250, 694), 100)
        run(sim, visitors.BIRD_END)
        self.assertIsNone(sim.bird)
        self.assertFalse(sim.notified(1100, 700, 300, 90))        # trop tôt après la précédente
        run(sim, visitors.BIRD_GAP)
        self.assertTrue(sim.notified(1100, 700, 300, 90))

    def test_can_be_turned_off_for_notifications(self):
        sim = make()
        sim.bird_on = 0.0
        self.assertFalse(sim.notified(1100, 700, 300, 90))
        self.assertTrue(sim.start_bird())


class Away(Base):
    def test_snow_piles_up_and_neko_naps_on_it(self):
        sim = make(snow=0.5)
        sim.cat_every = 0
        sim.idle_external = visitors.AWAY_AFTER + 5
        run(sim, 1)
        self.assertTrue(sim.away)
        sim.idle_external = 0.0
        sim.step(1 / 30)
        self.assertFalse(sim.away)
        s = sim.surfaces["1"]
        self.assertGreater(min(s.heights[10:-10]), 3.0)
        self.assertEqual(sim.cat["state"], "sleep")
        self.assertFalse(sim.welcome_back(3600))          # veille + inactivité : une seule fois

    def test_no_plow_while_away(self):
        sim = make(snow=5.0)
        sim.plow_every = 0.01
        sim.idle_external = visitors.AWAY_AFTER + 5
        run(sim, 3)
        self.assertEqual(sim.plows, [])


class Rendering(Base):
    def test_everything_at_once_renders_like_a_full_render(self):
        class Stub:
            view = VIEW
            _region = SnowView._region

            def width(self):
                return 1440

            def height(self):
                return 900

        def render(img, sim, rows=None):
            p = QPainter(img)
            if rows is not None:
                area, runs = Stub()._region(rows)
                p.setClipRegion(area)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                p.fillRect(img.rect(), Qt.GlobalColor.transparent)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            sim.paint(p, VIEW, None if rows is None else runs)
            p.end()

        for when in (at(12, 10, 21), at(10, 5, 12), at(7, 14, 22)):
            random.seed(5)
            sim = make(now=when())
            sim.idle_external = 300.0
            sim.start_aurora()
            sim.start_rain(60.0)
            sim.start_sled()
            sim.start_bird()
            sim.start_star()
            sim.start_fireflies()
            run(sim, 30)
            sim.notified(900, 600, 300, 90)
            img = QImage(1440, 900, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(0)
            sim.take_damage()
            render(img, sim)
            for i in range(45):                    # < 2 s : le lissage redessinerait tous les tas
                if i == 20:
                    move_window(sim, "1", 140, 220)
                sim.set_cursor(10 + 3 * i, 8)
                sim.step(1 / 30)
                render(img, sim, tile_rows(sim.take_damage()[1]))
            full = QImage(1440, 900, QImage.Format.Format_ARGB32_Premultiplied)
            full.fill(0)
            render(full, sim)
            self.assertLessEqual(worst_gap(img, full), 4, when())


if __name__ == "__main__":
    unittest.main()
