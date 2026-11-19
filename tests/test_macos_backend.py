#!/usr/bin/env python3
"""Backend macOS sans Mac : données CGWindow simulées et faux modules pyobjc.

Lancer : python3 tests/test_macos_backend.py
"""

import argparse
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PyQt6.QtCore import QRect, QSettings, QStandardPaths  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from yuki.backends import macos  # noqa: E402
from yuki.backends.macos import (ScreenInfo, build_payload, corner_radii, dock_rect, flip_rect,  # noqa: E402
                                 parse_on_battery)
from yuki.core import Sim  # noqa: E402

PID = 4242
# Écran 1440x900, barre des menus de 25 pt, Dock de 70 pt
MAIN = ScreenInfo(1, (0, 0, 1440, 900), (0, 25, 1440, 805))


def win(num, x=100, y=100, w=400, h=300, layer=0, alpha=1.0, pid=7, owner="App", name=None):
    info = {"kCGWindowNumber": num, "kCGWindowLayer": layer, "kCGWindowAlpha": alpha,
            "kCGWindowOwnerPID": pid, "kCGWindowOwnerName": owner,
            "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h}}
    if name:
        info["kCGWindowName"] = name
    return info


DOCK = win(50, 400, 825, 640, 75, layer=20, owner="Dock")


class Payload(unittest.TestCase):
    def ids(self, payload):
        return [it[0] for it in payload["w"]]

    def test_order_is_reversed_and_dock_is_on_top(self):
        p = build_payload([win(1), win(2), DOCK], [1, 2], [MAIN], PID)
        self.assertEqual(self.ids(p), ["2", "1", "dock:1"])

    def test_filters(self):
        rejected = [win(1, layer=25), win(2, alpha=0), win(3, w=49), win(4, h=49),
                    win(5, pid=PID), win(6, owner="Window Server")]
        p = build_payload(rejected + [win(7), DOCK], [], [MAIN], PID)
        self.assertEqual(self.ids(p), ["7", "dock:1"])

    def test_item_format(self):
        p = build_payload([win(9, 10.4, 20.6, 300, 200)], [9], [], PID)
        self.assertEqual(p["w"], [["9", 10, 21, 300, 200, 0, 0, 0]])

    def test_debug_adds_title_and_owner(self):
        p = build_payload([win(9, name="Doc", owner="Pages")], [9], [], PID, debug=True)
        self.assertEqual(p["w"][0][8:], ["Doc", "Pages"])

    def test_alive_keeps_hidden_windows_and_docks(self):
        p = build_payload([win(1)], [1, 2], [MAIN], PID)
        self.assertEqual(set(p["alive"]), {"1", "2", "dock:1"})

    def test_maximized_and_fullscreen(self):
        p = build_payload([win(1, 0, 25, 1440, 805), win(2, 1, 1, 1439, 899), win(3)],
                          [], [MAIN], PID)
        flags = {it[0]: it[6:8] for it in p["w"]}
        self.assertEqual(flags["1"], [0, 1])
        self.assertEqual(flags["2"], [1, 0])
        self.assertEqual(flags["3"], [0, 0])


class Dock(unittest.TestCase):
    def test_no_surface_when_width_is_unknown(self):
        self.assertIsNone(dock_rect(MAIN, []))

    def test_uses_dock_window_width(self):
        self.assertEqual(dock_rect(MAIN, [DOCK]), (400, 830, 640, 70))

    def test_full_screen_dock_window_says_nothing_about_width(self):
        d = win(50, 0, 0, 1440, 900, layer=20, owner="Dock")
        self.assertIsNone(dock_rect(MAIN, [d]))

    def test_accessibility_frame_gives_width(self):
        self.assertEqual(dock_rect(MAIN, [], [(500, 838, 440, 58)]), (500, 830, 440, 70))

    def test_accessibility_frame_of_another_screen_is_ignored(self):
        self.assertIsNone(dock_rect(MAIN, [], [(1600, 838, 440, 58)]))

    def test_corner_radii_by_version(self):
        self.assertEqual(corner_radii("26.1"), (24.0, 21.0))
        self.assertEqual(corner_radii("16.0"), (24.0, 21.0))
        self.assertEqual(corner_radii("15.6.1"), (10.0, 16.0))
        self.assertEqual(corner_radii("10.16"), (10.0, 16.0))
        self.assertEqual(corner_radii("10.15.7"), (5.0, 0.0))
        self.assertEqual(corner_radii(""), (10.0, 16.0))

    def test_hidden_dock_small_inset(self):
        self.assertIsNone(dock_rect(ScreenInfo(1, (0, 0, 1440, 900), (0, 25, 1440, 871)), []))

    def test_side_dock(self):
        self.assertIsNone(dock_rect(ScreenInfo(1, (0, 0, 1440, 900), (70, 25, 1370, 875)), []))

    def test_screen_above_primary(self):
        s = ScreenInfo(2, (0, -1080, 1920, 1080), (0, -1055, 1920, 985))
        d = win(50, 400, -75, 640, 75, layer=20, owner="Dock")
        self.assertEqual(dock_rect(s, [d]), (400, -70, 640, 70))


class Helpers(unittest.TestCase):
    def test_flip(self):
        self.assertEqual(flip_rect((0, 0, 1440, 900), 900), (0, 0, 1440, 900))
        self.assertEqual(flip_rect((0, 70, 1440, 805), 900), (0, 25, 1440, 805))
        self.assertEqual(flip_rect((1440, 900, 1920, 1080), 900), (1440, -1080, 1920, 1080))

    def test_pmset(self):
        self.assertTrue(parse_on_battery("Now drawing from 'Battery Power'\n -InternalBattery-0"))
        self.assertFalse(parse_on_battery("Now drawing from 'AC Power'\n"))
        self.assertIsNone(parse_on_battery(""))


class Point:
    def __init__(self, x, y):
        self.x, self.y = x, y


class Rect:
    def __init__(self, x, y, w, h):
        self.origin = Point(x, y)
        self.size = types.SimpleNamespace(width=w, height=h)


class FakeScreen:
    def frame(self):
        return Rect(0, 0, 1440, 900)

    def visibleFrame(self):
        return Rect(0, 70, 1440, 805)

    def deviceDescription(self):
        return {"NSScreenNumber": 1}


class FakeWindow:
    def __init__(self):
        self.calls = {}

    def __getattr__(self, name):
        def record(*args):
            self.calls[name] = args
        return record


class Cocoa:
    """Faux AppKit, Foundation, objc et Quartz, installés dans sys.modules."""

    def __init__(self):
        self.onscreen = []
        self.all = []
        self.all_queries = 0
        self.window = FakeWindow()
        self.policy = None
        self.trusted = False
        self.prompts = []
        self.dock_list = (500, 838, 440, 58)
        self.ended = []
        cocoa = self

        def copy_info(options, _relative):
            if options == 0:
                cocoa.all_queries += 1
                return cocoa.all
            return cocoa.onscreen

        class Pool:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def copy_attribute(element, attribute, _out):
            if attribute == "AXChildren":
                return 0, ["menu", "list"]
            if attribute == "AXRole":
                return 0, "AXList" if element == "list" else "AXMenu"
            x, y, w, h = cocoa.dock_list
            if attribute == "AXPosition":
                return 0, ("point", types.SimpleNamespace(x=x, y=y))
            return 0, ("size", types.SimpleNamespace(width=w, height=h))

        process = types.SimpleNamespace(
            beginActivityWithOptions_reason_=lambda *_: "token",
            endActivity_=self.ended.append)
        self.modules = {
            "AppKit": types.SimpleNamespace(
                NSApplication=types.SimpleNamespace(sharedApplication=lambda: types.SimpleNamespace(
                    setActivationPolicy_=lambda p: setattr(cocoa, "policy", p))),
                NSApplicationActivationPolicyAccessory=1,
                NSScreen=types.SimpleNamespace(screens=lambda: [FakeScreen()]),
                NSRunningApplication=types.SimpleNamespace(
                    runningApplicationsWithBundleIdentifier_=lambda _b: [
                        types.SimpleNamespace(processIdentifier=lambda: 88)]),
                NSColor=types.SimpleNamespace(clearColor=lambda: "clear"),
                NSWindowCollectionBehaviorCanJoinAllSpaces=1,
                NSWindowCollectionBehaviorStationary=16,
                NSWindowCollectionBehaviorIgnoresCycle=64),
            "Foundation": types.SimpleNamespace(
                NSProcessInfo=types.SimpleNamespace(processInfo=lambda: process),
                NSActivityUserInitiatedAllowingIdleSystemSleep=15728639),
            "objc": types.SimpleNamespace(
                autorelease_pool=Pool,
                objc_object=lambda c_void_p: types.SimpleNamespace(window=lambda: cocoa.window)),
            "Quartz": types.SimpleNamespace(
                CGWindowListCopyWindowInfo=copy_info, kCGNullWindowID=0, kCGMainMenuWindowLevel=24,
                kCGWindowListOptionAll=0, kCGWindowListOptionOnScreenOnly=1,
                kCGWindowListExcludeDesktopElements=16),
            "ApplicationServices": types.SimpleNamespace(
                AXIsProcessTrusted=lambda: cocoa.trusted,
                AXIsProcessTrustedWithOptions=self.prompts.append,
                kAXTrustedCheckOptionPrompt="prompt",
                AXUIElementCreateApplication=lambda pid: "app",
                AXUIElementCopyAttributeValue=copy_attribute,
                AXValueGetValue=lambda value, _kind, _out: (True, value[1]),
                kAXValueCGPointType=1, kAXValueCGSizeType=2),
        }


class Backend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.cocoa = Cocoa()
        self.tmp = tempfile.TemporaryDirectory()
        ini = os.path.join(self.tmp.name, "yuki-macos.ini")
        patches = [mock.patch.dict(sys.modules, self.cocoa.modules),
                   mock.patch.object(QStandardPaths, "writableLocation",
                                     return_value=self.tmp.name),
                   mock.patch.object(macos, "QSettings",
                                     lambda *_: QSettings(ini, QSettings.Format.IniFormat))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.backend = self.make_backend()
        self.addCleanup(self.backend.lock.unlock)
        self.sim = Sim([QRect(0, 0, 1440, 900)])
        self.backend.attach(self.sim)
        self.paused = []
        self.backend.set_paused = self.set_paused

    def make_backend(self):
        return macos.MacBackend(self.app, argparse.Namespace(debug=False, on_battery=False))

    def set_paused(self, value):
        self.sim.user_paused = value
        self.paused.append(value)

    def snow_on(self, sid):
        s = self.sim.surfaces[sid]
        s.heights = [5.0] * len(s.heights)
        return s

    def test_accessory_app_and_lock(self):
        self.assertEqual(self.cocoa.policy, 1)
        with self.assertRaises(SystemExit):
            self.make_backend()

    def test_minimized_sleeps_and_closed_falls(self):
        self.cocoa.onscreen = [win(1), win(2, 600), DOCK]
        self.cocoa.all = [win(1), win(2, 600), DOCK]
        self.backend.poll()
        self.assertIn("dock:1", self.sim.surfaces)
        self.snow_on("1")
        self.snow_on("2")
        self.sim._compute_masks()

        self.cocoa.onscreen = [win(2, 600)]    # 1 réduite : toujours dans la liste complète
        self.backend.poll()
        self.assertIn("1", self.sim.dormant)
        self.assertEqual(self.sim.clumps, [])

        self.cocoa.onscreen = []               # 2 fermée : absente partout
        self.cocoa.all = [win(1)]
        self.backend.poll()
        self.assertNotIn("2", self.sim.surfaces)
        self.assertNotIn("2", self.sim.dormant)
        self.assertGreater(len(self.sim.clumps), 0)

    def test_window_server_failure_drops_nothing(self):
        self.cocoa.onscreen = self.cocoa.all = [win(1)]
        self.backend.poll()
        self.snow_on("1")
        self.cocoa.onscreen = None
        self.backend.poll()
        self.assertIn("1", self.sim.surfaces)
        self.assertEqual(self.sim.clumps, [])

    def test_full_list_is_not_read_every_poll(self):
        self.cocoa.onscreen = self.cocoa.all = [win(1)]
        for _ in range(5):
            self.backend.poll()
        self.assertEqual(self.cocoa.all_queries, 1)

    def test_apply_only_on_change_and_faster_while_moving(self):
        self.cocoa.onscreen = self.cocoa.all = [win(1)]
        self.backend.poll()
        with mock.patch.object(self.sim, "apply") as apply:
            self.backend.poll()
            apply.assert_not_called()
            self.assertEqual(self.backend.poll_timer.interval(), macos.POLL_IDLE_MS)
            self.cocoa.onscreen = [win(1, 130)]
            self.backend.poll()
            apply.assert_called_once()
        self.assertEqual(self.backend.poll_timer.interval(), macos.POLL_MOVING_MS)

    def test_poll_error_does_not_escape(self):
        self.cocoa.onscreen = [{"kCGWindowLayer": 0}]
        with mock.patch("sys.stderr"):
            self.backend.poll()

    def test_battery_pause_cycle(self):
        self.backend.power_changed(False)
        self.backend.power_changed(True)
        self.backend.power_changed(True)
        self.backend.power_changed(None)
        self.backend.power_changed(False)
        self.assertEqual(self.paused, [True, False])

    def test_manual_pause_survives_power_changes(self):
        self.sim.user_paused = True
        self.backend.power_changed(True)
        self.backend.power_changed(False)
        self.assertEqual(self.paused, [])
        self.assertTrue(self.sim.user_paused)

    def test_snow_on_battery_lifts_the_power_pause(self):
        self.backend.power_changed(False)
        self.backend.power_changed(True)
        self.backend.set_run_on_battery(True)
        self.assertEqual(self.paused, [True, False])
        self.assertFalse(self.backend.power_timer.isActive())
        self.backend.power_changed(True)       # réponse tardive de pmset : ignorée
        self.assertEqual(self.paused, [True, False])

    def test_unchecking_watches_power_again(self):
        self.backend.set_run_on_battery(True)
        self.backend.set_run_on_battery(False)
        self.addCleanup(self.backend.power_timer.stop)
        self.assertTrue(self.backend.power_timer.isActive())
        self.backend.power_changed(True)
        self.assertEqual(self.paused, [True])

    def test_start_honours_saved_choice(self):
        self.backend.run_on_battery = True
        self.backend.start(self.sim, self.set_paused)
        self.addCleanup(self.backend.poll_timer.stop)
        self.assertFalse(self.backend.power_timer.isActive())

    def start(self):
        with mock.patch("sys.stderr"):
            self.backend.start(self.sim, self.set_paused)
        self.addCleanup(self.backend.poll_timer.stop)
        self.addCleanup(self.backend.power_timer.stop)

    def test_accessibility_is_asked_only_once(self):
        self.start()
        self.backend.stop()
        self.backend.lock.lock()
        self.start()
        self.assertEqual(len(self.cocoa.prompts), 1)
        self.assertNotIn("dock:1", self.sim.surfaces)   # pas autorisé : largeur inconnue

    def test_dock_width_from_accessibility(self):
        self.cocoa.trusted = True
        self.start()
        self.assertEqual(self.cocoa.prompts, [])
        dock = self.sim.surfaces["dock:1"]
        self.assertEqual((dock.x, dock.y, dock.w), (500, 830, 440))
        self.assertEqual(dock.radius, self.sim.dock_radius)

    def test_overlay_window_setup(self):
        views = self.backend.create_views(self.sim, [QRect(0, 0, 640, 480)])
        calls = self.cocoa.window.calls
        self.assertEqual(calls["setLevel_"], (23,))
        self.assertEqual(calls["setCollectionBehavior_"], (1 | 16 | 64,))
        self.assertEqual(calls["setIgnoresMouseEvents_"], (True,))
        self.assertEqual(calls["setOpaque_"], (False,))
        self.assertEqual(calls["setHasShadow_"], (False,))
        self.assertEqual(calls["setBackgroundColor_"], ("clear",))
        self.backend.view_shown(views[0])
        views[0].dispose()

    def test_stop_releases_everything(self):
        self.backend.start(self.sim, self.set_paused)
        self.backend.stop()
        self.assertFalse(self.backend.poll_timer.isActive())
        self.assertEqual(self.cocoa.ended, ["token"])
        self.assertFalse(self.backend.lock.isLocked())


if __name__ == "__main__":
    unittest.main()
