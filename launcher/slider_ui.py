"""Native liquid glass sliders with precise dragging and standard keyboard input."""
from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QSlider, QStyle
from .material import paint_glass
from .themes import color


class GlassSlider(QSlider):
    dragStarted = Signal()
    dragFinished = Signal(int)
    dragCanceled = Signal()

    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation,parent)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(38)
        self.setAutoFillBackground(False)
        self._press = 0.0
        self._hover = False
        self._offset = 0.0
        self._dragging = False
        self.motion = QVariantAnimation(self)
        self.motion.valueChanged.connect(self._set_press)

    def sizeHint(self):
        return QSize(180,38)

    def minimumSizeHint(self):
        return QSize(90,38)

    def _set_press(self, value):
        self._press = value
        self.update()

    def _upside_down(self):
        return self.invertedAppearance() ^ (self.layoutDirection() == Qt.RightToLeft)

    def handle_center(self):
        span = max(1,self.width()-52)
        offset = QStyle.sliderPositionFromValue(self.minimum(),self.maximum(),self.sliderPosition(),span,self._upside_down())
        return QPointF(26+offset,self.height()/2)

    def _animate(self, pressed):
        self.motion.stop()
        if QApplication.instance().property("animations") is False:
            self._set_press(1.0 if pressed else 0.0)
            return
        self.motion.setDuration(90 if pressed else 320)
        self.motion.setEasingCurve(QEasingCurve.OutCubic if pressed else QEasingCurve.OutBack)
        self.motion.setStartValue(self._press)
        self.motion.setEndValue(1.0 if pressed else 0.0)
        self.motion.start()

    def _move_to(self,x):
        span = max(1,self.width()-52)
        position = max(0,min(span,round(x-self._offset-26)))
        value = QStyle.sliderValueFromPosition(self.minimum(),self.maximum(),position,span,self._upside_down())
        self.setSliderPosition(value)
        self.update()

    def mousePressEvent(self,event):
        if event.button() != Qt.LeftButton or self.orientation() != Qt.Horizontal:
            return super().mousePressEvent(event)
        self.setFocus(Qt.MouseFocusReason)
        center = self.handle_center()
        self._offset = event.position().x()-center.x() if abs(event.position().x()-center.x()) <= 18 else 0.0
        self._dragging = True
        self.dragStarted.emit()
        self.setSliderDown(True)
        self._animate(True)
        self._move_to(event.position().x())
        event.accept()

    def mouseMoveEvent(self,event):
        if self._dragging and self.orientation() == Qt.Horizontal:
            self._move_to(event.position().x())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._move_to(event.position().x())
            if not self.hasTracking():
                self.setValue(self.sliderPosition())
            self.setSliderDown(False)
            self._dragging = False
            self.dragFinished.emit(self.value())
            self._animate(False)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self,event):
        # The page may scroll, but wheel/trackpad gestures never change settings.
        event.ignore()

    def enterEvent(self,event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self,event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def changeEvent(self,event):
        if event.type() == QEvent.EnabledChange and not self.isEnabled() and hasattr(self,"motion"):
            self.motion.stop()
            self._press = 0.0
            self.cancel_drag()
        super().changeEvent(event)

    def cancel_drag(self):
        if self._dragging:
            self._dragging = False
            self.motion.stop()
            self._press = 0.0
            self.setSliderDown(False)
            self.dragCanceled.emit()
            self.update()

    def hideEvent(self,event):
        self.cancel_drag()
        super().hideEvent(event)

    def paintEvent(self,event):
        if self.orientation() != Qt.Horizontal:
            return super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(1 if self.isEnabled() else .4)
        center = self.handle_center()
        track = QRectF(26,self.height()/2-4,max(1,self.width()-52),8)
        p.setPen(QPen(QColor(255,255,255,100),.8))
        p.setBrush(color("surface",100))
        p.drawRoundedRect(track,4,4)
        fill = QRectF(track)
        if self._upside_down():
            fill.setLeft(center.x())
        else:
            fill.setRight(center.x())
        gradient = QLinearGradient(fill.topLeft(),fill.bottomLeft())
        gradient.setColorAt(0,color("accent",185))
        gradient.setColorAt(1,color("accent",85))
        p.setBrush(gradient)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(fill,4,4)
        width = 28 + 16*self._press + (4 if self._hover and not self.isSliderDown() else 0)
        height = 27 - 2*self._press
        thumb = QRectF(center.x()-width/2,center.y()-height/2,width,height)
        paint_glass(self,p,thumb,radius=16,neutral=True,sample=True,shadow=False,hover=.8 if self._hover else .2,strength=.65)
        if self._hover or self.isSliderDown():
            font = QFont(self.font())
            font.setPixelSize(10)
            p.setFont(font)
            p.setPen(color("text"))
            value = self.sliderPosition()
            text = "{:.1f}".format(value/1024) if self.maximum() > 1024 else str(value)
            p.drawText(thumb,Qt.AlignCenter,text)
        else:
            p.setPen(QPen(QColor(255,255,255,170),1.0))
            p.drawLine(QPointF(center.x()-2,center.y()-4),QPointF(center.x()-2,center.y()+4))
            p.drawLine(QPointF(center.x()+2,center.y()-4),QPointF(center.x()+2,center.y()+4))
