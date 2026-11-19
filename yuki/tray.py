"""Icône ❄ et son menu (zone de notification, ou barre des menus sur macOS)."""

import sys

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from .settings import PARAMS, same_value


def snowflake_icon():
    icon = QIcon()
    for size in (16, 22, 32, 48, 64):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(size / 2, size / 2)
        k = size / 64
        for color, width in ((QColor(70, 130, 200), 9 * k), (QColor(225, 245, 255), 5 * k)):
            pen = QPen(color, max(1.2, width))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for i in range(6):
                p.save()
                p.rotate(i * 60)
                p.drawLine(QPointF(0, 0), QPointF(0, -26 * k))
                p.drawLine(QPointF(0, -15 * k), QPointF(-8 * k, -22 * k))
                p.drawLine(QPointF(0, -15 * k), QPointF(8 * k, -22 * k))
                p.restore()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 170, 200))
        p.drawEllipse(QPointF(0, 0), 5 * k, 5 * k)     # petit cœur rose au centre
        p.end()
        icon.addPixmap(pm)
    return icon


def add_settings_menu(menu, settings, battery_option=False):
    """Sous-menu « Réglages » : une liste de choix par paramètre."""
    root = menu.addMenu("⚙  Réglages")
    root.menuAction().setMenuRole(QAction.MenuRole.NoRole)
    submenus = []

    def fill(sub, param):
        sub.clear()
        for old in sub.findChildren(QActionGroup):
            old.setParent(None)
            old.deleteLater()
        group = QActionGroup(sub)
        group.setExclusive(True)
        current = settings.values[param.key]
        choices = list(param.presets)
        if not any(same_value(v, current) for _, v in choices):
            choices.append(("Personnalisé (%g)" % current, current))   # valeur des options de lancement
        for text, value in choices:
            a = QAction(text, sub)
            a.setMenuRole(QAction.MenuRole.NoRole)
            a.setCheckable(True)
            a.setChecked(same_value(value, current))
            a.triggered.connect(lambda _=False, k=param.key, v=value: settings.set(k, v))
            group.addAction(a)
            sub.addAction(a)

    battery = None

    def reset():
        settings.reset()
        for sub, param in submenus:
            fill(sub, param)
        if battery is not None:
            battery.setChecked(settings.on_battery)

    for param in PARAMS:
        sub = root.addMenu(param.label)
        sub.menuAction().setMenuRole(QAction.MenuRole.NoRole)
        submenus.append((sub, param))
        fill(sub, param)
    if battery_option:
        battery = QAction("🔋  Neiger sur batterie", root)
        battery.setMenuRole(QAction.MenuRole.NoRole)
        battery.setCheckable(True)
        battery.setChecked(settings.on_battery)
        battery.triggered.connect(settings.set_on_battery)
        root.addAction(battery)
    root.addSeparator()
    again = QAction("↩  Revenir aux réglages de lancement", root)
    again.setMenuRole(QAction.MenuRole.NoRole)
    again.triggered.connect(reset)
    root.addAction(again)
    return root


def make_tray(app, sim, settings, battery_option=False):
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("yuki: pas de zone de notification, pas d'icône", file=sys.stderr)
        return None
    tray = QSystemTrayIcon(snowflake_icon(), app)
    tray.setToolTip("Yuki ❄")
    menu = QMenu()

    def act(text, fn):
        a = QAction(text, menu)
        a.setMenuRole(QAction.MenuRole.NoRole)  # macOS déplacerait « Quitter » ailleurs
        a.triggered.connect(fn)
        menu.addAction(a)
        return a

    def plow():
        if not sim.start_plow():
            msg = ("Le chasse-neige est déjà en route~" if sim.plows
                   else "Pas de panneau visible à déneiger")
            tray.showMessage("Yuki", msg, tray.icon(), 3000)

    def snowman():
        if not sim.start_snowman(force=True):
            tray.showMessage("Yuki", "Pas d'endroit visible avec assez de neige pour un bonhomme 🥲",
                             tray.icon(), 3000)

    def santa():
        if not sim.start_santa():
            tray.showMessage("Yuki", "Le père Noël est déjà en vol !", tray.icon(), 3000)

    def fight():
        if not sim.start_fight(force=True):
            msg = ("La bataille fait déjà rage !" if sim.fight
                   else "Pas assez de place visible pour une bataille 🥲")
            tray.showMessage("Yuki", msg, tray.icon(), 3000)

    def cat():
        if not sim.start_cat():
            msg = "Neko-chan est déjà là~" if sim.cat else "Neko-chan ne trouve pas où marcher 🥲"
            tray.showMessage("Yuki", msg, tray.icon(), 3000)

    act("🚜  Chasse-neige", plow)
    act("⛄  Bonhomme de neige", snowman)
    act("🎅  Père Noël", santa)
    act("⚔  Bataille de boules de neige", fight)
    act("🐱  Neko-chan", cat)
    act("🎆  Feu d'artifice", lambda: sim.start_fireworks())
    more = menu.addMenu("✨  Encore plus")
    more.menuAction().setMenuRole(QAction.MenuRole.NoRole)

    def extra(text, fn, busy=None):
        def run():
            if not fn() and busy:
                tray.showMessage("Yuki", busy, tray.icon(), 3000)
        a = QAction(text, more)
        a.setMenuRole(QAction.MenuRole.NoRole)
        a.triggered.connect(run)
        more.addAction(a)

    extra("🌌  Aurore boréale", sim.start_aurora, "L'aurore danse déjà~")
    extra("🌠  Étoile filante", sim.start_star)
    extra("✨  Lucioles", sim.start_fireflies)
    extra("🌧  Averse", sim.start_rain, "Il pleut déjà !")
    extra("🧊  Givre", sim.start_frost)
    extra("🛷  Luge", sim.start_sled, "Pas de fenêtre enneigée assez large pour la luge 🥲")
    extra("🐦  Oiseau postier", sim.start_bird, "L'oiseau est déjà en tournée~")
    extra("🏠  Retour d'absence", lambda: sim.welcome_back(1800.0), "On vient déjà de rentrer~")
    menu.addSeparator()
    add_settings_menu(menu, settings, battery_option)
    pause = act("⏸  Pause", lambda: toggle_pause())
    menu.addSeparator()
    act("✖  Quitter", app.quit)

    def sync_pause():
        pause.setText("▶  Reprendre" if sim.user_paused else "⏸  Pause")
        tray.setToolTip("Yuki ❄ (en pause)" if sim.user_paused else "Yuki ❄")

    def toggle_pause():
        sim.user_paused = not sim.user_paused
        sync_pause()

    tray.setContextMenu(menu)
    if sys.platform != "darwin":   # sur macOS, le clic sert à ouvrir le menu
        tray.activated.connect(lambda reason: toggle_pause()
                               if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()
    tray._menu = menu      # garde une référence
    tray.sync_pause = sync_pause
    return tray
