#!/usr/bin/env python3
"""Backend Windows sans Windows : données simulées et fausses DLL Win32.

Lancer : python3 tests/test_windows_backend.py
"""

import argparse
import ctypes
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PyQt6.QtCore import QRect, QStandardPaths  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from yuki.backends import windows  # noqa: E402
from yuki.backends.windows import (Row, Screen, Win32, build_payload, match_screens,  # noqa: E402
                                   overlay_needs_raise, taskbar_rect, to_logical)
from yuki.core import Sim  # noqa: E402

PID = 4242
MAIN = Screen("D1", (0, 0, 1920, 1080), (0, 0, 1920, 1032), (0, 0, 1920, 1080), 1.0)
HIDPI = Screen("D2", (1920, 0, 3840, 2160), (1920, 0, 3840, 2088), (1920, 0, 2560, 1440), 1.5)


def win(hwnd, x=100, y=100, w=400, h=300, **kw):
    kw.setdefault("cls", "Notepad")
    kw.setdefault("pid", 7)
    return Row(hwnd, visible=True, rect=(x, y, w, h), **kw)


BAR = win(90, 0, 1032, 1920, 48, cls="Shell_TrayWnd")


class Payload(unittest.TestCase):
    def ids(self, rows, screens=(MAIN,)):
        return [it[0] for it in build_payload(rows, list(screens), PID)["w"]]

    def test_bottom_to_top_with_taskbar_last(self):
        self.assertEqual(self.ids([win(1), win(2), BAR]), ["2", "1", "taskbar:D1"])

    def test_filters(self):
        rejected = [
            Row(1), win(2, iconic=True), win(3, cloaked=True), win(4, pid=PID), win(5, w=49),
            win(6, cls="Progman"), win(7, exstyle=windows.WS_EX_TRANSPARENT),
            win(8, exstyle=windows.WS_EX_TOOLWINDOW), win(9, exstyle=windows.WS_EX_NOACTIVATE),
            win(10, -32000, -32000)]
        self.assertEqual(self.ids(rejected), [])

    def test_app_window_flag_wins_over_tool_window(self):
        ex = windows.WS_EX_TOOLWINDOW | windows.WS_EX_APPWINDOW
        self.assertEqual(self.ids([win(1, exstyle=ex)]), ["1"])

    def test_hidden_windows_stay_alive(self):
        p = build_payload([Row(1), win(2, iconic=True), win(3, cloaked=True)], [MAIN], PID)
        self.assertEqual(set(p["alive"]), {"1", "2", "3", "taskbar:D1"})

    def test_maximized_and_fullscreen(self):
        rows = [win(1, 0, 0, 1920, 1032, zoomed=True, style=windows.WS_CAPTION),
                win(2, 0, 0, 1920, 1080),
                win(3, 0, 0, 1920, 1080, zoomed=True, style=windows.WS_CAPTION), win(4)]
        flags = {it[0]: it[6:8] for it in build_payload(rows, [MAIN], PID)["w"]}
        self.assertEqual(flags, {"1": [0, 1], "2": [1, 0], "3": [0, 1], "4": [0, 0]})

    def test_debug_fields(self):
        p = build_payload([win(1, title="Doc")], [MAIN], PID, debug=True)
        self.assertEqual(p["w"][0][8:], ["Doc", "Notepad"])


class Dpi(unittest.TestCase):
    def test_conversion_on_scaled_screen(self):
        self.assertEqual(to_logical((2220, 150, 900, 600), HIDPI), (2120, 100, 600, 400))

    def test_window_uses_screen_it_mostly_covers(self):
        p = build_payload([win(1, 1900, 150, 900, 600)], [MAIN, HIDPI], PID)
        self.assertEqual(p["w"][0][1:5], [1907, 100, 600, 400])

    def test_negative_origin(self):
        s = Screen("L", (-2560, -200, 2560, 1440), (-2560, -200, 2560, 1392),
                    (-2560, -200, 1707, 960), 1.5)
        self.assertEqual(to_logical((-2260, -50, 300, 300), s), (-2360, -100, 200, 200))

    def test_match_screens_computes_scale(self):
        out = match_screens([("D2", HIDPI.frame, HIDPI.work)], [(0, 0, 1920, 1080), (1920, 0, 2560, 1440)])
        self.assertEqual(out[0].logical, (1920, 0, 2560, 1440))
        self.assertEqual(out[0].scale, 1.5)


class Taskbar(unittest.TestCase):
    def test_visible(self):
        self.assertEqual(taskbar_rect([BAR], MAIN), BAR.rect)

    def test_auto_hidden(self):
        self.assertIsNone(taskbar_rect([win(90, 0, 1078, 1920, 48, cls="Shell_TrayWnd")], MAIN))
        self.assertIsNone(taskbar_rect([win(90, 0, 1078, 1920, 2, cls="Shell_TrayWnd")], MAIN))

    def test_top_or_side(self):
        self.assertIsNone(taskbar_rect([win(90, 0, 0, 1920, 48, cls="Shell_TrayWnd")], MAIN))
        self.assertIsNone(taskbar_rect([win(90, 0, 0, 62, 1080, cls="Shell_TrayWnd")], MAIN))

    def test_secondary_screen_is_scaled(self):
        bar = win(91, 1920, 2088, 3840, 72, cls="Shell_SecondaryTrayWnd")
        it = build_payload([BAR, bar], [MAIN, HIDPI], PID)["w"]
        self.assertEqual(it[1], ["taskbar:D2", 1920, 1392, 2560, 48, 1, 0, 0])

    def test_raise_needed_only_when_bar_is_in_front(self):
        self.assertTrue(overlay_needs_raise([BAR, win(5)], {5}))
        self.assertFalse(overlay_needs_raise([win(5), BAR], {5}))


class FakeApi:
    def __init__(self, debug=False):
        self.rows = [win(1), BAR]
        self.battery = False
        self.styled, self.raised = [], []
        self.notify = None
        self.stopped = False

    def snapshot(self):
        return self.rows

    def monitors(self):
        return [("D1", MAIN.frame, MAIN.work)]

    def style_overlay(self, hwnd):
        self.styled.append(hwnd)

    def raise_overlay(self, hwnd):
        self.raised.append(hwnd)

    def on_battery(self):
        return self.battery

    def start_hooks(self, notify):
        self.notify = notify

    def stop_hooks(self):
        self.stopped = True


class Backend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (mock.patch.object(windows, "Win32", FakeApi),
                  mock.patch.object(QStandardPaths, "writableLocation", return_value=self.tmp.name)):
            p.start()
            self.addCleanup(p.stop)
        self.b = windows.WindowsBackend(self.app, argparse.Namespace(debug=False, on_battery=False))
        self.addCleanup(self.b.lock.unlock)
        self.addCleanup(self.b.stop)
        g = self.app.primaryScreen().geometry()
        self.sim = Sim([g])
        self.b.attach(self.sim)
        self.paused = []
        self.b.set_paused = self.set_paused
        self.b.screens = [MAIN]

    def set_paused(self, value):
        self.sim.user_paused = value
        self.paused.append(value)

    def test_second_instance_refused(self):
        with self.assertRaises(SystemExit):
            windows.WindowsBackend(self.app, argparse.Namespace(debug=False, on_battery=False))

    def test_minimized_sleeps_and_closed_falls(self):
        self.b.scan()
        s = self.sim.surfaces["1"]
        s.heights = [5.0] * len(s.heights)
        s.mask = [True] * len(s.heights)
        self.b.api.rows = [win(1, iconic=True), BAR]
        self.b.scan()
        self.assertIn("1", self.sim.dormant)
        self.assertEqual(self.sim.clumps, [])
        self.b.api.rows = [win(2), BAR]
        self.b.scan()
        self.assertNotIn("1", self.sim.dormant)

    def test_snapshot_failure_drops_nothing(self):
        self.b.scan()
        self.b.api.rows = None
        self.b.scan()
        self.assertIn("1", self.sim.surfaces)

    def test_events_are_coalesced(self):
        self.b.request_scan()
        first = self.b.scan_timer.remainingTime()
        self.b.request_scan()
        self.assertTrue(self.b.scan_timer.isActive())
        self.assertLessEqual(self.b.scan_timer.remainingTime(), first)
        self.assertEqual(self.b.scan_timer.interval(), windows.SCAN_DELAY_MS)

    def test_scan_error_does_not_escape(self):
        self.b.api.snapshot = mock.Mock(side_effect=OSError("boum"))
        with mock.patch("sys.stderr"):
            self.b.scan()
            self.b.safety_poll()

    def test_overlay_hidden_when_paused_or_fullscreen(self):
        rect = QRect(0, 0, 640, 480)
        self.sim.set_screens([rect])
        v = self.b.create_views(self.sim, [rect])[0]
        self.addCleanup(v.dispose)
        v.show()
        self.b.view_shown(v)
        self.assertTrue(self.b.api.styled)
        self.sim.user_paused = True
        self.b.safety_poll()
        self.assertFalse(v.isVisible())
        self.sim.user_paused = False
        self.b.safety_poll()
        self.assertTrue(v.isVisible())
        self.b.api.rows = [win(3, 0, 0, 1920, 1080), BAR]
        self.b.scan()
        self.assertFalse(v.isVisible())

    def test_overlay_goes_back_above_taskbar(self):
        rect = QRect(0, 0, 640, 480)
        v = self.b.create_views(self.sim, [rect])[0]
        self.addCleanup(v.dispose)
        v.show()
        hwnd = int(v.winId())
        self.b.api.rows = [BAR, win(hwnd, pid=PID), win(1)]
        self.b.safety_poll()
        self.assertEqual(self.b.api.raised, [hwnd])

    def test_battery_pause_cycle(self):
        self.b.start(self.sim, self.set_paused)
        self.b.api.battery = True
        self.b._power_check()
        self.b.api.battery = False
        self.b._power_check()
        self.assertEqual(self.paused, [True, False])

    def test_start_installs_hooks_and_stop_removes_them(self):
        self.b.start(self.sim, self.set_paused)
        self.b.api.notify()
        self.assertTrue(self.b.scan_timer.isActive())
        self.b.stop()
        self.assertTrue(self.b.api.stopped)
        self.assertFalse(self.b.lock.isLocked())


class Fn:
    """Fausse fonction de DLL : accepte argtypes et restype."""

    def __init__(self, impl):
        self.impl = impl

    def __call__(self, *args):
        return self.impl(*args)


class FakeDll:
    def __init__(self, **funcs):
        self.__dict__.update({k: Fn(v) for k, v in funcs.items()})

    def __getattr__(self, name):
        fn = Fn(lambda *a: 0)
        self.__dict__[name] = fn
        return fn


class Win32Layer(unittest.TestCase):
    def make(self):
        def enum_windows(cb, param):
            for h in (11, 12):
                cb(h, param)
            return 1

        def text(h, buf, n):
            buf.value = "Notepad"
            return 7

        def long_ptr(h, index):
            return 0x10 if index == windows.GWL_EXSTYLE else windows.WS_CAPTION

        def pid(h, out):
            out._obj.value = 77
            return 1

        def dwm(h, attr, ptr, size):
            if attr == windows.DWMWA_EXTENDED_FRAME_BOUNDS:
                r = ptr._obj
                r.left, r.top, r.right, r.bottom = 10, 20, 410, 320
            else:
                ptr._obj.value = 2 if h == 12 else 0
            return 0

        def enum_monitors(dc, rect, cb, param):
            cb(1, None, None, param)
            return 1

        def monitor_info(handle, ptr):
            m = ptr._obj
            m.rcMonitor.right, m.rcMonitor.bottom = 1920, 1080
            m.rcWork.right, m.rcWork.bottom = 1920, 1032
            m.szDevice = "D1"
            return 1

        def power(ptr):
            ptr._obj.ACLineStatus = 0
            return 1
        self.set_pos = []
        user32 = FakeDll(EnumWindows=enum_windows, IsWindowVisible=lambda h: 1,
                         GetClassNameW=text, GetWindowLongPtrW=long_ptr,
                         GetWindowThreadProcessId=pid, EnumDisplayMonitors=enum_monitors,
                         GetMonitorInfoW=monitor_info,
                         SetWindowPos=lambda *a: self.set_pos.append(a) or 1)
        return Win32(False, user32, FakeDll(DwmGetWindowAttribute=dwm),
                     FakeDll(GetSystemPowerStatus=power))

    def test_snapshot(self):
        rows = self.make().snapshot()
        self.assertEqual([r.hwnd for r in rows], [11, 12])
        self.assertEqual(rows[0].rect, (10, 20, 400, 300))
        self.assertEqual((rows[0].pid, rows[0].cls, rows[0].exstyle), (77, "Notepad", 0x10))
        self.assertEqual((rows[0].cloaked, rows[1].cloaked), (False, True))

    def test_monitors_and_power(self):
        api = self.make()
        self.assertEqual(api.monitors(), [("D1", (0, 0, 1920, 1080), (0, 0, 1920, 1032))])
        self.assertTrue(api.on_battery())

    def test_hook_filters_noise(self):
        api = self.make()
        calls = []
        api.user32.GetAncestor = Fn(lambda h, flag: 5)
        api.start_hooks(lambda: calls.append(1))
        self.assertEqual(len(api.hooks), len(windows.EVENT_RANGES))
        api.callback(None, windows.EVENT_OBJECT_LOCATIONCHANGE, 5, -9, 0, 0, 0)   # curseur
        api.callback(None, windows.EVENT_OBJECT_LOCATIONCHANGE, 6, 0, 0, 0, 0)    # enfant
        self.assertEqual(calls, [])
        api.callback(None, windows.EVENT_OBJECT_LOCATIONCHANGE, 5, 0, 0, 0, 0)
        self.assertEqual(calls, [1])

    def test_structures_have_win32_layout_on_64_bits(self):
        self.assertEqual(ctypes.sizeof(windows.PowerStatus), 12)


if __name__ == "__main__":
    unittest.main()
