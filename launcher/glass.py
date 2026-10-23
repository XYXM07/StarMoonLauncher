"""Blue-white glass surfaces and native frameless-window controls."""
import ctypes
import math
import os
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QIcon, QImageReader, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QRegion
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QWidget

from .widgets import Button
from .material import blur_backdrop, paint_glass
from .themes import palette, color, blend


def enable_backdrop(window):
    """Keep the system frame/backdrop out of our per-pixel rounded glass."""
    if os.name != "nt":
        return False
    try:
        hwnd = ctypes.c_void_p(int(window.winId()))
        dwm = ctypes.windll.dwmapi
        # An entire-window Acrylic backdrop paints behind Qt's transparent
        # corners. Use our own material and remove the system's opaque border.
        for attribute, value in ((2, 1), (33, 1), (34, 0xfffffffe), (38, 1)):
            setting = ctypes.c_uint(value)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(setting), 4)
        return True
    except (OSError, AttributeError):
        return False


def update_window_mask(window):
    if window.isMaximized():
        window.clearMask()
    else:
        path = QPainterPath()
        # Slightly outside the antialiased painted curve, so the mask trims
        # child/system drawing without replacing the smooth visible edge.
        path.addRoundedRect(QRectF(window.rect()), 23, 23)
        window.setMask(QRegion(path.toFillPolygon().toPolygon()))


def clear_window(window):
    painter = QPainter(window)
    painter.setCompositionMode(QPainter.CompositionMode_Source)
    painter.fillRect(window.rect(), Qt.transparent)
    painter.end()


def resize_hit(window, message):
    if os.name != "nt":
        return None
    from ctypes import wintypes
    msg = wintypes.MSG.from_address(int(message))
    if msg.message == 0xA3 and msg.wParam == 2:
        return 0
    if window.isMaximized():
        return None
    if msg.message != 0x84:  # WM_NCHITTEST, in physical screen pixels.
        return None
    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(int(window.winId())), ctypes.byref(rect)):
        return None
    x, y = ctypes.c_short(msg.lParam & 0xffff).value, ctypes.c_short((msg.lParam >> 16) & 0xffff).value
    edge = round(7 * window.devicePixelRatioF())
    left, right = x < rect.left + edge, x >= rect.right - edge
    top, bottom = y < rect.top + edge, y >= rect.bottom - edge
    if top and left: return 13
    if top and right: return 14
    if bottom and left: return 16
    if bottom and right: return 17
    if left: return 10
    if right: return 11
    if top: return 12
    if bottom: return 15
    return None


class TitleBar(QWidget):
    def __init__(self, owner, title="", full=False):
        super().__init__(owner)
        self.owner = owner
        self.full = full
        self.setObjectName("topbar")
        self.setFixedHeight(64 if full else 50)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(20, 0, 12, 0)
        self.layout.setSpacing(8)
        if title:
            self.title = QLabel(title)
            self.title.setObjectName("brand" if full else "section")
            self.layout.addWidget(self.title)
        self.controls = []
        self._drag = None

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_glass(self, painter, radius=24, strength=.4)

    def add_controls(self):
        if self.full:
            self.add_control("−", "最小化", self.owner.showMinimized, "window-minimize")
            self.maximize = self.add_control("□", "最大化 / 还原", self.toggle_maximized, "window-maximize")
        callback = self.owner.reject if isinstance(self.owner, QDialog) else self.owner.close
        self.add_control("×", "关闭", callback, "window-close", True)

    def add_control(self, text, tip, callback, name, danger=False):
        button = Button(text, callback, danger=danger)
        button.setMinimumSize(34, 32)
        button.setFixedSize(34, 32)
        button.setObjectName(name)
        button.setToolTip(tip)
        self.layout.addWidget(button)
        self.controls.append(button)
        return button

    def toggle_maximized(self):
        self.owner.showNormal() if self.owner.isMaximized() else self.owner.showMaximized()

    def mouseDoubleClickEvent(self, event):
        self._drag = None
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.owner.windowHandle()
            if handle and handle.startSystemMove():
                event.accept()
                return
            self._drag = event.globalPosition().toPoint() - self.owner.pos()

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.owner.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event):
        self._drag = None


class GlassSurface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("glass-surface")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.image = QPixmap()
        self.background_opacity = 0.55
        self._backdrop = QPixmap()
        self._blurred = QPixmap()
        self._cache_size = None
        self._theme_key = None
        self.glass_opacity = .72
        self.background_key = None

    def set_glass_opacity(self, opacity):
        value = max(0, min(120, opacity)) / 100
        if value != self.glass_opacity:
            self.glass_opacity = value
            self._cache_size = None
            self.update()

    def set_background(self, path, opacity=55):
        key = (str(path), opacity)
        if key == self.background_key:
            return
        self.background_key = key
        self.background_opacity = max(0, min(100, opacity)) / 100
        self.image = QPixmap()
        if path and Path(path).is_file():
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and size.width() * size.height() <= 100000000:
                reader.setScaledSize(size.scaled(2560, 1600, Qt.KeepAspectRatio))
                self.image = QPixmap.fromImage(reader.read())
        self._cache_size = None
        self.update()

    def backdrop(self, blurred=False):
        if self._cache_size != self.size() or self._theme_key != palette()["key"]:
            self._theme_key = palette()["key"]
            self._cache_size = self.size()
            self._backdrop = QPixmap(self.size())
            self._backdrop.fill(Qt.transparent)
            self.paint_background(self._backdrop)
            self._blurred = blur_backdrop(self._backdrop)
        return self._blurred if blurred else self._backdrop

    def paint_background(self, target):
        p = QPainter(target)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        base = QLinearGradient(0, 0, self.width(), self.height())
        theme = palette()
        # Keep saved values visually stable, then extend smoothly to the new limit.
        alpha = round(200 + min(.95,self.glass_opacity)*50 + max(0,self.glass_opacity-.95)*30) if theme["dark"] else round(90 + self.glass_opacity * 100)
        base.setColorAt(0, color("bg",alpha))
        base.setColorAt(.5, color("bg2",alpha-12))
        end = blend(theme["bg"],theme["glow"],.16)
        end.setAlpha(min(255,alpha+5))
        base.setColorAt(1,end)
        p.fillRect(self.rect(), base)
        if not self.image.isNull():
            scaled = self.image.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.setOpacity(self.background_opacity)
            p.drawPixmap((self.width()-scaled.width())//2, (self.height()-scaled.height())//2, scaled)
            p.setOpacity(1)
        else:
            for x, y, radius, glow_color in ((.19, .15, .58, color("surface",35 if theme["dark"] else 95)), (.84, .21, .36, color("glow",45 if theme["dark"] else 85)), (.6, .92, .42, color("accent",30 if theme["dark"] else 65)), (.4, .56, .2, color("bg2",50))):
                gradient = QRadialGradient(QPointF(self.width()*x, self.height()*y), self.width()*radius)
                gradient.setColorAt(0, glow_color)
                gradient.setColorAt(1, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 0))
                p.fillRect(self.rect(), gradient)
            p.setPen(QPen(color("muted",12), .6))
            step = 42
            for x in range(0, self.width(), step):
                p.drawLine(x, 0, x, self.height())
            for y in range(0, self.height(), step):
                p.drawLine(0, y, self.width(), y)
        p.end()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        shape = QPainterPath()
        radius = 0 if self.window().isMaximized() else 24
        shape.addRoundedRect(bounds, radius, radius)
        p.setClipPath(shape)
        p.drawPixmap(0, 0, self.backdrop())


class MoonScene(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 230)
        self.phase = 0
        self.float_animation = QVariantAnimation(self)
        self.float_animation.setStartValue(0.0)
        self.float_animation.setEndValue(math.tau)
        self.float_animation.setDuration(6500)
        self.float_animation.setLoopCount(-1)
        self.float_animation.valueChanged.connect(self.animate)
        self.float_animation.start()

    def animate(self, value):
        self.phase = value
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(self.width()/2, self.height()/2 + math.sin(self.phase)*5)
        scale = min(self.width()/460, self.height()/350)
        p.scale(scale, scale)
        glow = QRadialGradient(QPointF(0,0), 155)
        glow.setColorAt(0, QColor(255,255,255,160)); glow.setColorAt(1, QColor(255,255,255,0))
        p.setPen(Qt.NoPen); p.setBrush(glow); p.drawEllipse(QPointF(0,0), 155,155)
        outer, inner = QPainterPath(), QPainterPath()
        outer.addEllipse(QRectF(-78,-78,156,156)); inner.addEllipse(QRectF(-36,-106,145,145))
        crescent = outer.subtracted(inner)
        gradient = QLinearGradient(-80,-80,80,80)
        gradient.setColorAt(0, QColor(255,255,255,245)); gradient.setColorAt(1, color("accent",210))
        p.setBrush(gradient); p.setPen(QPen(QColor(255,255,255,230),2)); p.drawPath(crescent)
        for x,y,r in ((-130,-64,7),(104,-48,12),(126,73,6),(-105,104,4),(48,105,5)):
            star = QPainterPath()
            star.moveTo(x,y-r); star.quadTo(x+2,y-2,x+r,y); star.quadTo(x+2,y+2,x,y+r)
            star.quadTo(x-2,y+2,x-r,y); star.quadTo(x-2,y-2,x,y-r)
            p.setBrush(QColor(255,255,255,220)); p.setPen(Qt.NoPen); p.drawPath(star)
        p.setPen(QPen(QColor(255,255,255,110),1)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(-175,-118,350,236))


def moon_icon(size=128):
    image = QPixmap(size, size)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size/128, size/128)
    gradient = QLinearGradient(0,0,128,128)
    gradient.setColorAt(0,QColor("#75bdff")); gradient.setColorAt(1,QColor("#4079e6"))
    p.setPen(QPen(QColor(255,255,255,220),2)); p.setBrush(gradient)
    p.drawRoundedRect(QRectF(2,2,124,124),30,30)
    moon, cut = QPainterPath(), QPainterPath()
    moon.addEllipse(QRectF(25,27,71,71)); cut.addEllipse(QRectF(49,14,64,64))
    p.setBrush(QColor("#f6fbff")); p.setPen(Qt.NoPen); p.drawPath(moon.subtracted(cut))
    star = QPainterPath(); star.moveTo(94,58); star.lineTo(98,69); star.lineTo(109,73)
    star.lineTo(98,77); star.lineTo(94,88); star.lineTo(90,77); star.lineTo(79,73); star.lineTo(90,69); star.closeSubpath()
    p.drawPath(star); p.end()
    return QIcon(image)
