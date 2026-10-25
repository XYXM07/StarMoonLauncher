"""Pixel-exact skin preview and avatar, rendered by Qt without a web view."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QWidget
from .material import paint_glass
from .i18n import tr
from .themes import color


def has_head_overlay(image):
    # Old RGB skins use an opaque unused region where modern skins have alpha.
    return image.height()!=32 or any(image.pixelColor(x,y).alpha()<128 for x in range(32,64) for y in range(32))


def skin_avatar(path, size=56):
    image = path if isinstance(path, QImage) else QImage(str(path))
    if image.isNull(): return QPixmap()
    pixmap = QPixmap(size,size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.drawImage(QRectF(0,0,size,size),image,QRectF(8,8,8,8))
    if has_head_overlay(image): painter.drawImage(QRectF(0,0,size,size),image,QRectF(40,8,8,8))
    painter.end()
    return pixmap


class AccountAvatar(QLabel):
    def __init__(self, icon, size=56, parent=None):
        super().__init__(parent)
        self.avatar_size, self.fallback_icon = size, icon
        self.setAlignment(Qt.AlignCenter)
        self.reset()

    def reset(self):
        self.setProperty("imageSourceKey", None)
        self.setPixmap(self.fallback_icon.pixmap(self.avatar_size, self.avatar_size))

    def set_image(self, image):
        if not image.isNull():
            self.setPixmap(skin_avatar(image, self.avatar_size))


class SkinPreview(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.model = "default"
        self.setMinimumSize(280,250)

    def set_skin(self,path,model="default"):
        self.image = QImage(str(path)) if path else QImage()
        self.model = model
        self.update()

    def paintEvent(self,event):
        painter = QPainter(self)
        paint_glass(self,painter,sample=False,strength=.6)
        if self.image.isNull():
            painter.setPen(color("muted"))
            painter.drawText(self.rect(),Qt.AlignCenter,tr("选择 PNG 皮肤"))
            return
        scale = min((self.height()-55)/32,(self.width()-80)/40)
        slim = self.model == "slim"
        arm = 3 if slim else 4
        modern = self.image.height()==64
        for back in (False,True):
            painter.save()
            painter.translate(self.width()*(.7 if back else .3)-8*scale,20)
            painter.scale(scale,scale)
            def part(x,y,w,h,u,v,flip=False):
                tile = self.image.copy(u,v,w,h)
                if flip: tile=tile.mirrored(True,False)
                painter.drawImage(QRectF(x,y,w,h),tile)
            part(4,0,8,8,24 if back else 8,8)
            if has_head_overlay(self.image): part(4,0,8,8,56 if back else 40,8)
            part(4,8,8,12,32 if back else 20,20)
            part(1 if slim else 0,8,arm,12,44+arm+4 if back else 44,20)
            part(12,8,arm,12,36+arm+4 if back else 36,52) if modern else part(12,8,arm,12,44+arm+4 if back else 44,20,True)
            part(4,20,4,12,12 if back else 4,20)
            part(8,20,4,12,28 if back else 20,52) if modern else part(8,20,4,12,12 if back else 4,20,True)
            if modern:
                part(4,8,8,12,32 if back else 20,36)
                part(1 if slim else 0,8,arm,12,44+arm+4 if back else 44,36)
                part(12,8,arm,12,52+arm+4 if back else 52,52)
                part(4,20,4,12,12 if back else 4,36)
                part(8,20,4,12,12 if back else 4,52)
            painter.restore()
        painter.setPen(color("muted"))
        painter.drawText(QRectF(0,self.height()-30,self.width()*.6,20),Qt.AlignCenter,tr("正面"))
        painter.drawText(QRectF(self.width()*.4,self.height()-30,self.width()*.6,20),Qt.AlignCenter,tr("背面"))
