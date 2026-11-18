#!/usr/bin/env python3
"""Bataille, curseur, cadeaux, feux d'artifice, glaçons, chat, saisons.

Lancer : python3 tests/test_fun.py
"""

import datetime
import os
import random
import sys
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PyQt6.QtCore import QRect, QRectF  # noqa: E402
from PyQt6.QtGui import QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from yuki import fun  # noqa: E402
from yuki.core import TILE, Sim  # noqa: E402

DECEMBER = datetime.datetime(2026, 12, 10, 12, 0)
WINDOWS = [["1", 100, 200, 500, 300, 0, 0, 0], ["2", 800, 300, 500, 300, 0, 0, 0]]


def make(now=DECEMBER, snow=5.0):
    sim = Sim([QRect(0, 0, 1440, 900)], density=0.2)
    sim.clock = lambda: now
    sim.set_season(fun.AUTO)
    sim.elves_every = sim.santa_every = sim.plow_every = sim.fight_every = sim.cat_every = 0
    sim.apply({"w": [list(w) for w in WINDOWS], "alive": ["1", "2"]})
    sim._compute_masks()
    for s in sim.order:
        s.heights = [snow] * len(s.heights)
    return sim


def run(sim, seconds, dt=1 / 30):
    for _ in range(int(seconds / dt)):
        sim.step(dt)


def move_window(sim, sid, x, y):
    items = [list(w) for w in WINDOWS]
    for w in items:
        if w[0] == sid:
            w[1], w[2] = x, y
    sim.apply({"w": items, "alive": ["1", "2"]})
    sim._compute_masks()


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        random.seed(7)


class Seasons(Base):
    def test_dates(self):
        expected = {1: fun.WINTER, 3: fun.SPRING, 5: fun.SPRING, 7: fun.WINTER,
                    9: fun.AUTUMN, 10: fun.AUTUMN, 11: fun.WINTER, 12: fun.WINTER}
        for month, season in expected.items():
            self.assertEqual(fun.season_for(datetime.date(2026, month, 15)), season, month)

    def test_choice_overrides_date_and_recolors(self):
        sim = make()
        before = sim.SNOW.rgba()
        sim.take_damage()
        sim.set_season(fun.SPRING)
        self.assertEqual(sim.season, fun.SPRING)
        self.assertNotEqual(sim.SNOW.rgba(), before)
        self.assertTrue(sim.take_damage()[0])
        sim.set_season(fun.AUTO)
        self.assertEqual(sim.season, fun.WINTER)

    def test_season_follows_the_clock(self):
        sim = make()
        sim.clock = lambda: datetime.datetime(2027, 4, 1, 12, 0)
        run(sim, 6)
        self.assertEqual(sim.season, fun.SPRING)

    def test_winter_visitors_stay_home_in_spring(self):
        sim = make(datetime.datetime(2026, 4, 10, 12, 0))
        sim.santa_every = sim.elves_every = sim.fight_every = 0.01
        run(sim, 3)
        self.assertIsNone(sim.santa)
        self.assertEqual(sim.snowmen, [])
        self.assertIsNone(sim.fight)


class Cursor(Base):
    def sweep(self, sim, speed, y=196):
        sim.set_cursor(200, y)
        sim.step(1 / 30)
        for i in range(1, 12):
            sim.set_cursor(200 + i * speed / 30, y)
            sim.step(1 / 30)

    def test_fast_cursor_sweeps_the_pile(self):
        sim = make()
        before = sum(sim.surfaces["1"].heights)
        self.sweep(sim, 1500)
        self.assertLess(sum(sim.surfaces["1"].heights), before - 3)
        self.assertTrue(sim.clumps)

    def test_slow_cursor_leaves_the_pile(self):
        sim = make()
        sim.melt = 0
        before = list(sim.surfaces["1"].heights)
        self.sweep(sim, 150)
        self.assertEqual(sim.clumps, [])
        self.assertEqual(sim.surfaces["1"].heights, before)

    def test_zero_force_does_nothing(self):
        sim = make()
        sim.cursor_force = 0
        self.sweep(sim, 1500)
        self.assertEqual(sim.clumps, [])

    def test_flakes_are_pushed_away(self):
        sim = make()
        sim.flakes = [[700.0, 500.0, 2.0, 0.0, 0.0, 0.0, 200, 0]]
        sim.cursor_v = (1000.0, 0.0)
        sim.cursor_prev = (660, 500)
        sim.set_cursor(690, 500)
        sim._step_cursor(1 / 30)
        self.assertGreater(sim.flakes[0][0], 700)

    def test_jump_between_screens_is_capped(self):
        sim = make()
        sim.cursor_prev = (0, 0)
        sim.set_cursor(5000, 0)
        sim._step_cursor(1 / 30)
        self.assertLessEqual(sim.cursor_speed, fun.CURSOR_MAX_SPEED + 1e-6)


class Fight(Base):
    def test_full_fight_throws_and_ends(self):
        sim = make()
        self.assertTrue(sim.start_fight(force=True))
        self.assertFalse(sim.start_fight(force=True))
        thrown = 0
        for _ in range(int(fun.LEAVE_END * 30) + 5):
            before = len(sim.balls)
            sim.step(1 / 30)
            thrown += max(0, len(sim.balls) - before)
        self.assertGreater(thrown, 4)
        self.assertIsNone(sim.fight)

    def test_closing_a_window_stops_the_fight(self):
        sim = make()
        sim.start_fight(force=True)
        sim.fight["sides"][0]["sid"] = "1"
        sim.apply({"w": [WINDOWS[1]], "alive": ["2"]})
        sim.step(1 / 30)
        self.assertIsNone(sim.fight)

    def test_ball_hits_an_elf(self):
        sim = make()
        sim.start_fight(force=True)
        sim.fight["age"] = 5.0
        fi = sim.fight
        e = next(o for o in fi["elves"] if o["side"] == 1)
        x, y = sim._elf_pos(fi, e)
        self.assertTrue(sim._ball_hit({"x": x, "y": y - 7, "vx": 0, "foe": 1}))
        self.assertGreater(e["hit"], 0)
        self.assertEqual(fi["sides"][0]["hits"], 1)

    def test_ball_knocks_a_snowman_head_off(self):
        sim = make()
        s = sim.surfaces["1"]
        m = {"sid": "1", "rel": 200.0, "age": 12.0, "dir": 1, "melt": None, "hat": "top",
             "scarf": (1, 2, 3)}
        sim.snowmen.append(m)
        hx, hy, _ = sim._snowman_heads()[0]
        self.assertEqual(hx, s.x + 200)
        self.assertTrue(sim._ball_hit({"x": hx, "y": hy, "vx": 0, "foe": 0}))
        self.assertTrue(m["headless"])
        self.assertEqual(sim._snowman_heads(), [])

    def test_sticky_ball_slides_down_the_window(self):
        sim = make()
        sim.balls.append({"x": 300.0, "y": 350.0, "vx": 0.0, "vy": 10.0, "t": 2.0, "end": 1.0,
                          "foe": 0, "stick": True})
        sim.step(1 / 30)
        self.assertEqual(sim.balls, [])
        y = sim.drips[0][1]
        run(sim, 1)
        self.assertGreater(sim.drips[0][1], y)
        run(sim, 8)
        self.assertEqual(sim.drips, [])


class Calendar(Base):
    def test_santa_comes_more_often_at_christmas(self):
        sim = make()
        sim.santa_every = 12
        normal = sim._santa_period()
        sim = make(datetime.datetime(2026, 12, 24, 12, 0))
        sim.santa_every = 12
        self.assertEqual(sim._santa_period(), normal / 4)

    def test_fireworks_days(self):
        self.assertIsNone(make(datetime.datetime(2026, 12, 31, 12, 0)).fireworks_gap)
        self.assertEqual(make(datetime.datetime(2026, 12, 31, 20, 0)).fireworks_gap[2], 3)
        self.assertEqual(make(datetime.datetime(2027, 1, 1, 0, 5)).fireworks_gap[2], 5)
        self.assertIsNone(make(datetime.datetime(2027, 1, 1, 1, 0)).fireworks_gap)

    def test_rockets_burst_then_fade(self):
        sim = make()
        sim.start_fireworks(3)
        run(sim, 2.2)
        self.assertGreater(len(sim.fw_sparks), 30)
        run(sim, 4)
        self.assertEqual((sim.rockets, sim.rocket_queue, sim.fw_sparks), ([], [], []))

    def test_santa_drops_a_gift_that_lands(self):
        sim = make()
        sim.start_santa()
        sim.santa.update(x=300.0, dir=1, speed=0.0, y0=60.0, y=60.0)
        sim.gift_timer = 0.0
        sim.step(1 / 30)
        self.assertEqual(len(sim.falling_gifts), 1)
        sim.santa = None
        run(sim, 4)
        self.assertEqual(sim.falling_gifts, [])
        self.assertEqual(sim.gifts[0]["sid"], "1")

    def add_gift(self, sim, sid="1", rel=100.0):
        sim.gifts.append({"sid": sid, "rel": rel, "lift": 3.0, "age": 0.0, "c": 0})

    def test_gift_falls_when_the_window_is_shaken(self):
        sim = make()
        self.add_gift(sim)
        sim._shed(sim.surfaces["1"], 0.9, 0, 0)
        self.assertEqual(sim.gifts, [])
        self.assertEqual(sim.falling_gifts[0]["skip"], "1")

    def test_plow_opens_the_gift(self):
        sim = make()
        self.add_gift(sim)
        sim.plows.append({"sid": "1", "rel": 100.0, "dir": 1, "load": 0.0, "dist": 0.0,
                          "dumped": False, "puff": 0.0, "speed": 0.0})
        sim.step(1 / 30)
        self.assertEqual(sim.gifts, [])
        self.assertTrue(sim.sparkles)

    def test_gift_count_is_capped(self):
        sim = make()
        s = sim.surfaces["1"]
        for i in range(fun.MAX_GIFTS + 3):
            sim._rest_gift({"c": 0}, s, 20.0 + i)
        self.assertEqual(len(sim.gifts), fun.MAX_GIFTS)


class Ice(Base):
    def test_ice_grows_under_snowy_windows_only(self):
        sim = make()
        sim.surfaces["2"].heights = [0.0] * len(sim.surfaces["2"].heights)
        for _ in range(60):
            sim._grow_ice(sim.surfaces["1"], 4.0)
            sim._grow_ice(sim.surfaces["2"], 4.0)
            sim.surfaces["1"].heights = [5.0] * len(sim.surfaces["1"].heights)
        ice = sim.surfaces["1"].ice
        self.assertTrue(ice)
        self.assertTrue(all(i[1] <= i[2] <= fun.ICE_MAX for i in ice))
        self.assertEqual(sim.surfaces["2"].ice, [])

    def test_shaking_drops_the_ice(self):
        sim = make()
        s = sim.surfaces["1"]
        s.ice = [[100.0, 10.0, 12.0, 2.0]]
        sim._shed(s, 0.4, 0, 0)
        self.assertEqual(s.ice, [])
        self.assertEqual(len(sim.shards), 1)
        run(sim, 2)
        self.assertEqual(sim.shards, [])

    def test_ice_melts_out_of_winter(self):
        sim = make(datetime.datetime(2026, 4, 10, 12, 0))
        s = sim.surfaces["1"]
        s.ice = [[100.0, 1.0, 12.0, 2.0]]
        run(sim, 5)
        self.assertEqual(s.ice, [])

    def test_redraw_reaches_the_tips(self):
        sim = make()
        s = sim.surfaces["1"]
        s.ice = [[100.0, 14.0, 14.0, 2.0]]
        sim.take_damage()
        sim._touch(s)
        rows = {ty for _, ty in sim.take_damage()[1]}
        self.assertIn(int((s.y + 14) // TILE), rows)

    def test_resize_forgets_ice_beyond_the_edge(self):
        s = make().surfaces["1"]
        s.ice = [[100.0, 5.0, 8.0, 2.0], [480.0, 5.0, 8.0, 2.0]]
        s.resize(300)
        self.assertEqual([i[0] for i in s.ice], [100.0])


class Cat(Base):
    def walk_to_sleep(self, sim):
        self.assertTrue(sim.start_cat())
        for _ in range(30 * 60):
            sim.step(1 / 30)
            if sim.cat["state"] == "sleep":
                return sim.cat
        self.fail("le chat ne s'est pas couché")

    def test_only_one_cat(self):
        sim = make()
        self.assertTrue(sim.start_cat())
        self.assertFalse(sim.start_cat())

    def test_walks_leaves_prints_and_sleeps(self):
        sim = make()
        self.walk_to_sleep(sim)
        self.assertTrue(sim.prints)

    def test_prints_fade(self):
        sim = make()
        self.walk_to_sleep(sim)
        sim.cat["sleep"] = 1000
        run(sim, 30)
        self.assertEqual(sim.prints, [])

    def test_moving_the_window_wakes_it(self):
        sim = make()
        c = self.walk_to_sleep(sim)
        sid = c["sid"]
        s = sim.surfaces[sid]
        s.x += 40
        sim.step(1 / 30)
        self.assertEqual(c["state"], "jump")
        self.assertEqual(c["from"], sid)

    def test_wakes_up_and_leaves_alone(self):
        sim = make()
        c = self.walk_to_sleep(sim)
        c["sleep"] = 0.01
        for _ in range(30 * 60):
            sim.step(1 / 30)
            if sim.cat is None:
                break
        self.assertIsNone(sim.cat)

    def test_closed_window_makes_it_fall_and_land(self):
        sim = make()
        s = sim.surfaces["1"]
        sim.cat = None
        sim.order = [o for o in sim.order if o.sid != "2"]
        sim.surfaces.pop("2")
        c = self.walk_to_sleep(sim)
        self.assertEqual(c["sid"] in ("1", "ground0"), True)
        c["sid"] = "1"
        c["x"], c["y"] = s.x + 50, s.y
        sim.apply({"w": [], "alive": []})
        sim._compute_masks()
        sim.step(1 / 30)
        self.assertEqual(c["state"], "jump")
        run(sim, 3)
        self.assertTrue(sim.cat is None or sim.cat["sid"] == "ground0")


class Painting(Base):
    def everything(self, season):
        sim = make()
        sim.set_season(season)
        sim.start_fight(force=True)
        sim.start_cat()
        sim.start_santa()
        sim.start_fireworks(4)
        sim.surfaces["1"].ice = [[100.0, 12.0, 12.0, 2.0]]
        sim.gifts.append({"sid": "2", "rel": 100.0, "lift": 3.0, "age": 0.0, "c": 1})
        sim.snowmen.append({"sid": "1", "rel": 200.0, "age": 12.0, "dir": 1, "melt": None,
                            "hat": "top", "scarf": (1, 2, 3), "headless": True})
        return sim

    def test_every_season_paints_every_feature(self):
        img = QImage(1440, 900, QImage.Format.Format_ARGB32_Premultiplied)
        view = QRectF(0, 0, 1440, 900)
        for season in (fun.WINTER, fun.SPRING, fun.AUTUMN):
            img.fill(0)
            sim = self.everything(season)
            run(sim, 3.5)
            p = QPainter(img)
            sim.paint(p, view)
            p.end()
            self.assertGreater(sum(img.pixelColor(x, 199).alpha() > 0
                                   for x in range(100, 600, 4)), 0)

    def test_quiet_sim_stays_cheap(self):
        sim = make()
        run(sim, 1)
        sim.take_damage()
        sim.flakes = []
        sim.step(1 / 30)
        sim.step(1 / 30)
        self.assertLess(len(sim.take_damage()[1]), 80)


if __name__ == "__main__":
    unittest.main()
