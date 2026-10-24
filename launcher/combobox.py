"""Rounded, searchable native dropdowns with keyboard and popup scrolling."""
from PySide6.QtCore import (QEvent, QPoint, QPointF, QRectF, QSize, QSortFilterProxyModel, Qt, QPropertyAnimation)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF, QRegion
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFrame, QLineEdit,
                               QListView, QStyledItemDelegate, QStyle, QVBoxLayout, QLayout, QSizePolicy)
from .material import paint_glass
from .i18n import tr
from .themes import color, palette


class MenuDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(180,38)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        enabled = bool(index.flags() & Qt.ItemIsEnabled)
        rect = QRectF(option.rect).adjusted(3,2,-3,-2)
        if selected or hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(color("accent",45 if selected else 24))
            painter.drawRoundedRect(rect,10,10)
        painter.setFont(option.font)
        painter.setPen(color("text") if enabled else color("muted"))
        text = str(index.data(Qt.DisplayRole) or "")
        text = option.fontMetrics.elidedText(text,Qt.ElideRight,int(rect.width()-46))
        painter.drawText(rect.adjusted(12,0,-30,0),Qt.AlignVCenter|Qt.AlignLeft,text)
        if selected:
            painter.setPen(QPen(color("accent"),1.8))
            x,y = rect.right()-20,rect.center().y()
            painter.drawPolyline(QPolygonF([QPointF(x-4,y),QPointF(x-1,y+3),QPointF(x+5,y-4)]))
        painter.restore()


class ComboPopup(QFrame):
    def __init__(self, owner):
        super().__init__(owner,Qt.Popup|Qt.FramelessWindowHint|Qt.NoDropShadowWindowHint)
        self.owner = owner
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Closing a Qt.Popup on its anchor must consume the outside click.
        # Otherwise Qt replays it to the combo and opens the menu again.
        self.setAttribute(Qt.WA_NoMouseReplay)
        self.setProperty("comboPopup",True)
        self.setObjectName("combo-popup")

    def paintEvent(self,event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(),Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.Antialiasing)
        gradient = QLinearGradient(0,0,self.width(),self.height())
        gradient.setColorAt(0,color("surface",252))
        gradient.setColorAt(1,color("bg2",250))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(255,255,255,235),1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1,1,-1,-1),16,16)

    def resizeEvent(self,event):
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()),15,15)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        super().resizeEvent(event)

    def hideEvent(self,event):
        self.owner.popup_animation.stop()
        self.owner.update()
        super().hideEvent(event)

    def mousePressEvent(self,event):
        if not self.rect().contains(event.position().toPoint()):
            self.setAttribute(Qt.WA_NoMouseReplay)
            self.owner.hidePopup()
            event.accept()
            return
        super().mousePressEvent(event)


class GlassComboBox(QComboBox):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(39)
        self.setMinimumWidth(0)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Fixed)
        self.popup = ComboPopup(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model())
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(0)
        self.search = QLineEdit(self.popup)
        self.search.setPlaceholderText(tr("搜索选项…"))
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.search.installEventFilter(self)
        self.menu = QListView(self.popup)
        self.menu.setModel(self.proxy)
        self.menu.setItemDelegate(MenuDelegate(self.menu))
        self.menu.setMouseTracking(True)
        self.menu.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.menu.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.menu.setTextElideMode(Qt.ElideRight)
        self.menu.setStyleSheet("QListView {background:transparent;border:none;outline:none;} QListView::item {padding:0;}")
        self.menu.installEventFilter(self)
        self.menu.clicked.connect(self.choose)
        layout = QVBoxLayout(self.popup)
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        layout.setContentsMargins(8,8,8,8)
        layout.setSpacing(6)
        layout.addWidget(self.search)
        layout.addWidget(self.menu)
        self.popup_animation = QPropertyAnimation(self.popup,b"windowOpacity",self)
        self.popup_animation.setDuration(140)

    def view(self):
        return self.menu

    def sizeHint(self):
        return QSize(180,max(39,self.fontMetrics().height()+18))

    def minimumSizeHint(self):
        return QSize(90,39)

    def paintEvent(self,event):
        painter = QPainter(self)
        paint_glass(self,painter,shadow=False,sample=False,radius=13,active=self.hasFocus() or self.popup.isVisible(),strength=.72,neutral=True)
        painter.setOpacity(1 if self.isEnabled() else .45)
        painter.setFont(self.font())
        painter.setPen(color("text"))
        painter.drawText(self.rect().adjusted(12,0,-35,0),Qt.AlignVCenter|Qt.AlignLeft,
                         self.fontMetrics().elidedText(self.currentText(),Qt.ElideRight,max(1,self.width()-48)))
        x,y = self.width()-20,self.height()/2
        direction = -1 if self.popup.isVisible() else 1
        painter.setPen(QPen(color("muted"),1.6))
        painter.drawPolyline(QPolygonF([QPointF(x-4,y-direction*2),QPointF(x,y+direction*2),QPointF(x+4,y-direction*2)]))

    def showPopup(self):
        if not self.count() or not self.isEnabled(): return
        if self.popup.isVisible(): return
        self.ensurePolished()
        self.popup.ensurePolished()
        for row in range(self.count()):
            self.setItemData(row,self.itemText(row),Qt.ToolTipRole)
        self.search.clear()
        searchable = self.count()>10
        self.search.setVisible(searchable)
        self.proxy.setSourceModel(self.model())
        current = self.proxy.mapFromSource(self.model().index(self.currentIndex(),0))
        self.menu.setCurrentIndex(current)
        screen = self.screen().availableGeometry()
        width = min(max(self.width(),200,min(520,max(self.fontMetrics().horizontalAdvance(self.itemText(i))+56 for i in range(self.count())))),screen.width()-16)
        height = min(8,self.count())*38+18+(45 if searchable else 0)
        height = min(height,screen.height()-24)
        origin = self.mapToGlobal(QPoint(0,self.height()+5))
        if origin.y()+height > screen.bottom()-8:
            origin.setY(self.mapToGlobal(QPoint()).y()-height-5)
        origin.setX(max(screen.left()+8,min(origin.x(),screen.right()-width-8)))
        origin.setY(max(screen.top()+8,origin.y()))
        self.popup.setFixedSize(width,height)
        self.popup.move(origin)
        self.popup.layout().activate()
        self.menu.doItemsLayout()
        self.menu.scrollTo(current,QAbstractItemView.PositionAtCenter)
        animated = QApplication.instance().property("animations") is not False
        self.popup_animation.stop()
        self.popup.setWindowOpacity(0 if animated else 1)
        self.popup.show()
        if animated:
            self.popup_animation.setStartValue(0.0)
            self.popup_animation.setEndValue(1.0)
            self.popup_animation.start()
        (self.search if searchable else self.menu).setFocus()
        self.update()

    def mousePressEvent(self,event):
        if event.button() == Qt.LeftButton:
            self.setFocus(Qt.MouseFocusReason)
            self.hidePopup() if self.popup.isVisible() else self.showPopup()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        if event.button() == Qt.LeftButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def hidePopup(self):
        self.popup_animation.stop()
        self.popup.hide()
        super().hidePopup()
        self.update()

    def choose(self,index):
        source = self.proxy.mapToSource(index)
        if not source.isValid() or not source.flags() & Qt.ItemIsEnabled: return
        row = source.row()
        self.hidePopup()
        self.setCurrentIndex(row)
        self.activated.emit(row)
        self.textActivated.emit(self.itemText(row))

    def eventFilter(self,watched,event):
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self.hidePopup(); self.setFocus(); return True
            if event.key() in (Qt.Key_Return,Qt.Key_Enter):
                self.choose(self.menu.currentIndex()); return True
            if watched is self.search and event.key() == Qt.Key_Down:
                self.menu.setCurrentIndex(self.proxy.index(0,0)); self.menu.setFocus(); return True
        return super().eventFilter(watched,event)

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key_Space,Qt.Key_Return,Qt.Key_Enter,Qt.Key_F4) or event.key() == Qt.Key_Down and event.modifiers() & Qt.AltModifier:
            self.hidePopup() if self.popup.isVisible() else self.showPopup()
            event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self,event):
        event.ignore()

    def hideEvent(self,event):
        self.hidePopup()
        super().hideEvent(event)
