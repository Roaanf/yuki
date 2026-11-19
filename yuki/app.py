"""Partie commune : options, vues par écran et boucle d'animation."""

import argparse
import signal
import sys

from PyQt6.QtCore import QElapsedTimer, QRectF, QTimer
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication

from .core import Sim
from .fun import SEASON_NAMES
from .settings import Settings
from .tray import make_tray
from .view import tile_rows

PAUSED_TICK_MS = 200   # en pause, inutile de réveiller le processus à chaque image


def load_backend():
    if sys.platform == "darwin":
        from .backends.macos import MacBackend
        return MacBackend
    if sys.platform == "win32":
        from .backends.windows import WindowsBackend
        return WindowsBackend
    from .backends.kde import KdeBackend
    return KdeBackend


def season_value(text):
    try:
        return float(SEASON_NAMES[text])
    except KeyError:
        raise argparse.ArgumentTypeError("choisir parmi : " + ", ".join(SEASON_NAMES)) from None


def parse_args(backend):
    ap = argparse.ArgumentParser(description=backend.DESCRIPTION)
    ap.add_argument("--density", type=float, default=1.0, help="quantité de flocons (1.0)")
    ap.add_argument("--wind", type=float, default=15.0, help="vent moyen en px/s (15, négatif = vers la gauche)")
    ap.add_argument("--pile", type=float, default=1.0, help="vitesse d'accumulation (1.0, 0 = pas d'accumulation)")
    ap.add_argument("--melt", type=float, default=0.003, help="fonte par seconde (0.003)")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--over-max", type=float, default=0.0,
                    help="opacité des flocons sur une fenêtre maximisée (0 = cachés, 1 = normal)")
    ap.add_argument("--over-windows", type=float, default=1.0,
                    help="opacité des flocons sur les autres fenêtres (1.0 ; 0.3 = discret)")
    ap.add_argument("--elves-every", type=float, default=6.0,
                    help="des lutins font un bonhomme de neige toutes les N minutes (6 ; 0 = jamais)")
    ap.add_argument("--santa-every", type=float, default=12.0,
                    help="le père Noël passe toutes les N minutes (12 ; 0 = jamais)")
    ap.add_argument("--plow-every", type=float, default=10.0,
                    help="un chasse-neige passe sur le panneau toutes les N minutes s'il y a de la neige (10 ; 0 = jamais)")
    ap.add_argument("--fight-every", type=float, default=8.0,
                    help="bataille de boules de neige toutes les N minutes (8 ; 0 = jamais)")
    ap.add_argument("--cat-every", type=float, default=9.0,
                    help="Neko-chan passe toutes les N minutes (9 ; 0 = jamais)")
    ap.add_argument("--cursor", type=float, default=1.0,
                    help="force du souffle du curseur (1.0 ; 0 = aucun)")
    ap.add_argument("--season", type=season_value, default=0.0,
                    help="auto, hiver, printemps ou automne (auto)")
    ap.add_argument("--no-tray", action="store_true",
                    help="pas d'icône ❄ dans la barre des tâches")
    ap.add_argument("--debug", action="store_true",
                    help="affiche les fenêtres sur lesquelles la neige peut tenir")
    backend.add_arguments(ap)
    return ap.parse_args()


def main():
    backend_class = load_backend()
    args = parse_args(backend_class)
    backend_class.prepare_environment()
    app = QApplication(sys.argv)          # QApplication : nécessaire pour l'icône de la barre
    app.setQuitOnLastWindowClosed(False)
    backend = backend_class(app, args)

    settings = Settings(args)
    if settings.saved:
        print("yuki: réglages du menu prioritaires sur les options :", ", ".join(settings.saved))
    rects = backend.screen_rects()
    v = settings.values
    sim = Sim(rects, density=v["density"], wind=v["wind"], pile=v["pile"], melt=v["melt"])
    settings.attach(sim)
    backend.attach(sim)

    views = []

    def build_views():
        # Les anciennes surfaces sont détruites AVANT d'en recréer : peindre sur une
        # surface dont l'écran a changé fait planter QtWayland.
        for v in views:
            v.dispose()
        views.clear()
        rs = backend.screen_rects()
        if [QRectF(r) for r in rs] != sim.screens:
            sim.set_screens(rs)
        views.extend(backend.create_views(sim, rs))
        for v in views:
            v.show()
            backend.view_shown(v)

    build_views()

    # Écran branché/débranché, résolution ou fréquence changée (kscreen-doctor…) :
    # on suspend le dessin et on reconstruit les surfaces une fois que ça s'est calmé.
    rebuild = QTimer()
    rebuild.setSingleShot(True)
    rebuild.setInterval(1200)
    rebuild.timeout.connect(build_views)

    def screens_changed(*_):
        for v in views:
            v.frozen = True
        rebuild.start()

    def watch(scr):
        for sig in (scr.geometryChanged, scr.refreshRateChanged,
                    scr.logicalDotsPerInchChanged, scr.physicalSizeChanged):
            sig.connect(screens_changed)

    for scr in app.screens():
        watch(scr)
    app.screenAdded.connect(lambda scr: (watch(scr), screens_changed()))
    app.screenRemoved.connect(screens_changed)
    app.primaryScreenChanged.connect(screens_changed)

    tray = None

    def set_paused(paused):
        sim.user_paused = paused
        if tray is not None:
            tray.sync_pause()

    if backend.HAS_BATTERY_OPTION:
        backend.run_on_battery = settings.on_battery
        settings.battery_listener = backend.set_run_on_battery
    backend.start(sim, set_paused)
    app.aboutToQuit.connect(backend.stop)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())

    clock = QElapsedTimer()
    clock.start()
    timer = QTimer()

    def tick():
        wanted = PAUSED_TICK_MS if sim.user_paused else settings.frame_ms
        if timer.interval() != wanted:
            timer.setInterval(wanted)
        dt = min(clock.restart() / 1000.0, 0.1)
        live = [v for v in views if not v.frozen]
        if not live:
            return
        hidden = [sim.user_paused or sim.hidden_on(v.view) for v in live]
        if not all(hidden):
            if backend.POLL_CURSOR and sim.cursor_force > 0:
                pos = QCursor.pos()
                sim.set_cursor(pos.x(), pos.y())
            sim.step(dt)
        full, tiles = sim.take_damage()
        rows = None if full else tile_rows(tiles)
        for v, h in zip(live, hidden):
            if h != v.blank:
                v.render(full=True)                 # passage plein écran : on vide / on remet tout
            elif not h:
                v.render(full=full, rows=rows)
            v.blank = h

    tray = None if args.no_tray else make_tray(app, sim, settings, backend.HAS_BATTERY_OPTION)

    timer.timeout.connect(tick)
    timer.start(settings.frame_ms)
    print("yuki: ❄ il neige ! Ctrl+C pour arrêter.")
    sys.exit(app.exec())
