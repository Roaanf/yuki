"""Fenêtre overlay transparente, une par écran."""

from PyQt6.QtCore import QEvent, QRect, QRectF, Qt
from PyQt6.QtGui import (QBackingStore, QPainter, QPlatformSurfaceEvent, QRegion, QSurface,
                         QSurfaceFormat, QWindow)

from .core import TILE


class SnowView(QWindow):
    """Overlay transparent. On gère nous-mêmes le QBackingStore (au lieu de QRasterWindow) :
    quand KWin/LayerShellQt recrée la surface (changement de fréquence d'écran, etc.),
    l'ancien backing store pointe vers une fenêtre Wayland détruite et Qt plante.
    Ici on le jette dès que la surface va disparaître et on en recrée un neuf."""

    def __init__(self, sim, rect):
        super().__init__()
        self.sim = sim
        self.view = QRectF(rect)
        self.store = None
        self.blank = False
        self.frozen = False
        self.needs_full = True
        self.setSurfaceType(QSurface.SurfaceType.RasterSurface)
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        self.setFormat(fmt)
        self.setFlags(Qt.WindowType.FramelessWindowHint
                      | Qt.WindowType.WindowTransparentForInput
                      | Qt.WindowType.WindowStaysOnTopHint
                      | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setTitle("Yuki")
        self.setGeometry(rect)

    def event(self, e):
        t = e.type()
        if t == QEvent.Type.PlatformSurface:
            if e.surfaceEventType() == QPlatformSurfaceEvent.SurfaceEventType.SurfaceAboutToBeDestroyed:
                self.store = None
            self.needs_full = True
        elif t == QEvent.Type.Expose:
            self.needs_full = True
            self.render()
        elif t == QEvent.Type.Resize:
            self.needs_full = True
            if self.store is not None:
                self.store.resize(self.size())
        return super().event(e)

    def _region(self, tiles):
        """Tuiles abîmées -> QRegion locale, en fusionnant les tuiles voisines d'une même ligne."""
        vx, vy = int(self.view.x()), int(self.view.y())
        tx0, ty0 = vx // TILE, vy // TILE
        tx1, ty1 = (vx + self.width()) // TILE, (vy + self.height()) // TILE
        rows = {}
        for tx, ty in tiles:
            if tx0 <= tx <= tx1 and ty0 <= ty <= ty1:
                rows.setdefault(ty, []).append(tx)
        rects = []
        for ty in sorted(rows):                 # ordre « y puis x » exigé par setRects
            xs = sorted(rows[ty])
            start = prev = xs[0]
            for tx in xs[1:] + [None]:
                if tx is not None and tx == prev + 1:
                    prev = tx
                    continue
                rects.append(QRect(start * TILE - vx, ty * TILE - vy,
                                   (prev - start + 1) * TILE, TILE))
                if tx is not None:
                    start = prev = tx
        reg = QRegion()
        if rects:
            reg.setRects(rects)
        return reg.intersected(QRect(0, 0, self.width(), self.height()))

    def render(self, full=True, tiles=()):
        if self.frozen or not self.isExposed() or self.width() <= 0 or self.height() <= 0:
            return
        if self.store is None:
            self.store = QBackingStore(self)
            self.store.resize(self.size())
            self.needs_full = True
        whole = QRegion(0, 0, self.width(), self.height())
        if full or self.needs_full:
            area = whole
        else:
            area = self._region(tiles)
            if area.isEmpty():
                return                       # rien n'a bougé ici : pas de dessin, pas d'envoi
        self.needs_full = False
        self.store.beginPaint(area)
        dev = self.store.paintDevice()
        if dev is None:
            self.store.endPaint()
            return
        p = QPainter(dev)
        p.setClipRegion(area)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(0, 0, self.width(), self.height(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if not (self.sim.user_paused or self.sim.hidden_on(self.view)):
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.sim.paint(p, self.view)
        p.end()
        self.store.endPaint()
        self.store.flush(area)

    def dispose(self):
        self.frozen = True
        self.store = None
        self.hide()
        self.destroy()
        self.deleteLater()
