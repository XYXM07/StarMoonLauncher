"""Glass light-guide progress, retaining QProgressBar's exact data semantics."""
import math

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QProgressBar
from .themes import color, palette


class GlassProgress(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(18)
        self.setTextVisible(False)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self._fraction = 0.0
        self._phase = 0.0
        self._active = True
        self._settle = QVariantAnimation(self)
        self._settle.setDuration(220)
        self._settle.setEasingCurve(QEasingCurve.OutCubic)
        self._settle.valueChanged.connect(self._set_fraction)
        self._flow = QVariantAnimation(self)
        self._flow.setStartValue(0.0)
        self._flow.setEndValue(math.tau)
        self._flow.setDuration(1900)
        self._flow.setLoopCount(-1)
        self._flow.valueChanged.connect(self._set_phase)
        self.valueChanged.connect(self._value_changed)

    def _animations(self):
        return QApplication.instance().property("animations") is not False

    def _target(self):
        span = self.maximum() - self.minimum()
        return min(1.0, max(0.0, (self.value()-self.minimum()) / span)) if span else 0.0

    def _set_fraction(self, value):
        self._fraction = value
        self.update()

    def _set_phase(self, value):
        self._phase = value
        self.update()

    def _sync_flow(self):
        busy = self.minimum() == self.maximum()
        flowing = self.isVisible() and self._active and self._animations() and (busy or 0 < self._target() < 1)
        if flowing and self._flow.state() != QAbstractAnimation.Running:
            self._flow.start()
        elif not flowing:
            self._flow.stop()
        if not self._animations():
            self._settle.stop()
            self._fraction = self._target()
            self._phase = 0.0

    def setActivity(self, active):
        self._active = bool(active)
        if not active:
            self._settle.stop()
            self._fraction = self._target()
        self._sync_flow()
        self.update()

    def setRange(self, minimum, maximum):
        changed = (minimum, maximum) != (self.minimum(), self.maximum())
        super().setRange(minimum, maximum)
        if hasattr(self, "_settle"):
            if changed:
                self._settle.stop()
                self._fraction = self._target()
            self._sync_flow()

    def _value_changed(self, _value):
        target = self._target()
        self._settle.stop()
        # Retries and phase changes reset immediately; increasing real bytes
        # settle gently, without ever painting beyond the current true value.
        if self.isVisible() and self._animations() and self._fraction < target:
            self._settle.setStartValue(self._fraction)
            self._settle.setEndValue(target)
            self._settle.start()
        else:
            self._fraction = target
        self._sync_flow()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._fraction = self._target()
        self._sync_flow()

    def hideEvent(self, event):
        self._flow.stop()
        self._settle.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 4, -1, -4)
        radius = rect.height()/2
        track = QPainterPath()
        track.addRoundedRect(rect, radius, radius)
        p.setPen(QPen(QColor(255, 255, 255, 160), .8))
        p.setBrush(QColor(255, 255, 255, 45))
        p.drawPath(track)
        p.setClipPath(track)
        busy = self.minimum() == self.maximum()
        if busy:
            t = (math.sin(self._phase-math.pi/2)+1)/2 if self._animations() else .5
            fill = QRectF(rect.left() + t*rect.width()*.68, rect.top(), rect.width()*.32, rect.height())
        else:
            fill = QRectF(rect.left(), rect.top(), rect.width()*min(self._fraction, self._target()), rect.height())
        if fill.width() <= 0:
            return
        shape = QPainterPath()
        shape.addRoundedRect(fill, radius, radius)
        p.setClipPath(shape, Qt.IntersectClip)
        complete = not busy and self._target() >= 1
        fill_color = color("success" if complete else "accent")
        body = QLinearGradient(fill.topLeft(), fill.bottomLeft())
        body.setColorAt(0, QColor(min(255,fill_color.red()+30),min(255,fill_color.green()+30),min(255,fill_color.blue()+18),185))
        body.setColorAt(.5, QColor(fill_color.red(),fill_color.green(),fill_color.blue(),190))
        body.setColorAt(1, QColor(fill_color.red(),fill_color.green(),fill_color.blue(),125))
        p.fillPath(shape, body)
        if self._active and not complete:
            glint_x = fill.left() + (self._phase/math.tau)*fill.width()
            glow = QRadialGradient(glint_x, fill.center().y(), max(18, fill.width()*.25))
            glow.setColorAt(0, QColor(255, 255, 255, 155))
            glow.setColorAt(1, QColor(255, 255, 255, 0))
            p.fillPath(shape, glow)
            wave = QPainterPath()
            for i in range(81):
                x = fill.left() + fill.width()*i/80
                y = fill.center().y() + math.sin(i*.14-self._phase)*1.15
                wave.moveTo(x,y) if i == 0 else wave.lineTo(x,y)
            p.setPen(QPen(QColor(255,255,255,155), 1.1))
            p.drawPath(wave)

