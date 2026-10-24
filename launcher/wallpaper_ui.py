"""Clickable wallpaper previews using the launcher's glass button motion."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QImageReader, QPainter, QPainterPath, QPixmap
from .themes import color
from .i18n import tr
from .widgets import Button


class WallpaperTile(Button):
    def __init__(self, name, path, selected, callback):
        super().__init__("", callback)
        self.name = name
        self.setCheckable(True)
        self.setChecked(selected)
        self.setAccessibleName(name)
        self.setToolTip(name)
        self.setFixedHeight(118)
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid(): reader.setScaledSize(size.scaled(560,220,Qt.KeepAspectRatio))
        self.preview = QPixmap.fromImage(reader.read())

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        inset = 2*self._press
        rect = QRectF(self.rect()).adjusted(6+inset,6+inset,-6-inset,-30-inset)
        clip = QPainterPath()
        clip.addRoundedRect(rect,12,12)
        painter.save()
        painter.setClipPath(clip)
        if not self.preview.isNull():
            image = self.preview.scaled(rect.size().toSize(),Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation)
            painter.drawPixmap(rect.center()-QRectF(image.rect()).center(),image)
        painter.restore()
        painter.setPen(color("accent") if self.isChecked() else color("text"))
        painter.setFont(self.font())
        text = ("✓ " if self.isChecked() else "")+self.name
        painter.drawText(QRectF(8,self.height()-29,self.width()-16,25),Qt.AlignCenter,text)
