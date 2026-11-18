"""Backend Windows : fenêtres et événements via Win32 (ctypes), sans dépendance en plus.

Win32 parle en pixels physiques, Qt en coordonnées logiques : la conversion se fait écran
par écran. Les fonctions du haut travaillent sur des données simples et se testent sans Windows.
"""

import collections
import ctypes
import os
import sys
from ctypes import wintypes

from PyQt6.QtCore import QRect, QStandardPaths, QTimer

from ..core import WindowFeed
from ..view import SnowView
from .common import BatteryPauseMixin, acquire_instance_lock

SCAN_DELAY_MS = 16        # regroupe les rafales d'événements (60 lectures/s au plus)
SCAN_PAUSED_MS = 1000
SAFETY_POLL_MS = 1000     # rattrape un événement manqué, écrans et barre des tâches

MIN_WINDOW = 50
RECT_TOLERANCE = 2
MIN_TASKBAR_HEIGHT = 8    # barre masquée automatiquement : il n'en reste que 2 px

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CAPTION = 0x00C00000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x10, 0x20
DWMWA_EXTENDED_FRAME_BOUNDS = 9   # sans l'ombre ni la bordure invisible de GetWindowRect
DWMWA_CLOAKED = 14                # autre bureau virtuel, app suspendue…
GA_ROOT = 2
OBJID_WINDOW = 0
WINEVENT_OUTOFCONTEXT, WINEVENT_SKIPOWNPROCESS = 0x0, 0x2
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
EVENT_RANGES = ((0x0003, 0x0003),   # premier plan
                (0x000A, 0x000B),   # début/fin de déplacement
                (0x0016, 0x0017),   # réduction
                (0x8000, 0x8004),   # création, destruction, affichage, masquage, ordre
                (EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE),
                (0x8017, 0x8018))   # masquage par le système (bureaux virtuels)

TASKBAR_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
SHELL_CLASSES = TASKBAR_CLASSES | {
    "Progman", "WorkerW", "#32768", "tooltips_class32", "NotifyIconOverflowWindow",
    "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow", "ForegroundStaging",
    "TopLevelWindowForOverflowXamlIsland", "MultitaskingViewFrame", "TaskListThumbnailWnd",
    "ApplicationManager_ImmersiveShellWindow"}

# rect : (x, y, w, h) en pixels physiques
Row = collections.namedtuple("Row", "hwnd visible iconic cloaked zoomed pid style exstyle cls rect title")
Row.__new__.__defaults__ = (False, False, False, False, 0, 0, 0, "", None, "")
# frame et work en pixels physiques ; logical = frame vue par Qt ; scale = physique / logique
Screen = collections.namedtuple("Screen", "key frame work logical scale")


def overlap(a, b):
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0, w) * max(0, h)


def same_rect(a, b, tolerance=RECT_TOLERANCE):
    return all(abs(p - q) <= tolerance for p, q in zip(a, b))


def screen_of(rect, screens):
    best = max(screens, key=lambda s: overlap(rect, s.frame), default=None)
    return best if best is not None and overlap(rect, best.frame) > 0 else None


def to_logical(rect, screen):
    """Qt garde l'origine physique de chaque écran et met à l'échelle à partir de là."""
    x, y, w, h = rect
    s = screen.scale
    return (int(round(screen.logical[0] + (x - screen.frame[0]) / s)),
            int(round(screen.logical[1] + (y - screen.frame[1]) / s)),
            int(round(w / s)), int(round(h / s)))


def match_screens(monitors, qt_screens):
    """monitors : (clé, frame, work) ; qt_screens : (x, y, w, h) logiques."""
    out = []
    for key, frame, work in monitors:
        if not qt_screens:
            break
        logical = min(qt_screens, key=lambda q: abs(q[0] - frame[0]) + abs(q[1] - frame[1]))
        out.append(Screen(key, frame, work, logical, frame[2] / max(1, logical[2])))
    return out


def holds_snow(row, own_pid):
    if not row.visible or row.iconic or row.cloaked or row.rect is None or row.pid == own_pid:
        return False
    if row.cls in SHELL_CLASSES or row.exstyle & WS_EX_TRANSPARENT:
        return False
    if row.exstyle & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) and not row.exstyle & WS_EX_APPWINDOW:
        return False
    return row.rect[2] >= MIN_WINDOW and row.rect[3] >= MIN_WINDOW


def is_fullscreen(row, screen):
    if row.zoomed and row.style & WS_CAPTION:
        return False             # fenêtre maximisée avec barre de titre masquée automatiquement
    return same_rect(row.rect, screen.frame, 1)


def taskbar_rect(rows, screen):
    """Barre des tâches visible en bas de cet écran (pixels physiques), ou None."""
    fx, fy, fw, fh = screen.frame
    bottom = fy + fh
    for row in rows:
        if row.cls not in TASKBAR_CLASSES or not row.visible or row.cloaked or row.rect is None:
            continue
        x, y, w, h = row.rect
        if overlap(row.rect, screen.frame) == 0 or w < fw / 2 or y < fy + fh / 2:
            continue             # sur un autre écran, ou sur un côté, ou en haut
        if abs(y + h - bottom) <= RECT_TOLERANCE and bottom - y >= MIN_TASKBAR_HEIGHT:
            return row.rect
    return None


def build_payload(rows, screens, own_pid, debug=False):
    """Fenêtres de la plus haute à la plus basse -> dictionnaire attendu par Sim.apply()."""
    items, alive = [], {str(r.hwnd) for r in rows}
    for row in reversed(rows):
        if not holds_snow(row, own_pid):
            continue
        screen = screen_of(row.rect, screens)
        if screen is None:
            continue             # rangée hors écran : elle dort
        fullscreen = is_fullscreen(row, screen)
        maximized = not fullscreen and (row.zoomed or same_rect(row.rect, screen.work))
        item = [str(row.hwnd)] + list(to_logical(row.rect, screen)) + [0, int(fullscreen), int(maximized)]
        if debug:
            item += [row.title, row.cls]
        items.append(item)
    for screen in screens:
        sid = "taskbar:%s" % screen.key
        alive.add(sid)           # barre masquée : sa neige s'endort
        rect = taskbar_rect(rows, screen)
        if rect is not None:
            item = [sid] + list(to_logical(rect, screen)) + [1, 0, 0]
            if debug:
                item += ["Barre des tâches", "Shell_TrayWnd"]
            items.append(item)
    return {"w": items, "alive": sorted(alive)}


def overlay_needs_raise(rows, overlays):
    """La barre des tâches est passée devant un de nos overlays ?"""
    bar_seen = False
    for row in rows:
        if row.cls in TASKBAR_CLASSES and row.visible:
            bar_seen = True
        elif row.hwnd in overlays and row.visible:
            return bar_seen
    return False


class MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                ("szDevice", ctypes.c_wchar * 32)]


class PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_uint32), ("BatteryFullLifeTime", ctypes.c_uint32)]


def rect_tuple(r):
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


_FUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)   # CFUNCTYPE : tests hors Windows
WNDENUMPROC = _FUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
MONITORENUMPROC = _FUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
WINEVENTPROC = _FUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                         wintypes.LONG, wintypes.DWORD, wintypes.DWORD)


class Win32:
    def __init__(self, debug=False, user32=None, dwmapi=None, kernel32=None):
        self.debug = debug
        self.user32 = user32 if user32 is not None else ctypes.WinDLL("user32", use_last_error=True)
        self.dwmapi = dwmapi if dwmapi is not None else ctypes.WinDLL("dwmapi")
        self.kernel32 = kernel32 if kernel32 is not None else ctypes.WinDLL("kernel32")
        self.hooks = []
        self.callback = None
        self._declare()

    def _declare(self):
        H = wintypes.HWND
        u = self.user32
        signatures = {
            "EnumWindows": ([WNDENUMPROC, wintypes.LPARAM], wintypes.BOOL),
            "IsWindowVisible": ([H], wintypes.BOOL), "IsIconic": ([H], wintypes.BOOL),
            "IsZoomed": ([H], wintypes.BOOL),
            "GetWindowThreadProcessId": ([H, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            "GetWindowLongPtrW": ([H, ctypes.c_int], ctypes.c_ssize_t),
            "SetWindowLongPtrW": ([H, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t),
            "GetClassNameW": ([H, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetWindowTextW": ([H, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetWindowRect": ([H, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "GetAncestor": ([H, wintypes.UINT], H),
            "SetWindowPos": ([H, H] + [ctypes.c_int] * 4 + [wintypes.UINT], wintypes.BOOL),
            "EnumDisplayMonitors": ([wintypes.HDC, ctypes.POINTER(wintypes.RECT), MONITORENUMPROC,
                                     wintypes.LPARAM], wintypes.BOOL),
            "GetMonitorInfoW": ([wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)], wintypes.BOOL),
            "SetWinEventHook": ([wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, WINEVENTPROC,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "UnhookWinEvent": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (args, res) in signatures.items():
            fn = getattr(u, name)
            fn.argtypes, fn.restype = args, res
        fn = self.dwmapi.DwmGetWindowAttribute
        fn.argtypes = [H, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        fn.restype = ctypes.c_long
        fn = self.kernel32.GetSystemPowerStatus
        fn.argtypes, fn.restype = [ctypes.POINTER(PowerStatus)], wintypes.BOOL

    def _dwm(self, hwnd, attribute, value):
        return self.dwmapi.DwmGetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                 ctypes.sizeof(value)) == 0

    def _rect(self, hwnd):
        r = wintypes.RECT()
        if self._dwm(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, r) or self.user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return rect_tuple(r)
        return None

    def _row(self, hwnd):
        u = self.user32
        if not u.IsWindowVisible(hwnd):
            return Row(hwnd)
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        cloaked = wintypes.DWORD()
        self._dwm(hwnd, DWMWA_CLOAKED, cloaked)
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, 256)
        title = ""
        if self.debug:
            buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(hwnd, buf, 256)
            title = buf.value
        return Row(hwnd, True, bool(u.IsIconic(hwnd)), bool(cloaked.value), bool(u.IsZoomed(hwnd)),
                   pid.value, u.GetWindowLongPtrW(hwnd, GWL_STYLE) & 0xFFFFFFFF,
                   u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF, cls.value,
                   self._rect(hwnd), title)

    def snapshot(self):
        """Toutes les fenêtres de premier niveau, de la plus haute à la plus basse."""
        hwnds = []

        def collect(hwnd, _):
            hwnds.append(hwnd)
            return True
        if not self.user32.EnumWindows(WNDENUMPROC(collect), 0):
            return None
        return [self._row(h) for h in hwnds if h]

    def monitors(self):
        out = []

        def collect(handle, _dc, _rect, _param):
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(MonitorInfo)
            if self.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                out.append((info.szDevice, rect_tuple(info.rcMonitor), rect_tuple(info.rcWork)))
            return True
        self.user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(collect), 0)
        return out

    def style_overlay(self, hwnd):
        u = self.user32
        style = u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        # WS_EX_LAYERED reste à Qt : le forcer rendrait la fenêtre invisible.
        u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE,
                            style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        self.raise_overlay(hwnd, SWP_FRAMECHANGED)

    def raise_overlay(self, hwnd, extra=0):
        self.user32.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                                 SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | extra)

    def on_battery(self):
        status = PowerStatus()
        if not self.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None
        return {0: True, 1: False}.get(status.ACLineStatus)

    def start_hooks(self, notify):
        def event(_hook, code, hwnd, id_object, id_child, _thread, _time):
            if id_object != OBJID_WINDOW or id_child != 0:
                return           # curseur, texte, contrôles : très fréquents et sans intérêt
            if code == EVENT_OBJECT_LOCATIONCHANGE and self.user32.GetAncestor(hwnd, GA_ROOT) != hwnd:
                return           # fenêtre enfant qui bouge
            notify()
        self.callback = WINEVENTPROC(event)   # à garder : Windows l'appelle jusqu'à la fin
        flags = WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS
        self.hooks = [self.user32.SetWinEventHook(lo, hi, None, self.callback, 0, 0, flags)
                      for lo, hi in EVENT_RANGES]

    def stop_hooks(self):
        for hook in self.hooks:
            if hook:
                self.user32.UnhookWinEvent(hook)
        self.hooks = []


class WindowsBackend(BatteryPauseMixin):
    DESCRIPTION = "Neige kawaii sur Windows ❄"

    @staticmethod
    def add_arguments(ap):
        ap.add_argument("--on-battery", action="store_true",
                        help="continue à neiger sur batterie (par défaut : pause)")

    @staticmethod
    def prepare_environment():
        if sys.stdout is None:   # pythonw n'a pas de console : on garde une trace
            folder = os.path.join(QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.GenericDataLocation), "Yuki")
            os.makedirs(folder, exist_ok=True)
            sys.stdout = sys.stderr = open(os.path.join(folder, "yuki.log"), "w",
                                           encoding="utf-8", buffering=1)

    def __init__(self, app, args):
        self.app = app
        self.args = args
        self.pid = os.getpid()
        self.api = Win32(args.debug)
        self.lock = acquire_instance_lock()
        self.sim = self.feed = self.set_paused = None
        self.views = []
        self.screens = []
        self.last_payload = None
        self.last_error = None
        self.scan_timer = QTimer()
        self.scan_timer.setSingleShot(True)
        self.scan_timer.timeout.connect(self.scan)
        self.safety_timer = QTimer()
        self.safety_timer.setInterval(SAFETY_POLL_MS)
        self.safety_timer.timeout.connect(self.safety_poll)
        self._init_power(args)

    def screen_rects(self):
        return [s.geometry() for s in self.app.screens()]

    def attach(self, sim):
        self.sim = sim
        self.feed = WindowFeed(sim, self.args.debug)

    # ---- overlay -------------------------------------------------------
    def _hwnd(self, view):
        return int(view.winId())

    def create_views(self, sim, rects):
        self.views = []
        for scr, rect in zip(self.app.screens(), rects):
            v = SnowView(sim, rect)
            v.setScreen(scr)
            v.create()
            self.api.style_overlay(self._hwnd(v))   # avant show : pas d'apparition dans Alt+Tab
            self.views.append(v)
        return self.views

    def view_shown(self, view):
        self.api.style_overlay(self._hwnd(view))
        wanted = QRect(view.view.toRect())
        if view.geometry() != wanted:
            view.setGeometry(wanted)

    def _sync_visibility(self):
        # Un overlay caché ne coûte rien et ne gêne pas les jeux en plein écran.
        for v in self.views:
            if v.frozen:
                continue
            hide = self.sim.user_paused or self.sim.hidden_on(v.view)
            if hide and v.isVisible():
                v.hide()
            elif not hide and not v.isVisible():
                v.show()
                self.view_shown(v)

    # ---- fenêtres et écrans --------------------------------------------
    def _read_screens(self):
        qt = [(g.x(), g.y(), g.width(), g.height())
              for g in (s.geometry() for s in self.app.screens())]
        return match_screens(self.api.monitors(), qt) or self.screens

    def request_scan(self):
        if not self.scan_timer.isActive():
            self.scan_timer.start(SCAN_PAUSED_MS if self.sim.user_paused else SCAN_DELAY_MS)

    def _guarded(self, fn):
        try:
            fn()
        except Exception as e:   # dans un slot PyQt, une exception ferait quitter Yuki
            if str(e) != self.last_error:
                self.last_error = str(e)
                print("yuki: lecture des fenêtres impossible:", e, file=sys.stderr)

    def _scan_once(self):
        rows = self.api.snapshot()
        if not rows:
            return None          # liste indisponible : surtout ne rien faire tomber
        payload = build_payload(rows, self.screens, self.pid, self.args.debug)
        if payload != self.last_payload:
            self.last_payload = payload
            self.feed.push(payload)
            self._sync_visibility()
        return rows

    def scan(self):
        self._guarded(self._scan_once)

    def _safety_once(self):
        self.screens = self._read_screens()
        rows = self._scan_once()
        self._sync_visibility()
        overlays = {self._hwnd(v) for v in self.views if v.isVisible()}
        if rows and overlay_needs_raise(rows, overlays):
            for hwnd in overlays:
                self.api.raise_overlay(hwnd)

    def safety_poll(self):
        self._guarded(self._safety_once)

    # ---- alimentation et cycle de vie ----------------------------------
    def _power_check(self):
        self.power_changed(self.api.on_battery())

    def start(self, sim, set_paused):
        self.set_paused = set_paused
        self.safety_poll()
        self.api.start_hooks(self.request_scan)
        self.safety_timer.start()
        self._start_power()

    def stop(self):
        self.scan_timer.stop()
        self.safety_timer.stop()
        self.power_timer.stop()
        self.api.stop_hooks()
        self.lock.unlock()
