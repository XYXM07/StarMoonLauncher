"""Native Qt widgets, animation and wheel routing shared by every desktop page."""
from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QObject, QPoint, QPointF, QRectF, QSize,
                            Property, QPropertyAnimation, Qt, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap, QTextLayout, QTextOption
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSpinBox, QApplication, QButtonGroup, QComboBox, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QPushButton, QScrollArea, QSizePolicy, QProxyStyle, QStyle,
                               QVBoxLayout, QWidget)
from .material import paint_glass
from .motion import entrance
from .themes import color, stylesheet

ACCENT = "#4b91e8"

STYLE = """
QWidget { color:#294768; font-family:'Microsoft YaHei UI','Segoe UI'; font-size:13px; }
QMainWindow, QDialog { background:transparent; }
QPushButton { background:transparent; border:none; }
QWidget#sidebar, QWidget#topbar { background:transparent; border:none; }
QWidget#page, QScrollArea, QScrollArea>QWidget>QWidget { background:transparent; border:none; }
QFrame#card, QFrame#project-card, QFrame#version-row, QFrame#softCard { background:transparent; border:none; }
QFrame#notice { background:rgba(215,236,255,180); border:1px solid rgba(255,255,255,210); border-radius:10px; }
QLabel { background:transparent; border:none; }
QLabel#title { font-size:27px; font-weight:700; color:#244b7f; }
QLabel#subtitle { color:#6f8aaa; font-size:12px; }
QLabel#section { font-size:16px; font-weight:650; }
QLabel#muted { color:#6f88a3; font-size:11px; }
QLabel#badge { color:#3174c7; background:rgba(214,235,255,180); border-radius:6px; padding:4px 8px; font-size:10px; }
QLabel#heroTitle { font-size:37px; font-weight:700; color:#3568a1; }
QLabel#brand { font-size:21px; font-weight:750; color:#285b94; }
QLabel#fieldLabel { color:#5e7fa2; font-size:12px; }
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {
 background:rgba(255,255,255,75); border:1px solid rgba(255,255,255,195); border-radius:13px; padding:9px 11px;
 selection-background-color:#b8d9ff; selection-color:#24476a; min-height:19px;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus { border:1px solid #80b3f4; background:rgba(255,255,255,215); }
QComboBox::drop-down { border:none; width:25px; }
QComboBox {background:transparent;border:none;}
QSpinBox::up-button,QSpinBox::down-button {border:none;background:transparent;}
QComboBox QAbstractItemView { background:#f3f9ff; selection-background-color:#d7eaff; color:#294f77; border:1px solid #c4dcf7; padding:5px; }
QListWidget { background:rgba(255,255,255,95); border:1px solid rgba(255,255,255,200); border-radius:18px; padding:9px; outline:none; }
QListWidget::item { padding:12px; margin:2px; border-radius:7px; }
QListWidget::item:selected { background:#d3e8ff; color:#2865ac; }
QListWidget::item:hover { background:#e8f3ff; }
QScrollBar:vertical { background:transparent; width:10px; margin:5px 1px; }
QScrollBar::handle:vertical { background:rgba(113,163,221,125); border-radius:4px; min-height:32px; }
QScrollBar::handle:vertical:hover { background:#7ca9de; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QProgressBar { border:0; background:transparent; color:transparent; }
QCheckBox, QRadioButton { spacing:9px; padding:4px 0; }
QCheckBox::indicator { width:17px; height:17px; border:1px solid #b8d0ed; border-radius:5px; background:rgba(255,255,255,180); }
QCheckBox::indicator:checked { background:#6aa2eb; border-color:#5c96e1; image:none; }
QRadioButton::indicator { width:17px; height:17px; border:1px solid rgba(116,165,218,160); border-radius:9px; background:rgba(255,255,255,140); }
QRadioButton::indicator:checked { border:1px solid #6ca5ea; background:qradialgradient(cx:0.5, cy:0.5, fx:0.5, fy:0.5, radius:0.5, stop:0 #6ca5ea, stop:0.45 #6ca5ea, stop:0.5 white, stop:1 white); }
QRadioButton:disabled { color:#8a9daf; }
QTabWidget::pane { border:0; }
QTabBar::tab { padding:12px 17px; background:transparent; color:#7793b1; border-bottom:2px solid transparent; }
QTabBar::tab:selected { color:#3c7bca; border-bottom:2px solid #76a8ea; }
QSlider::groove:horizontal { height:5px; background:rgba(176,205,240,140); border-radius:2px; }
QSlider::sub-page:horizontal { background:#81b1f0; border-radius:2px; }
QSlider::handle:horizontal { background:#5495e6; border:2px solid white; width:17px; margin:-7px 0; border-radius:10px; }
QToolTip { background:#f4f9ff; color:#315a84; border:1px solid #c5dcf7; padding:7px; }
"""


def glass_style(opacity=72):
    QApplication.instance().setProperty("glassOpacity", max(0, min(120, opacity)))
    return stylesheet(STYLE)


class FocusFreeStyle(QProxyStyle):
    def drawPrimitive(self, element, option, painter, widget=None):
        if element != QStyle.PE_FrameFocusRect:
            super().drawPrimitive(element,option,painter,widget)


def remove_focus_rings():
    app = QApplication.instance()
    if not isinstance(app.style(),FocusFreeStyle):
        app.setStyle(FocusFreeStyle("Fusion"))


class Button(QPushButton):
    def __init__(self, text, callback=None, primary=False, danger=False, parent=None):
        super().__init__(text, parent)
        self.primary, self.danger = primary, danger
        self._hover = 0.0
        self._press = 0.0
        self._pointer = QPointF(12, 8)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(37)
        self.setMinimumWidth(82)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.animation = QPropertyAnimation(self, b"hoverProgress", self)
        self.animation.setDuration(240)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.press_animation = QPropertyAnimation(self, b"pressProgress", self)
        self.pressed.connect(lambda: self.animate_press(True))
        self.released.connect(lambda: self.animate_press(False))
        if callback:
            self.clicked.connect(callback)

    def get_hover(self):
        return self._hover

    def set_hover(self, value):
        self._hover = value
        self.update()

    hoverProgress = Property(float, get_hover, set_hover)

    def get_press(self):
        return self._press

    def set_press(self, value):
        self._press = value
        self.update()

    pressProgress = Property(float, get_press, set_press)

    def animate_press(self, down):
        self.press_animation.stop()
        if QApplication.instance().property("animations") is False:
            self.set_press(1.0 if down else 0.0)
            return
        self.press_animation.setDuration(85 if down else 350)
        self.press_animation.setEasingCurve(QEasingCurve.OutCubic if down else QEasingCurve.OutBack)
        self.press_animation.setStartValue(self._press)
        self.press_animation.setEndValue(1.0 if down else 0.0)
        self.press_animation.start()

    def mouseMoveEvent(self, event):
        self._pointer = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.EnabledChange and not self.isEnabled():
            self.animation.stop()
            self.press_animation.stop()
            self._press = self._hover = 0.0
        super().changeEvent(event)

    def enterEvent(self, event):
        if hasattr(event, "position"):
            self._pointer = event.position()
        self.animation.stop()
        if QApplication.instance().property("animations") is False:
            self.set_hover(1.0)
            return super().enterEvent(event)
        self.animation.setStartValue(self._hover)
        self.animation.setEndValue(1.0)
        self.animation.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.animation.stop()
        if QApplication.instance().property("animations") is False:
            self.set_hover(0.0)
            return super().leaveEvent(event)
        self.animation.setStartValue(self._hover)
        self.animation.setEndValue(0.0)
        self.animation.start()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        value = self._hover if self.isEnabled() else 0
        painter.setOpacity(1.0 if self.isEnabled() else .45)
        press = self._press if self.isEnabled() else 0
        inset = 2 * press
        rect = QRectF(self.rect()).adjusted(inset, 1.4*press, -inset, -1.0*press)
        paint_glass(self, painter, rect, radius=22, active=self.primary,
                    hover=value, shadow=False, sample=False, strength=.55, danger=self.danger,
                    neutral=True, light_pos=self._pointer)
        rect = rect.adjusted(1,1,-1,-1)
        painter.setFont(self.font())
        painter.setPen(color("danger" if self.danger else "primary" if self.primary else "text"))
        painter.drawText(rect, Qt.AlignCenter, self.fontMetrics().elidedText(self.text(), Qt.ElideRight, max(1, int(rect.width()-18))))

    def sizeHint(self):
        size = super().sizeHint()
        size.setWidth(max(82, self.fontMetrics().horizontalAdvance(self.text()) + 32))
        size.setHeight(39)
        return size


class NavigationSwitch(QWidget):
    """Exclusive navigation segments sharing one glass track and a moving selection."""
    selected = Signal(str)

    class Segment(Button):
        def paintEvent(self,event):
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            font = self.font()
            font.setBold(self.isChecked())
            p.setFont(font)
            p.setPen(color("primary" if self.isChecked() else "text"))
            p.drawText(QRectF(self.rect()).translated(0,self._press),Qt.AlignCenter,self.text())

    def __init__(self,items,parent=None):
        super().__init__(parent)
        self.setObjectName("top-switch")
        self.setFixedHeight(44)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons,self.current = {},""
        self.pill = QRectF()
        self.motion = QVariantAnimation(self)
        self.motion.valueChanged.connect(self._move_pill)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4,3,4,3)
        layout.setSpacing(1)
        for key,text in items:
            button = self.Segment(text)
            button.setCheckable(True)
            button.setObjectName("top-"+key)
            button.clicked.connect(lambda checked=False,k=key:self._clicked(k))
            self.group.addButton(button)
            self.buttons[key] = button
            layout.addWidget(button)

    def _clicked(self,key):
        if key != self.current:
            self.selected.emit(key)

    def _move_pill(self,value):
        self.pill = value
        self.update()

    def select(self,key,animated=True):
        self.current = key
        self.buttons[key].setChecked(True)
        for name,button in self.buttons.items():
            button.primary = name == key
            button.update()
        target = QRectF(self.buttons[key].geometry()).adjusted(0,1,0,-1)
        self.motion.stop()
        if animated and not self.pill.isEmpty() and QApplication.instance().property("animations") is not False:
            self.motion.setDuration(280)
            self.motion.setEasingCurve(QEasingCurve.OutCubic)
            self.motion.setStartValue(self.pill)
            self.motion.setEndValue(target)
            self.motion.start()
        else:
            self._move_pill(target)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.current:
            self.layout().activate()
            self.select(self.current,False)

    def paintEvent(self,event):
        p = QPainter(self)
        paint_glass(self,p,radius=23,shadow=False,sample=False,strength=.30,neutral=True)
        if not self.pill.isEmpty():
            p.setPen(Qt.NoPen)
            p.setBrush(color("accent",30))
            p.drawRoundedRect(self.pill,18,18)
            paint_glass(self,p,self.pill,radius=18,shadow=False,sample=False,active=True,strength=.85,neutral=True)


class SmoothScroll(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setObjectName("page")
        self.body = QWidget()
        self.body.setObjectName("page")
        self.layout = QVBoxLayout(self.body)
        self.layout.setContentsMargins(32, 27, 32, 25)
        self.layout.setSpacing(19)
        self.layout.setAlignment(Qt.AlignTop)
        self.setWidget(self.body)


class WheelRouter(QObject):
    """Route wheel events from labels/combos/cards to the nearest scrollable ancestor."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.animations = {}
        self.enabled = True

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Wheel or not isinstance(watched, QWidget):
            return False
        if watched.window().property("comboPopup"):
            return False
        delta = event.pixelDelta().y() or event.angleDelta().y() / 120 * 105
        if not delta:
            return False
        current = watched
        while current:
            if isinstance(current, QAbstractScrollArea):
                bar = current.verticalScrollBar()
                if bar.maximum() > 0 and ((delta < 0 and bar.value() < bar.maximum()) or (delta > 0 and bar.value() > 0)):
                    key = id(bar)
                    animation = self.animations.get(key)
                    if animation is None:
                        animation = QPropertyAnimation(bar, b"value", bar)
                        animation.setDuration(155)
                        animation.setEasingCurve(QEasingCurve.OutCubic)
                        self.animations[key] = animation
                        bar.destroyed.connect(lambda _=None, k=key: self.animations.pop(k, None))
                    start = animation.endValue() if animation.state() == QAbstractAnimation.Running else bar.value()
                    target = max(bar.minimum(), min(bar.maximum(), int(start-delta)))
                    animation.stop()
                    if self.enabled and not event.pixelDelta().y():
                        animation.setStartValue(bar.value())
                        animation.setEndValue(target)
                        animation.start()
                    else:
                        bar.setValue(target)
                    event.accept()
                    return True
            current = current.parentWidget()
        if isinstance(watched, (QComboBox, QAbstractSpinBox)):
            event.accept()
            return True
        return False


class PageHost(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.page = None
        self.animation = None

    def switch(self, page, animated=True, direction=1, vertical=False):
        if self.animation:
            self.animation.stop()
            self.animation.deleteLater()
            self.animation = None
        if self.page:
            self.page.hide()
            self.page.deleteLater()
        self.page = page
        if page.parentWidget() is not self:
            page.setParent(self)
        page.setGeometry(self.rect())
        page.ensurePolished()
        page.layout.activate()
        if animated:
            offset = QPoint(0, 22*direction) if vertical else QPoint(34*direction, 0)
            self.animation = entrance(page, parent=self, offset=offset)
            self.animation.finished.connect(lambda: page.setGeometry(self.rect()) if page is self.page else None)
        page.show()
        if self.animation:
            self.animation.start()
        page.setFocus(Qt.OtherFocusReason)

    def resizeEvent(self, event):
        if self.page:
            if self.animation and self.animation.state() == QAbstractAnimation.Running:
                self.animation.stop()
                self.page.graphicsEffect().setOpacity(1.0)
            self.page.setGeometry(self.rect())
        super().resizeEvent(event)


def label(text, name="", wrap=False):
    widget = QLabel(str(text))
    widget.setTextFormat(Qt.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


class ElidedLabel(QLabel):
    def __init__(self, text, name="", parent=None):
        super().__init__(parent)
        self.full_text = str(text)
        self.setTextFormat(Qt.PlainText)
        self.setToolTip(self.full_text)
        self.setObjectName(name)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def resizeEvent(self, event):
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.ElideRight, self.contentsRect().width()))
        super().resizeEvent(event)


class SummaryLabel(QLabel):
    """Two complete lines with an ellipsis, independent of a card's width."""
    def __init__(self, text, lines=2, parent=None):
        super().__init__(parent)
        self.full_text = " ".join(str(text).split())
        self.lines = lines
        self.setObjectName("muted")
        self.setToolTip(str(text))
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.ensurePolished()
        self.setFixedHeight(self.fontMetrics().lineSpacing() * lines + 2)

    def sizeHint(self):
        return QSize(150, self.fontMetrics().lineSpacing() * self.lines + 2)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(color("muted"))
        metrics = self.fontMetrics()
        layout = QTextLayout(self.full_text, self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        for index in range(self.lines):
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, self.width()))
            start = line.textStart()
            text = self.full_text[start:start + line.textLength()].strip()
            if index == self.lines - 1:
                text = metrics.elidedText(self.full_text[start:].strip(), Qt.ElideRight, self.width())
            painter.drawText(0, metrics.ascent() + index * metrics.lineSpacing(), text)
        layout.endLayout()


class GlassFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover = 0.0
        self.hover_animation = QVariantAnimation(self)
        self.hover_animation.setDuration(180)
        self.hover_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.hover_animation.valueChanged.connect(self._animate_hover)

    def _animate_hover(self, value):
        self._hover = value
        self.update()

    def _hover_to(self, value):
        if self.objectName() != "version-row":
            return
        self.hover_animation.stop()
        if QApplication.instance().property("animations") is False:
            self._animate_hover(value)
        else:
            self.hover_animation.setStartValue(self._hover)
            self.hover_animation.setEndValue(value)
            self.hover_animation.start()

    def enterEvent(self, event):
        self._hover_to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover_to(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_glass(self, painter, active=bool(self.property("selected")) or self.objectName() == "softCard",
                    hover=self._hover, strength=.45 if self.objectName() == "sidebar" else None)


def card(soft=False):
    frame = GlassFrame()
    frame.setObjectName("softCard" if soft else "card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(13)
    return frame, layout


class VersionRow(GlassFrame):
    selected = Signal()

    def __init__(self, chosen=False):
        super().__init__()
        self.setObjectName("version-row")
        self.setProperty("selected", chosen)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.selected.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.selected.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class Landscape(QWidget):
    """Small native vector illustration; no HTML canvas or browser is involved."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 210)
        self._phase = 0.0
        self.float_animation = QVariantAnimation(self)
        self.float_animation.setStartValue(0.0)
        self.float_animation.setEndValue(1.0)
        self.float_animation.setDuration(4500)
        self.float_animation.setLoopCount(-1)
        self.float_animation.valueChanged.connect(self._move)
        self.float_animation.start()

    def _move(self, value):
        self._phase = value
        self.update()

    def paintEvent(self, event):
        import math
        from PySide6.QtGui import QPolygonF
        from PySide6.QtCore import QPointF
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        scale = min(self.width()/430, self.height()/290)
        p.translate((self.width()-430*scale)/2, (self.height()-290*scale)/2 + math.sin(self._phase*math.pi*2)*3)
        p.scale(scale, scale)
        p.setPen(Qt.NoPen)
        def poly(points, color):
            p.setBrush(QColor(color));p.drawPolygon(QPolygonF([QPointF(x,y) for x,y in points]))
        poly([(44,200),(215,102),(391,202),(218,285)],"#d5dfca")
        poly([(55,157),(219,63),(384,157),(219,252)],"#a3bf7c")
        poly([(55,157),(219,252),(219,278),(55,184)],"#8e9f72")
        poly([(219,252),(384,157),(384,183),(219,278)],"#748d63")
        poly([(55,157),(219,252),(219,260),(55,165)],"#bfd19b")
        poly([(219,252),(384,157),(384,165),(219,260)],"#a5bf83")
        poly([(185,196),(243,162),(285,186),(227,220)],"#a7d4ce")
        poly([(227,220),(245,210),(245,265),(227,276)],"#9dc9c0")
        poly([(134,130),(218,82),(278,116),(194,165)],"#bad39b")
        poly([(134,130),(194,165),(194,185),(134,151)],"#95b577")
        poly([(194,165),(278,116),(278,137),(194,185)],"#7b9d65")
        for x,y in ((137,117),(295,154)):
            poly([(x-5,y),(x+4,y+5),(x+4,y+39),(x-5,y+34)],"#9a8968")
            poly([(x+4,y+5),(x+10,y+1),(x+10,y+35),(x+4,y+39)],"#7e7656")
            poly([(x-32,y-24),(x+1,y-43),(x+34,y-24),(x+1,y-5)],"#9dbe74")
            poly([(x-32,y-24),(x+1,y-5),(x+1,y+24),(x-32,y+5)],"#7fa25d")
            poly([(x+1,y-5),(x+34,y-24),(x+34,y+5),(x+1,y+24)],"#698d53")
            poly([(x-23,y-41),(x+1,y-55),(x+25,y-41),(x+1,y-27)],"#bfd694")
            poly([(x-23,y-41),(x+1,y-27),(x+1,y-9),(x-23,y-23)],"#a1c17a")
            poly([(x+1,y-27),(x+25,y-41),(x+25,y-23),(x+1,y-9)],"#86aa67")
        poly([(283,87),(310,71),(347,92),(320,108)],"#e0e9d5")
        poly([(65,58),(90,44),(119,60),(94,75)],"#e3ecd9")
