"""Backend macOS : fenêtres lues avec Quartz, overlay réglé avec AppKit, Dock via NSScreen.

Les fonctions du haut travaillent sur de simples dictionnaires : elles se testent sans Mac.
"""

import collections
import ctypes
import os
import sys
import time

from PyQt6.QtCore import QProcess, QRect, QTimer

from ..core import WindowFeed
from ..view import SnowView
from .common import BatteryPauseMixin, acquire_instance_lock

POLL_IDLE_MS = 33
POLL_MOVING_MS = 16       # une fenêtre bouge : la neige doit suivre de près
POLL_PAUSED_MS = 1000
MOVING_HOLD = 0.4         # s à garder le rythme rapide après le dernier mouvement
ALIVE_EVERY = 2.0         # s entre deux relectures de la liste complète (coûteuse)
SCREENS_EVERY = 1.0

MIN_WINDOW = 50
RECT_TOLERANCE = 2
DOCK_MIN_INSET = 8        # Dock masqué : visibleFrame garde quand même ~4 pt en bas
DOCK_LAYER = 20           # kCGDockWindowLevel
SYSTEM_OWNERS = {"Window Server", "Dock"}

K_NUMBER = "kCGWindowNumber"
K_LAYER = "kCGWindowLayer"
K_BOUNDS = "kCGWindowBounds"
K_ALPHA = "kCGWindowAlpha"
K_PID = "kCGWindowOwnerPID"
K_OWNER = "kCGWindowOwnerName"
K_NAME = "kCGWindowName"

# frame et visible : (x, y, w, h) en points, origine en haut à gauche de l'écran principal
ScreenInfo = collections.namedtuple("ScreenInfo", "key frame visible")


def flip_rect(rect, primary_height):
    """Repère Cocoa (origine en bas à gauche) -> repère CG/Qt (origine en haut à gauche)."""
    x, y, w, h = rect
    return (int(round(x)), int(round(primary_height - y - h)), int(round(w)), int(round(h)))


def window_rect(info):
    b = info.get(K_BOUNDS)
    if not b:
        return None
    return (int(round(float(b["X"]))), int(round(float(b["Y"]))),
            int(round(float(b["Width"]))), int(round(float(b["Height"]))))


def same_rect(a, b):
    return all(abs(p - q) <= RECT_TOLERANCE for p, q in zip(a, b))


def holds_snow(info, own_pid):
    if int(info.get(K_LAYER, -1)) != 0 or float(info.get(K_ALPHA, 1)) <= 0:
        return False
    if int(info.get(K_PID, -1)) == own_pid or str(info.get(K_OWNER, "")) in SYSTEM_OWNERS:
        return False
    r = window_rect(info)
    return r is not None and r[2] >= MIN_WINDOW and r[3] >= MIN_WINDOW


def dock_rect(screen, onscreen):
    """Partie visible du Dock en bas de cet écran, ou None (masqué, sur le côté, largeur inconnue).

    Sans largeur mesurée, pas de surface : la neige flotterait de chaque côté du Dock.
    """
    fx, fy, fw, fh = screen.frame
    vx, vy, vw, vh = screen.visible
    bottom = fy + fh
    inset = bottom - (vy + vh)
    if inset <= DOCK_MIN_INSET:
        return None
    top = bottom - inset
    best = None
    for info in onscreen:
        if str(info.get(K_OWNER, "")) != "Dock" or int(info.get(K_LAYER, -1)) != DOCK_LAYER:
            continue
        r = window_rect(info)
        if r is None or not (100 <= r[2] < fw) or r[0] < fx or r[0] + r[2] > fx + fw:
            continue
        if r[1] < bottom and r[1] + r[3] > top and (best is None or r[2] > best[2]):
            best = r
    return None if best is None else (best[0], top, best[2], inset)


def build_payload(onscreen, known_ids, screens, own_pid, debug=False):
    """Listes CGWindow (du haut vers le bas) -> dictionnaire attendu par Sim.apply()."""
    items, alive = [], {str(i) for i in known_ids}
    for info in reversed(list(onscreen)):
        alive.add(str(int(info[K_NUMBER])))
        if not holds_snow(info, own_pid):
            continue
        rect = window_rect(info)
        fullscreen = any(same_rect(rect, s.frame) for s in screens)
        maximized = not fullscreen and any(same_rect(rect, s.visible) for s in screens)
        item = [str(int(info[K_NUMBER]))] + list(rect) + [0, int(fullscreen), int(maximized)]
        if debug:
            item += [str(info.get(K_NAME) or ""), str(info.get(K_OWNER) or "")]
        items.append(item)
    for s in screens:
        sid = "dock:%s" % s.key
        alive.add(sid)          # Dock disparu (plein écran…) : sa neige s'endort
        rect = dock_rect(s, onscreen)
        if rect is not None:
            item = [sid] + list(rect) + [1, 0, 0]
            if debug:
                item += ["Dock", "Dock"]
            items.append(item)  # le Dock passe devant les fenêtres
    return {"w": items, "alive": sorted(alive)}


def parse_on_battery(pmset_output):
    """Sortie de « pmset -g batt » -> True, False, ou None si elle est illisible."""
    if "'Battery Power'" in pmset_output:
        return True
    if "'AC Power'" in pmset_output:
        return False
    return None


class MacBackend(BatteryPauseMixin):
    DESCRIPTION = "Neige kawaii sur macOS ❄"

    @staticmethod
    def add_arguments(ap):
        ap.add_argument("--on-battery", action="store_true",
                        help="continue à neiger sur batterie (par défaut : pause)")

    @staticmethod
    def prepare_environment():
        # Sinon Qt fait de Python une app normale, avec une icône dans le Dock.
        os.environ.setdefault("QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM", "1")

    def __init__(self, app, args):
        try:
            import AppKit
            import Foundation
            import objc
            import Quartz
        except ImportError as e:
            sys.exit("yuki: %s\n  pip install pyobjc-framework-Quartz pyobjc-framework-Cocoa" % e)
        self.AppKit, self.Foundation, self.objc, self.Quartz = AppKit, Foundation, objc, Quartz
        self.app = app
        self.args = args
        self.pid = os.getpid()
        self.sim = self.feed = self.set_paused = None

        self.lock = acquire_instance_lock()

        AppKit.NSApplication.sharedApplication().setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyAccessory)
        # App Nap ralentirait les timers d'une app sans fenêtre active.
        self.activity = Foundation.NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
            Foundation.NSActivityUserInitiatedAllowingIdleSystemSleep, "Yuki : animation de la neige")

        self.screens = []
        self.visible_ids = None
        self.known_ids = []
        self.last_payload = None
        self.last_error = None
        self.next_alive = self.next_screens = self.moving_until = 0.0
        self.poll_timer = QTimer()
        self.poll_timer.timeout.connect(self.poll)

        self._init_power(args)
        self.power_proc = QProcess()
        self.power_proc.finished.connect(self._power_read)

    def screen_rects(self):
        return [s.geometry() for s in self.app.screens()]

    def attach(self, sim):
        self.sim = sim
        self.feed = WindowFeed(sim, self.args.debug)

    # ---- overlay -------------------------------------------------------
    def _configure(self, view):
        ns_view = self.objc.objc_object(c_void_p=ctypes.c_void_p(int(view.winId())))
        win = ns_view.window()
        if win is None:
            return
        A = self.AppKit
        win.setIgnoresMouseEvents_(True)
        # Au-dessus des fenêtres et du Dock, sous la barre des menus.
        win.setLevel_(self.Quartz.kCGMainMenuWindowLevel - 1)
        # Pas de FullScreenAuxiliary : rien ne s'affiche sur les apps en plein écran.
        win.setCollectionBehavior_(A.NSWindowCollectionBehaviorCanJoinAllSpaces
                                   | A.NSWindowCollectionBehaviorStationary
                                   | A.NSWindowCollectionBehaviorIgnoresCycle)
        win.setOpaque_(False)
        win.setHasShadow_(False)
        win.setBackgroundColor_(A.NSColor.clearColor())
        win.setCanHide_(False)   # survit à « Masquer les autres »

    def create_views(self, sim, rects):
        views = []
        for scr, rect in zip(self.app.screens(), rects):
            v = SnowView(sim, rect)
            v.setScreen(scr)
            v.create()
            self._configure(v)
            views.append(v)
        return views

    def view_shown(self, view):
        self._configure(view)    # Qt réapplique son propre niveau à l'affichage
        wanted = QRect(view.view.toRect())
        if view.geometry() != wanted:
            view.setGeometry(wanted)

    # ---- fenêtres et écrans --------------------------------------------
    def _read_screens(self):
        screens = self.AppKit.NSScreen.screens()
        if not screens:
            return self.screens  # écrans en veille : on garde ce qu'on sait
        primary_h = screens[0].frame().size.height
        out = []
        for s in screens:
            f, v = s.frame(), s.visibleFrame()
            out.append(ScreenInfo(
                int(s.deviceDescription()["NSScreenNumber"]),
                flip_rect((f.origin.x, f.origin.y, f.size.width, f.size.height), primary_h),
                flip_rect((v.origin.x, v.origin.y, v.size.width, v.size.height), primary_h)))
        return out

    def _window_list(self, options):
        return self.Quartz.CGWindowListCopyWindowInfo(options, self.Quartz.kCGNullWindowID)

    def _poll_once(self):
        Q = self.Quartz
        now = time.monotonic()
        onscreen = self._window_list(Q.kCGWindowListOptionOnScreenOnly
                                     | Q.kCGWindowListExcludeDesktopElements)
        if onscreen is None:
            return               # WindowServer indisponible : surtout ne rien faire tomber
        ids = frozenset(int(w[K_NUMBER]) for w in onscreen)
        # Une fenêtre vient de disparaître : réduite (neige gardée) ou fermée (neige qui tombe) ?
        if ids != self.visible_ids or now >= self.next_alive:
            everything = self._window_list(Q.kCGWindowListOptionAll)
            if everything is not None:
                self.known_ids = [int(w[K_NUMBER]) for w in everything]
                self.visible_ids = ids
                self.next_alive = now + ALIVE_EVERY
        if now >= self.next_screens:
            self.screens = self._read_screens()
            self.next_screens = now + SCREENS_EVERY

        payload = build_payload(onscreen, self.known_ids, self.screens, self.pid, self.args.debug)
        if payload != self.last_payload:
            if self.last_payload is not None and payload["w"] != self.last_payload["w"]:
                self.moving_until = now + MOVING_HOLD
            self.last_payload = payload
            self.feed.push(payload)

        if self.sim.user_paused:
            interval = POLL_PAUSED_MS
        else:
            interval = POLL_MOVING_MS if now < self.moving_until else POLL_IDLE_MS
        if self.poll_timer.interval() != interval:
            self.poll_timer.setInterval(interval)

    def poll(self):
        try:
            with self.objc.autorelease_pool():
                self._poll_once()
        except Exception as e:   # dans un slot PyQt, une exception ferait quitter Yuki
            if str(e) != self.last_error:
                self.last_error = str(e)
                print("yuki: lecture des fenêtres impossible:", e, file=sys.stderr)

    # ---- alimentation --------------------------------------------------
    def _power_check(self):
        if self.power_proc.state() == QProcess.ProcessState.NotRunning:
            self.power_proc.start("/usr/bin/pmset", ["-g", "batt"])

    def _power_read(self, *_):
        out = bytes(self.power_proc.readAllStandardOutput()).decode("utf-8", "replace")
        self.power_changed(parse_on_battery(out))

    # ---- cycle de vie --------------------------------------------------
    def start(self, sim, set_paused):
        self.set_paused = set_paused
        self.poll()
        self.poll_timer.start(POLL_IDLE_MS)
        self._start_power()

    def stop(self):
        self.poll_timer.stop()
        self.power_timer.stop()
        if self.power_proc.state() != QProcess.ProcessState.NotRunning:
            self.power_proc.kill()
            self.power_proc.waitForFinished(500)
        self.Foundation.NSProcessInfo.processInfo().endActivity_(self.activity)
        self.lock.unlock()
