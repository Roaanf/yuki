"""Backend KDE Plasma 6 : script KWin, layer-shell et D-Bus."""

import ctypes
import json
import os
import sys
import tempfile

from PyQt6.QtCore import QObject, pyqtClassInfo, pyqtSlot
from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

from ..core import WindowFeed
from ..view import SnowView

APP_ID = "yuki-snow"
SERVICE = "org.roland.Yuki"
OBJ_PATH = "/Yuki"
IFACE = "org.roland.Yuki"
SCRIPT_NAME = "yuki-snow"


# --------------------------------------------------------------------------
# Script KWin (Plasma 6). __PID__, __MANAGE__, __VG__ sont remplacés.
# --------------------------------------------------------------------------
KWIN_SCRIPT = r"""
var PID = __PID__;
var MANAGE_SELF = __MANAGE__;
var VG = __VG__;
var PANEL_GAP = __GAP__;
var DEBUG = __DEBUG__;
var removed = {};
var lastSent = "";
var lastCursor = 0;

function onCurrentDesktop(w) {
    if (w.onAllDesktops) return true;
    var cur = workspace.currentDesktop;
    var ds = w.desktops || [];
    for (var i = 0; i < ds.length; i++) {
        if (ds[i] && cur && ds[i].id === cur.id) return true;
    }
    return false;
}
function onCurrentActivity(w) {
    var acts = w.activities;
    if (!acts || acts.length === 0) return true;      // sur toutes les activités
    var cur = workspace.currentActivity;
    if (!cur) return true;
    for (var i = 0; i < acts.length; i++) if (acts[i] === cur) return true;
    return false;
}
function kindOk(w) {
    return !w.desktopWindow && (w.normalWindow || w.dialog || w.dock || w.utility);
}
function isVisible(w) {
    return kindOk(w) && !w.minimized && !w.hidden && w.readyForPainting !== false
        && (w.opacity === undefined || w.opacity > 0.05)
        && onCurrentDesktop(w) && onCurrentActivity(w);
}
function manageSelf(w) {
    if (!MANAGE_SELF || w.pid !== PID || !w.normalWindow) return;
    try {
        w.keepAbove = true;
        w.skipTaskbar = true;
        w.skipPager = true;
        w.skipSwitcher = true;
        w.onAllDesktops = true;
        w.noBorder = true;
        w.frameGeometry = {x: VG.x, y: VG.y, width: VG.width, height: VG.height};
    } catch (e) {}
}
// Panneau du bas : sa surface est plus grande que ce qu'on voit (marge du mode flottant).
// Haut visible = bas de la zone des fenêtres maximisées, moins l'écart s'il flotte.
// Plasma « déflotte » le panneau quand une fenêtre le touche : on refait le même test.
function panelTop(p, order) {
    var area;
    try { area = workspace.clientArea(KWin.MaximizeArea, p); } catch (e) { return null; }
    var g = p.frameGeometry;
    var ab = area.y + area.height;
    if (!(ab > g.y && ab < g.y + g.height)) return null;   // pas un panneau du bas
    var touched = false;
    for (var i = 0; i < order.length && !touched; i++) {
        var w = order[i];
        if (!w || w.pid === PID || w.dock || !isVisible(w) || w.fullScreen) continue;
        if (!(w.normalWindow || w.dialog)) continue;
        var f = w.frameGeometry;
        if (f.x < g.x + g.width && f.x + f.width > g.x && f.y + f.height >= ab - PANEL_GAP - 1)
            touched = true;
    }
    var floating = PANEL_GAP > 0 && !touched;
    return {y: floating ? ab - PANEL_GAP : ab, floating: floating};
}

function isMaximized(w) {
    if (w.dock || !w.normalWindow) return false;
    var a;
    try { a = workspace.clientArea(KWin.MaximizeArea, w); } catch (e) { return false; }
    var g = w.frameGeometry;
    return Math.abs(g.x - a.x) <= 2 && Math.abs(g.y - a.y) <= 2 &&
           Math.abs(g.width - a.width) <= 2 && Math.abs(g.height - a.height) <= 2;
}

function push() {
    var order = workspace.stackingOrder;
    if (!order || order.length === undefined) order = workspace.windowList();
    var vis = [], alive = [];
    for (var i = 0; i < order.length; i++) {
        var w = order[i];
        if (!w || w.pid === PID || !kindOk(w)) continue;
        var id = String(w.internalId);
        if (removed[id]) continue;
        alive.push(id);
        if (!isVisible(w)) continue;
        var g = w.frameGeometry;
        var gx = g.x, gy = g.y, gw = g.width, gh = g.height;
        if (w.dock) {
            var top = panelTop(w, order);
            if (top !== null) {
                if (top.floating) { gx += PANEL_GAP; gw -= 2 * PANEL_GAP; }
                gh = gy + gh - top.y; gy = top.y;
            }
        }
        vis.push([id, Math.round(gx), Math.round(gy), Math.round(gw), Math.round(gh),
                  w.dock ? 1 : 0, w.fullScreen ? 1 : 0, isMaximized(w) ? 1 : 0]);
        if (DEBUG) vis[vis.length - 1].push(String(w.caption), String(w.resourceClass));
    }
    var msg = JSON.stringify({w: vis, alive: alive});
    if (msg === lastSent) return;
    lastSent = msg;
    callDBus("org.roland.Yuki", "/Yuki", "org.roland.Yuki", "update", msg);
}
// Une souris à 1000 Hz saturerait D-Bus : 30 positions par seconde suffisent.
function pushCursor() {
    var now = Date.now();
    if (now - lastCursor < 33) return;
    lastCursor = now;
    var c = workspace.cursorPos;
    callDBus("org.roland.Yuki", "/Yuki", "org.roland.Yuki", "cursor",
             Math.round(c.x) + " " + Math.round(c.y));
}
function hook(w) {
    if (!w) return;
    manageSelf(w);
    var sigs = ["frameGeometryChanged", "minimizedChanged", "desktopsChanged",
                "fullScreenChanged", "hiddenChanged"];
    for (var i = 0; i < sigs.length; i++) {
        var s = w[sigs[i]];
        if (s && s.connect) s.connect(push);
    }
}
function connectWs(name, fn) {
    var s = workspace[name];
    if (s && s.connect) s.connect(fn);
}

var initial = workspace.windowList();
for (var i = 0; i < initial.length; i++) hook(initial[i]);
// Notification : l'oiseau postier y apporte une lettre (pas de neige dessus, elle ne dure pas).
function notify(w) {
    if (!w || !(w.notification || w.criticalNotification)) return;
    var g = w.frameGeometry;
    callDBus("org.roland.Yuki", "/Yuki", "org.roland.Yuki", "notified",
             Math.round(g.x) + " " + Math.round(g.y) + " " + Math.round(g.width) + " " + Math.round(g.height));
}
connectWs("windowAdded", function (w) { hook(w); notify(w); push(); });
connectWs("windowRemoved", function (w) { if (w) removed[String(w.internalId)] = true; push(); });
connectWs("windowActivated", push);
connectWs("currentDesktopChanged", push);
connectWs("currentActivityChanged", push);
connectWs("stackingOrderChanged", push);
connectWs("cursorPosChanged", pushCursor);
push();
"""


def load_layer_shell():
    try:
        lib = ctypes.CDLL("libLayerShellQtInterface.so.6")
        lib._ZN12LayerShellQt6Window3getEP7QWindow  # noqa: B018 (vérifie le symbole)
        return lib
    except (OSError, AttributeError):
        return None


def make_layer_surface(lib, win):
    """Transforme la QWindow en surface layer-shell (couche « top », plein écran)."""
    from PyQt6 import sip
    get = lib._ZN12LayerShellQt6Window3getEP7QWindow
    get.restype = ctypes.c_void_p
    get.argtypes = [ctypes.c_void_p]
    ptr = get(sip.unwrapinstance(win))
    if not ptr:
        return False

    def call(name, *args, optional=False):
        try:
            fn = getattr(lib, name)
        except AttributeError:
            if optional:
                return
            raise
        fn.restype = None
        fn.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * len(args)
        fn(ptr, *args)

    call("_ZN12LayerShellQt6Window8setLayerENS0_5LayerE", 2)                       # LayerTop
    call("_ZN12LayerShellQt6Window10setAnchorsE6QFlagsINS0_6AnchorEE", 1 | 2 | 4 | 8)
    call("_ZN12LayerShellQt6Window16setExclusiveZoneEi", -1)                      # passe sur les panneaux
    call("_ZN12LayerShellQt6Window24setKeyboardInteractivityENS0_21KeyboardInteractivityE", 0)
    call("_ZN12LayerShellQt6Window26setWantsToBeOnActiveScreenEb", 0, optional=True)
    return True


# --------------------------------------------------------------------------
# D-Bus
# --------------------------------------------------------------------------
@pyqtClassInfo("D-Bus Interface", IFACE)
class BusObject(QObject):
    def __init__(self, sim, debug=False):
        super().__init__()
        self.sim = sim
        self.feed = WindowFeed(sim, debug)

    @pyqtSlot(result=bool)
    def snowman(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki snowman"""
        return self.sim.start_snowman(force=True)

    @pyqtSlot(result=bool)
    def santa(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki santa"""
        return self.sim.start_santa()

    @pyqtSlot(result=bool)
    def plow(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki plow"""
        return self.sim.start_plow()

    @pyqtSlot(result=bool)
    def fight(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki fight"""
        return self.sim.start_fight(force=True)

    @pyqtSlot(result=bool)
    def cat(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki cat"""
        return self.sim.start_cat()

    @pyqtSlot(result=bool)
    def fireworks(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki fireworks"""
        return self.sim.start_fireworks()

    @pyqtSlot(result=bool)
    def aurora(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki aurora"""
        return self.sim.start_aurora()

    @pyqtSlot(result=bool)
    def star(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki star"""
        return self.sim.start_star()

    @pyqtSlot(result=bool)
    def fireflies(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki fireflies"""
        return self.sim.start_fireflies()

    @pyqtSlot(result=bool)
    def rain(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki rain"""
        return self.sim.start_rain()

    @pyqtSlot(result=bool)
    def frost(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki frost"""
        return self.sim.start_frost()

    @pyqtSlot(result=bool)
    def sled(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki sled"""
        return self.sim.start_sled()

    @pyqtSlot(result=bool)
    def bird(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki bird"""
        return self.sim.start_bird()

    @pyqtSlot(result=bool)
    def welcome(self):
        """busctl --user call org.roland.Yuki /Yuki org.roland.Yuki welcome"""
        return self.sim.welcome_back(1800.0)

    @pyqtSlot(str)
    def notified(self, geometry):
        """Envoyé par le script KWin quand une notification s'affiche : « x y largeur hauteur »."""
        try:
            x, y, w, h = (float(v) for v in geometry.split())
        except ValueError:
            return
        self.sim.notified(x, y, w, h)

    @pyqtSlot(str)
    def cursor(self, position):
        try:
            x, y = position.split()
            self.sim.set_cursor(int(x), int(y))
        except ValueError:
            pass

    @pyqtSlot(str)
    def update(self, payload):
        try:
            data = json.loads(payload)
        except ValueError as e:
            print("yuki: message ignoré:", e, file=sys.stderr)
            return
        self.feed.push(data)


def is_error(msg):
    return msg.type() == QDBusMessage.MessageType.ErrorMessage


def load_kwin_script(bus, path):
    scripting = QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus)
    if not scripting.isValid():
        return "KWin introuvable sur D-Bus (tu es bien dans Plasma ?)"
    scripting.call("unloadScript", SCRIPT_NAME)
    r = scripting.call("loadScript", path, SCRIPT_NAME)
    if is_error(r):
        return "loadScript: " + r.errorMessage()
    sid = r.arguments()[0]
    for p in ("/Scripting/Script%d" % sid, "/%d" % sid):
        s = QDBusInterface("org.kde.KWin", p, "org.kde.kwin.Script", bus)
        if s.isValid() and not is_error(s.call("run")):
            return None
    return "impossible de lancer le script KWin (id %s)" % sid


def unload_kwin_script(bus):
    QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus) \
        .call("unloadScript", SCRIPT_NAME)


# --------------------------------------------------------------------------
class KdeBackend:
    DESCRIPTION = "Neige kawaii sur Plasma ❄"
    HAS_BATTERY_OPTION = False   # géré par yuki-sync.sh, qui arrête le processus
    POLL_CURSOR = False          # Wayland cache la souris : le script KWin l'envoie

    @staticmethod
    def add_arguments(ap):
        ap.add_argument("--panel-gap", type=int, default=8,
                        help="écart du panneau flottant en px (8 ; 0 si ton panneau ne flotte pas)")

    @staticmethod
    def prepare_environment():
        if os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

    def __init__(self, app, args):
        self.app = app
        self.args = args
        self.script_path = None
        self.bus = QDBusConnection.sessionBus()
        if not self.bus.isConnected():
            sys.exit("yuki: pas de bus D-Bus de session")
        if not self.bus.registerService(SERVICE):
            sys.exit("yuki: déjà lancé ? (" + SERVICE + " est pris)")
        self.lib = load_layer_shell() if app.platformName() == "wayland" else None
        self.idle = None
        if app.platformName() == "wayland":
            try:
                from .wl_idle import WaylandIdle
                self.idle = WaylandIdle()
            except (OSError, AttributeError) as e:
                print("yuki: inactivité clavier inconnue (%s), on suit le curseur" % e, file=sys.stderr)
        if not self.lib:
            print("yuki: layer-shell indisponible, mode fenêtre « au-dessus »", file=sys.stderr)

    def screen_rects(self):
        if self.lib:
            return [s.geometry() for s in self.app.screens()]
        return [self.app.primaryScreen().virtualGeometry()]

    def attach(self, sim):
        self.bus_obj = BusObject(sim, self.args.debug)
        self.bus.registerObject(OBJ_PATH, self.bus_obj,
                                QDBusConnection.RegisterOption.ExportAllSlots)

    def create_views(self, sim, rects):
        if not self.lib:
            return [SnowView(sim, rects[0])]
        views = []
        for scr, rect in zip(self.app.screens(), rects):
            v = SnowView(sim, rect)
            v.setScreen(scr)
            if not make_layer_surface(self.lib, v):
                sys.exit("yuki: layer-shell a échoué")
            views.append(v)
        return views

    def view_shown(self, view):
        pass

    def idle_seconds(self):
        return None if self.idle is None else self.idle.seconds()

    def start(self, sim, set_paused):
        args = self.args
        vg = self.app.primaryScreen().virtualGeometry() if self.lib else self.screen_rects()[0]
        script = (KWIN_SCRIPT
                  .replace("__PID__", str(os.getpid()))
                  .replace("__MANAGE__", "false" if self.lib else "true")
                  .replace("__GAP__", str(args.panel_gap))
                  .replace("__DEBUG__", "true" if args.debug else "false")
                  .replace("__VG__", json.dumps({"x": vg.x(), "y": vg.y(),
                                                 "width": vg.width(), "height": vg.height()})))
        fd, self.script_path = tempfile.mkstemp(prefix="yuki-kwin-", suffix=".js")
        with os.fdopen(fd, "w") as fh:
            fh.write(script)
        err = load_kwin_script(self.bus, self.script_path)
        if err:
            print("yuki:", err, "— la neige tombera mais ne tiendra pas sur les fenêtres",
                  file=sys.stderr)

    def stop(self):
        unload_kwin_script(self.bus)
        if self.idle is not None:
            self.idle.close()
        try:
            os.unlink(self.script_path)
        except (OSError, TypeError):
            pass
