#!/usr/bin/env python3
"""Réglages du menu ❄. Lancer : python3 tests/test_settings.py"""

import argparse
import os
import sys
import tempfile
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PyQt6.QtCore import QRect, QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMenu  # noqa: E402

from yuki.core import Sim  # noqa: E402
from yuki.settings import PARAMS, Settings  # noqa: E402
from yuki.tray import add_settings_menu  # noqa: E402


def launch_args(**over):
    values = dict(density=1.0, wind=15.0, pile=1.0, melt=0.003, fps=30, over_max=0.0,
                  over_windows=1.0, plow_every=10.0, elves_every=6.0, santa_every=12.0)
    values.update(over)
    return argparse.Namespace(**values)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "yuki.conf")

    def store(self):
        return QSettings(self.path, QSettings.Format.IniFormat)

    def make(self, **over):
        s = Settings(launch_args(**over), self.store())
        sim = Sim([QRect(0, 0, 1920, 1080), QRect(1920, 0, 1280, 720)], density=s.values["density"])
        s.attach(sim)
        return s, sim


class Values(Base):
    def test_launch_values_are_clamped_and_applied(self):
        s, sim = self.make(over_max=3.0, over_windows=-1.0, plow_every=-5.0, fps=0)
        self.assertEqual((sim.over_max, sim.over_win, sim.plow_every), (1.0, 0.0, 0.0))
        self.assertEqual(s.frame_ms, 1000)

    def test_change_is_live_and_survives_restart(self):
        s, sim = self.make()
        s.set("wind", -60.0)
        s.set("over_windows", 0.3)
        s.set("fps", 60.0)
        self.assertEqual((sim.wind, sim.over_win, s.frame_ms), (-60.0, 0.3, 16))
        s2, sim2 = self.make(wind=5.0)
        self.assertEqual((sim2.wind, sim2.over_win, s2.frame_ms), (-60.0, 0.3, 16))
        self.assertCountEqual(s2.saved, ["wind", "over_windows", "fps"])

    def test_unchanged_options_follow_the_launch_values(self):
        s, _ = self.make()
        s.set("wind", 60.0)
        s.set("wind", 15.0)
        self.assertIsNone(self.store().value("wind"))
        _, sim = self.make(pile=0.35)
        self.assertEqual(sim.pile, 0.35)

    def test_reset(self):
        s, sim = self.make(melt=0.006)
        s.set("melt", 0.015)
        s.reset()
        self.assertEqual(sim.melt, 0.006)
        self.assertEqual(self.store().allKeys(), [])

    def test_corrupt_file_is_ignored(self):
        st = self.store()
        st.setValue("wind", "pas un nombre")
        st.setValue("pile", "nan")
        st.sync()
        s, sim = self.make()
        self.assertEqual((sim.wind, sim.pile, s.saved), (15.0, 1.0, []))

    def test_battery_choice_survives_restart_and_reset(self):
        s, _ = self.make()
        calls = []
        s.battery_listener = calls.append
        s.set_on_battery(True)
        s.set_on_battery(True)
        self.assertEqual(calls, [True])
        s2, _ = self.make()
        self.assertTrue(s2.on_battery)
        self.assertEqual(s2.saved, ["on_battery"])
        s2.battery_listener = calls.append
        s2.reset()
        self.assertFalse(s2.on_battery)
        self.assertEqual(calls, [True, False])
        self.assertEqual(self.store().allKeys(), [])

    def test_density_keeps_settled_snow(self):
        s, sim = self.make()
        ground = sim.grounds[0]
        ground.heights = [4.0] * len(ground.heights)
        for density in (2.5, 0.3, 1.0):
            s.set("density", density)
            for i, scr in enumerate(sim.screens):
                self.assertEqual(sum(f[7] == i for f in sim.flakes), sim._flake_count(scr))
        self.assertIs(sim.grounds[0], ground)
        self.assertEqual(ground.heights[0], 4.0)


class Menu(Base):
    def build(self, battery_option=False, **over):
        s, sim = self.make(**over)
        menu = QMenu()
        root = add_settings_menu(menu, s, battery_option)
        subs = {p.key: a.menu() for p, a in zip(PARAMS, root.actions())}
        return s, sim, menu, root, subs

    @staticmethod
    def checked(sub):
        return [a.text() for a in sub.actions() if a.isChecked()]

    def test_every_setting_has_one_checked_choice(self):
        _, _, _menu, _root, subs = self.build()
        self.assertEqual(len(subs), len(PARAMS))
        for sub in subs.values():
            self.assertEqual(len(self.checked(sub)), 1)

    def test_click_applies_and_stays_exclusive(self):
        s, sim, _menu, _root, subs = self.build()
        next(a for a in subs["density"].actions() if a.text() == "Tempête").trigger()
        self.assertEqual(sim.density, 2.5)
        self.assertEqual(self.checked(subs["density"]), ["Tempête"])

    def test_battery_entry_only_when_supported(self):
        _, _, _menu, root, _subs = self.build()
        self.assertFalse(any("batterie" in a.text() for a in root.actions()))

    def test_battery_entry_toggles_and_reset_unchecks(self):
        s, _, _menu, root, _subs = self.build(battery_option=True)
        entry = next(a for a in root.actions() if "batterie" in a.text())
        self.assertFalse(entry.isChecked())
        entry.trigger()
        self.assertTrue(s.on_battery)
        root.actions()[-1].trigger()
        self.assertFalse(entry.isChecked())
        self.assertFalse(s.on_battery)

    def test_custom_launch_value_is_listed_and_reset_refreshes(self):
        s, sim, _menu, root, subs = self.build(density=0.85)
        self.assertEqual(self.checked(subs["density"]), ["Personnalisé (0.85)"])
        next(a for a in subs["density"].actions() if a.text() == "Peu").trigger()
        root.actions()[-1].trigger()
        self.assertEqual(sim.density, 0.85)
        self.assertEqual(self.checked(subs["density"]), ["Personnalisé (0.85)"])
        next(a for a in subs["density"].actions() if a.text() == "Peu").trigger()
        self.assertEqual(self.checked(subs["density"]), ["Peu"])


if __name__ == "__main__":
    unittest.main()
