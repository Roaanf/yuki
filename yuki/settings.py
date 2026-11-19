"""Réglages modifiables depuis le menu : appliqués en direct et gardés d'une session à l'autre."""

import collections
import math

from PyQt6.QtCore import QSettings

Param = collections.namedtuple("Param", "key label low high presets")

PARAMS = (
    Param("density", "❄  Quantité de flocons", 0.0, None,
          (("Très peu", 0.3), ("Peu", 0.5), ("Moyenne", 0.7), ("Normale", 1.0),
           ("Beaucoup", 1.5), ("Tempête", 2.5))),
    Param("wind", "🌬  Vent", None, None,
          (("Fort vers la gauche", -60.0), ("Léger vers la gauche", -15.0), ("Aucun", 0.0),
           ("Léger vers la droite", 15.0), ("Fort vers la droite", 60.0))),
    Param("pile", "🏔  Accumulation", 0.0, None,
          (("Aucune", 0.0), ("Lente", 0.35), ("Normale", 1.0), ("Rapide", 2.0))),
    Param("melt", "💧  Fonte", 0.0, None,
          (("Lente", 0.001), ("Normale", 0.003), ("Rapide", 0.006), ("Très rapide", 0.015))),
    Param("over_windows", "🪟  Flocons sur les fenêtres", 0.0, 1.0,
          (("Cachés", 0.0), ("Discrets", 0.3), ("Légers", 0.6), ("Normaux", 1.0))),
    Param("over_max", "🖥  Flocons sur les fenêtres maximisées", 0.0, 1.0,
          (("Cachés", 0.0), ("Très discrets", 0.1), ("Discrets", 0.3), ("Normaux", 1.0))),
    Param("plow_every", "🚜  Passage du chasse-neige", 0.0, None,
          (("Jamais", 0.0), ("5 min", 5.0), ("10 min", 10.0), ("20 min", 20.0), ("30 min", 30.0))),
    Param("elves_every", "⛄  Passage des lutins", 0.0, None,
          (("Jamais", 0.0), ("3 min", 3.0), ("6 min", 6.0), ("15 min", 15.0), ("30 min", 30.0))),
    Param("santa_every", "🎅  Passage du père Noël", 0.0, None,
          (("Jamais", 0.0), ("5 min", 5.0), ("12 min", 12.0), ("30 min", 30.0), ("1 h", 60.0))),
    Param("fight_every", "⚔  Bataille de boules de neige", 0.0, None,
          (("Jamais", 0.0), ("4 min", 4.0), ("8 min", 8.0), ("15 min", 15.0), ("30 min", 30.0))),
    Param("cat_every", "🐱  Visite de Neko-chan", 0.0, None,
          (("Jamais", 0.0), ("5 min", 5.0), ("9 min", 9.0), ("20 min", 20.0), ("45 min", 45.0))),
    Param("aurora_every", "🌌  Aurores boréales (soirs d'hiver)", 0.0, None,
          (("Jamais", 0.0), ("10 min", 10.0), ("20 min", 20.0), ("45 min", 45.0))),
    Param("rain_every", "🌧  Averses (automne)", 0.0, None,
          (("Jamais", 0.0), ("6 min", 6.0), ("12 min", 12.0), ("25 min", 25.0))),
    Param("frost_after", "🧊  Givre quand tu t'absentes", 0.0, None,
          (("Jamais", 0.0), ("Après 1 min", 1.0), ("Après 2 min", 2.0), ("Après 5 min", 5.0))),
    Param("bird", "🐦  Oiseau postier (notifications)", 0.0, 1.0,
          (("Jamais", 0.0), ("Oui", 1.0))),
    Param("cursor", "🖱  Souffle du curseur", 0.0, None,
          (("Aucun", 0.0), ("Doux", 0.5), ("Normal", 1.0), ("Fort", 2.0))),
    Param("season", "🌸  Saison", 0.0, 3.0,
          (("Selon la date", 0.0), ("Hiver", 1.0), ("Printemps", 2.0), ("Automne", 3.0))),
    Param("fps", "🎞  Images par seconde", 1.0, None,
          (("15", 15.0), ("30", 30.0), ("60", 60.0), ("120", 120.0))),
    Param("fps_calm", "🎞  Images par seconde au calme", 0.0, None,
          (("Comme en action", 0.0), ("15", 15.0), ("30", 30.0))),
)
BY_KEY = {p.key: p for p in PARAMS}
SIM_ATTRS = {"wind": "wind", "pile": "pile", "melt": "melt", "over_windows": "over_win",
             "over_max": "over_max", "plow_every": "plow_every",
             "elves_every": "elves_every", "santa_every": "santa_every",
             "fight_every": "fight_every", "cat_every": "cat_every", "cursor": "cursor_force",
             "aurora_every": "aurora_every", "rain_every": "rain_every", "frost_after": "frost_after",
             "bird": "bird_on"}


def same_value(a, b):
    return abs(a - b) < 1e-9


def clean(param, value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(value)
    if param.low is not None:
        value = max(param.low, value)
    if param.high is not None:
        value = min(param.high, value)
    return float(round(value)) if param.key in ("fps", "fps_calm", "season") else value


class Settings:
    """Valeurs de lancement + choix faits dans le menu (ces derniers ont priorité)."""

    def __init__(self, args, store=None):
        self.store = QSettings("yuki", "yuki") if store is None else store
        self.sim = None
        self.battery_listener = None
        self.launch = {p.key: clean(p, getattr(args, p.key)) for p in PARAMS}
        self.values = dict(self.launch)
        self.saved = []
        for p in PARAMS:
            raw = self.store.value(p.key)
            if raw is None:
                continue
            try:
                self.values[p.key] = clean(p, raw)
                self.saved.append(p.key)
            except (TypeError, ValueError):
                self.store.remove(p.key)   # fichier abîmé : on oublie ce réglage
        self.launch_on_battery = bool(getattr(args, "on_battery", False))
        self.on_battery = self.launch_on_battery
        raw = self.store.value("on_battery")
        if raw is not None:                # true/false en texte ou booléen selon la plateforme
            text = str(raw).lower()
            if text in ("true", "false"):
                self.on_battery = text == "true"
                self.saved.append("on_battery")
            else:
                self.store.remove("on_battery")

    @property
    def frame_ms(self):
        return max(5, int(1000 / self.values["fps"]))

    @property
    def calm_ms(self):
        """Quand rien de rapide ne bouge : KWin paie chaque image, même pour de la neige qui tombe."""
        calm = self.values["fps_calm"]
        return self.frame_ms if calm <= 0 else max(self.frame_ms, int(1000 / calm))

    def attach(self, sim):
        self.sim = sim
        for key in self.values:
            self._apply(key)

    def _apply(self, key):
        if self.sim is None:
            return
        if key == "density":
            self.sim.set_density(self.values[key])
        elif key == "season":
            self.sim.set_season(self.values[key])
        elif key in SIM_ATTRS:
            setattr(self.sim, SIM_ATTRS[key], self.values[key])

    def set(self, key, value):
        value = clean(BY_KEY[key], value)
        self.values[key] = value
        if same_value(value, self.launch[key]):
            self.store.remove(key)         # retour à la valeur de lancement : rien à retenir
        else:
            self.store.setValue(key, value)
        self.store.sync()
        self._apply(key)

    def set_on_battery(self, value):
        value = bool(value)
        changed = value != self.on_battery
        self.on_battery = value
        if value == self.launch_on_battery:
            self.store.remove("on_battery")
        else:
            self.store.setValue("on_battery", value)
        self.store.sync()
        if changed and self.battery_listener is not None:
            self.battery_listener(value)

    def reset(self):
        self.store.clear()
        self.store.sync()
        self.saved = []
        self.values = dict(self.launch)
        for key in self.values:
            self._apply(key)
        self.set_on_battery(self.launch_on_battery)
