"""Morceaux partagés par les backends sans D-Bus (macOS, Windows)."""

import os
import sys

from PyQt6.QtCore import QLockFile, QStandardPaths, QTimer

POWER_EVERY_MS = 30000


def acquire_instance_lock():
    """Rien ne joue ici le rôle du nom D-Bus : un verrou évite deux neiges."""
    tmp = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
    lock = QLockFile(os.path.join(tmp, "org.roland.yuki.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        sys.exit("yuki: déjà lancé ?")
    return lock


class BatteryPauseMixin:
    """Pause sur batterie. La classe fournit _power_check(), qui appelle power_changed()."""

    HAS_BATTERY_OPTION = True
    POLL_CURSOR = True       # Qt y connaît la position globale de la souris

    def _init_power(self, args):
        self.run_on_battery = args.on_battery
        self.on_battery = None
        self.paused_by_power = False
        self.power_timer = QTimer()
        self.power_timer.setInterval(POWER_EVERY_MS)
        self.power_timer.timeout.connect(self._power_check)

    def _start_power(self):
        if not self.run_on_battery:
            self._power_check()
            self.power_timer.start()

    def power_changed(self, on_battery):
        if self.run_on_battery or on_battery is None or on_battery == self.on_battery:
            return               # une réponse tardive peut arriver après le changement
        self.on_battery = on_battery
        if on_battery:           # une pause choisie à la main reste à la main
            self.paused_by_power = not self.sim.user_paused
            if self.paused_by_power:
                self.set_paused(True)
        elif self.paused_by_power:
            self.paused_by_power = False
            self.set_paused(False)

    def set_run_on_battery(self, run):
        if run == self.run_on_battery:
            return
        self.run_on_battery = run
        self.on_battery = None   # à relire : l'état a pu changer sans qu'on le suive
        if run:
            self.power_timer.stop()
            if self.paused_by_power:
                self.paused_by_power = False
                self.set_paused(False)
        else:
            self._power_check()
            self.power_timer.start()
