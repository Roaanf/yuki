#!/usr/bin/env python3
"""Mesure le coût de l'overlay Yuki sous Windows, avant d'écrire le vrai backend.

Affiche la vraie neige environ une minute, puis écrit yuki-probe-windows.txt.
Ctrl+C pour arrêter. Rien n'est installé ni modifié sur le système.

    py -m venv .venv
    .venv\\Scripts\\python -m pip install PyQt6
    .venv\\Scripts\\python tools\\windows_probe.py
"""

import argparse
import collections
import ctypes
import os
import signal
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PyQt6.QtCore import QRect, Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402

from yuki.core import Sim  # noqa: E402
from yuki.view import SnowView  # noqa: E402

WARMUP = 2.0
REPORT = "yuki-probe-windows.txt"
PDH_FMT_DOUBLE_NOCAP = 0x200 | 0x8000
PDH_MORE_DATA = 0x800007D2

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_TOOLWINDOW = 0x80
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_FLAGS = 0x1 | 0x2 | 0x10 | 0x20   # NOSIZE, NOMOVE, NOACTIVATE, FRAMECHANGED

Phase = collections.namedtuple("Phase", "name fps full snow")


class FmtValue(ctypes.Structure):
    _fields_ = [("status", wintypes.DWORD), ("value", ctypes.c_double)]


class FmtItem(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar_p), ("fmt", FmtValue)]


class NoCounters:
    """--dry-run : la boucle tourne, sans mesure système."""

    def sample(self):
        return {"dwm": None, "gpu": None}


class PdhCounters:
    """Compteurs de performance. Noms anglais : ils marchent sur un Windows français."""

    def __init__(self, pdh=None):
        self.pdh = pdh if pdh is not None else ctypes.WinDLL("pdh")
        self.query = wintypes.HANDLE()
        if self._status(self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query))):
            raise OSError("PdhOpenQueryW a échoué")
        self.dwm = self._add("\\Process(dwm)\\% Processor Time")
        self.gpu = self._add("\\GPU Engine(*engtype_3D)\\Utilization Percentage")
        self.pdh.PdhCollectQueryData(self.query)   # ces compteurs mesurent un écart entre 2 relevés

    @staticmethod
    def _status(code):
        return code & 0xFFFFFFFF

    def _add(self, path):
        counter = wintypes.HANDLE()
        code = self.pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(counter))
        return None if self._status(code) else counter

    def _single(self, counter):
        value = FmtValue()
        code = self.pdh.PdhGetFormattedCounterValue(counter, PDH_FMT_DOUBLE_NOCAP, None,
                                                    ctypes.byref(value))
        return None if self._status(code) or value.status else value.value

    def _total(self, counter):
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        code = self.pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE_NOCAP,
                                                     ctypes.byref(size), ctypes.byref(count), None)
        if self._status(code) != PDH_MORE_DATA:
            return None
        buf = ctypes.create_string_buffer(size.value)
        code = self.pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE_NOCAP,
                                                     ctypes.byref(size), ctypes.byref(count), buf)
        if self._status(code):
            return None
        items = ctypes.cast(buf, ctypes.POINTER(FmtItem))
        return sum(items[i].fmt.value for i in range(count.value) if not items[i].fmt.status)

    def sample(self):
        if self._status(self.pdh.PdhCollectQueryData(self.query)):
            return {"dwm": None, "gpu": None}
        dwm = self._single(self.dwm) if self.dwm else None
        return {"dwm": None if dwm is None else dwm / (os.cpu_count() or 1),
                "gpu": self._total(self.gpu) if self.gpu else None}


def style_overlay(view):
    """Hors d'Alt+Tab, jamais actif, au premier plan. Rend l'état réel de la fenêtre."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]
    hwnd = wintypes.HWND(int(view.winId()))
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
    user32.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0, SWP_FLAGS)
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    return "transparence %s, clics traversants %s" % (
        "oui" if style & WS_EX_LAYERED else "NON", "oui" if style & WS_EX_TRANSPARENT else "NON")


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def percentile(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def verdict(cpu, gpu):
    """Repères volontairement simples, en % de la machine et du GPU ajoutés par la neige."""
    if cpu is None or gpu is None:
        return "incomplet"
    if cpu <= 5 and gpu <= 15:
        return "OK"
    if cpu <= 10 and gpu <= 30:
        return "limite"
    return "trop cher"


def fmt(value, unit="%"):
    return "n/d" if value is None else "%.1f%s" % (value, unit)


class Probe:
    def __init__(self, app, args, counters):
        self.app, self.args, self.counters = app, args, counters
        self.rects = [s.geometry() for s in app.screens()]
        self.sim = Sim(self.rects, density=args.density, pile=0.35, melt=0.006)
        self.feed_windows()
        self.views = []
        self.lines = []
        self.results = []
        self.phases = collections.deque([
            Phase("Référence, sans neige", 0, False, False),
            Phase("30 i/s, rendu partiel", 30, False, True),
            Phase("60 i/s, rendu partiel", 60, False, True),
            Phase("30 i/s, tout redessiné", 30, True, True)])
        self.phase = None
        self.frame_timer = QTimer()
        self.frame_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.frame_timer.timeout.connect(self.tick)
        self.sample_timer = QTimer()
        self.sample_timer.timeout.connect(self.sample)
        self.phase_timer = QTimer()
        self.phase_timer.setSingleShot(True)
        self.phase_timer.timeout.connect(self.next_phase)

    def feed_windows(self):
        items = []
        for i, r in enumerate(self.rects):
            items.append(["win%d" % i, r.x() + r.width() // 4, r.y() + r.height() // 3,
                          r.width() // 2, r.height() // 3, 0, 0, 0])
            items.append(["bar%d" % i, r.x(), r.y() + r.height() - 48, r.width(), 48, 1, 0, 0])
        self.sim.apply({"w": items, "alive": [it[0] for it in items]})

    def log(self, text=""):
        print(text, flush=True)
        self.lines.append(text)

    def show_views(self):
        for scr, rect in zip(self.app.screens(), self.rects):
            v = SnowView(self.sim, QRect(rect))
            v.setScreen(scr)
            v.show()
            self.views.append(v)
            state = style_overlay(v) if sys.platform == "win32" else "non vérifié (--dry-run)"
            self.log("Écran %s : %dx%d à (%d, %d), échelle %g, %s" % (
                scr.name(), rect.width(), rect.height(), rect.x(), rect.y(),
                scr.devicePixelRatio(), state))
        self.log()

    def start(self):
        self.log("Sonde Yuki : Python %s, %d processeurs logiques" % (
            sys.version.split()[0], os.cpu_count() or 1))
        self.sample_timer.start(1000)
        self.next_phase()

    def begin_measure(self):
        self.t0, self.cpu0 = time.perf_counter(), time.process_time()
        self.frames = 0
        self.work = []
        self.samples = []
        self.measuring = True

    def next_phase(self):
        if self.phase is not None:
            self.finish_phase()
        if not self.phases:
            self.report()
            return
        self.phase = self.phases.popleft()
        print("… %s" % self.phase.name, flush=True)
        self.measuring = False
        self.frame_timer.stop()
        if self.phase.snow:
            if not self.views:
                self.show_views()
            self.sim.full = True
            if self.sim.santa is None:
                self.sim.start_santa()
            self.last = time.perf_counter()
            self.frame_timer.start(int(1000 / self.phase.fps))
        QTimer.singleShot(int(WARMUP * 1000), self.begin_measure)
        self.phase_timer.start(int((WARMUP + self.args.seconds) * 1000))

    def tick(self):
        now = time.perf_counter()
        dt, self.last = min(now - self.last, 0.1), now
        self.sim.step(dt)
        full, tiles = self.sim.take_damage()
        for v in self.views:
            v.render(full=full or self.phase.full, tiles=tiles)
        if self.measuring:
            self.frames += 1
            self.work.append((time.perf_counter() - now) * 1000)

    def sample(self):
        values = self.counters.sample()
        if self.phase is not None and self.measuring:
            self.samples.append(values)

    def finish_phase(self):
        wall = max(time.perf_counter() - self.t0, 1e-6)
        cores = os.cpu_count() or 1
        self.results.append({
            "phase": self.phase, "fps": self.frames / wall,
            "cpu": (time.process_time() - self.cpu0) / wall / cores * 100,
            "mean_ms": mean(self.work) or 0.0, "p95_ms": percentile(self.work, 0.95),
            "dwm": mean([s["dwm"] for s in self.samples]),
            "gpu": mean([s["gpu"] for s in self.samples])})

    def report(self):
        self.frame_timer.stop()
        self.sample_timer.stop()
        base = self.results[0]
        self.log("Référence : DWM %s, GPU 3D %s" % (fmt(base["dwm"]), fmt(base["gpu"])))
        self.log()
        for r in self.results[1:]:
            dwm = None if r["dwm"] is None or base["dwm"] is None else max(0.0, r["dwm"] - base["dwm"])
            gpu = None if r["gpu"] is None or base["gpu"] is None else max(0.0, r["gpu"] - base["gpu"])
            cpu = None if dwm is None else r["cpu"] + dwm
            self.log("%s : %s" % (r["phase"].name, verdict(cpu, gpu)))
            self.log("  images/s %.1f, calcul par image %.1f ms (95 %% sous %.1f ms)" % (
                r["fps"], r["mean_ms"], r["p95_ms"]))
            self.log("  CPU machine ajouté %s (Yuki %s, DWM %s), GPU ajouté %s" % (
                fmt(cpu), fmt(r["cpu"]), fmt(dwm), fmt(gpu)))
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines) + "\n")
        print("\nRapport écrit dans %s" % os.path.abspath(REPORT), flush=True)
        self.app.quit()


def main():
    ap = argparse.ArgumentParser(description="Mesure le coût de l'overlay Yuki sous Windows")
    ap.add_argument("--seconds", type=float, default=15.0, help="durée de chaque mesure (15)")
    ap.add_argument("--density", type=float, default=0.7, help="quantité de flocons (0.7)")
    ap.add_argument("--dry-run", action="store_true", help="vérifie la boucle, sans mesure système")
    args = ap.parse_args()
    if sys.platform != "win32" and not args.dry_run:
        sys.exit("Ce script se lance sous Windows (ou avec --dry-run pour vérifier la boucle).")
    app = QGuiApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    probe = Probe(app, args, NoCounters() if args.dry_run else PdhCounters())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    probe.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
