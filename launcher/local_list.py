"""Bounded native card widgets for a long list inside the page's scroll area."""
import time

from PySide6.QtCore import QEvent, QPoint, QSize, QTimer
from PySide6.QtWidgets import QSizePolicy, QWidget


class VirtualCardList(QWidget):
    def __init__(self, scroll, factory, parent=None):
        super().__init__(parent)
        self.scroll, self.factory = scroll, factory
        self.items, self.rows = [], {}
        self.row_height = max(184, self.fontMetrics().height() * 5 + 100)
        self.spacing = 18
        self.setObjectName("local-mod-list")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        self.setFixedHeight(0)
        self.pending = QTimer(self)
        self.pending.setSingleShot(True)
        self.pending.timeout.connect(self.update_visible)
        scroll.verticalScrollBar().valueChanged.connect(self.schedule)
        scroll.verticalScrollBar().rangeChanged.connect(self.schedule)
        scroll.viewport().installEventFilter(self)

    def set_items(self, items):
        self.items = list(items)
        for index in list(self.rows):
            if index >= len(self.items) or self.rows[index][0] != self.items[index]:
                self.retire(index)
        height = max(0, len(self.items) * (self.row_height + self.spacing) - self.spacing)
        self.setFixedHeight(height)
        self.schedule()

    def retire(self, index):
        _, widget = self.rows.pop(index)
        widget.hide()
        widget.deleteLater()

    def schedule(self, *unused):
        if not self.pending.isActive():
            self.pending.start(0)

    def update_visible(self):
        if not self.isVisible():
            return
        top = self.mapFrom(self.scroll.viewport(), QPoint()).y()
        stride = self.row_height + self.spacing
        first = max(0, top // stride - 1)
        end = min(len(self.items), max(first, (top + self.scroll.viewport().height()) // stride + 2))
        for index in list(self.rows):
            if not first <= index < end:
                self.retire(index)
        started = time.perf_counter()
        for index in range(first, end):
            if index not in self.rows:
                # Yield between card batches so the selection animation keeps ticking.
                if time.perf_counter() - started >= .004:
                    self.schedule()
                    break
                widget = self.factory(self.items[index])
                widget.setParent(self)
                widget.setMinimumWidth(0)
                self.rows[index] = (self.items[index], widget)
            widget = self.rows[index][1]
            widget.setGeometry(0, index * stride, self.width(), self.row_height)
            widget.show()

    def sizeHint(self):
        return QSize(0, self.height())

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.schedule()
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self.schedule()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule()
