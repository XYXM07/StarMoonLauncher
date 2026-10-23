"""Native frosted glass painting, sharing a cached backdrop without widget grabs."""
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QApplication, QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene
from .themes import palette, color, blend


def blur_backdrop(pixmap, radius=24):
    # Render once per background/size change, never once per hover or page frame.
    pad = radius * 2
    extended = QPixmap(pixmap.width() + pad * 2, pixmap.height() + pad * 2)
    extended.fill(Qt.transparent)
    painter = QPainter(extended)
    painter.drawPixmap(extended.rect(), pixmap)
    painter.drawPixmap(pad, pad, pixmap)
    painter.end()
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(extended)
    scene.addItem(item)
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    effect.setBlurHints(QGraphicsBlurEffect.QualityHint)
    item.setGraphicsEffect(effect)
    result = QImage(pixmap.size(), QImage.Format_ARGB32_Premultiplied)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    scene.render(painter, QRectF(result.rect()), QRectF(pad, pad, pixmap.width(), pixmap.height()))
    painter.end()
    return QPixmap.fromImage(result)


def paint_glass(widget, painter, bounds=None, radius=22, active=False, hover=0.0,
                shadow=True, sample=True, strength=None, danger=False, neutral=False, light_pos=None):
    """Blurred background, subtle refraction, white rim and inset specular light."""
    painter.setRenderHint(QPainter.Antialiasing)
    bounds = QRectF(bounds if bounds is not None else widget.rect())
    bounds.adjust(4 if shadow else 1, 3 if shadow else 1, -4 if shadow else -1, -7 if shadow else -1)
    radius = min(radius, bounds.height() / 2)
    if shadow:
        painter.setPen(Qt.NoPen)
        for spread in range(5, 0, -1):
            painter.setBrush(QColor(33, 80, 132, 3 if spread > 2 else 5))
            painter.drawRoundedRect(bounds.adjusted(-spread*.65, 2-spread*.4, spread*.65, 3+spread*.4), radius+spread, radius+spread)
    path = QPainterPath()
    path.addRoundedRect(bounds, radius, radius)
    painter.save()
    painter.setClipPath(path)
    refraction = (QApplication.instance().property("glassRefraction") or 0) / 100
    if sample:
        surface = widget.parentWidget()
        while surface is not None and not hasattr(surface, "backdrop"):
            surface = surface.parentWidget()
        if surface is not None:
            backdrop = surface.backdrop(blurred=True)
            if not backdrop.isNull():
                origin = widget.mapTo(surface, QPoint())
                source = bounds.translated(origin)
                source.adjust(1, 1, -1, -1)
                painter.save()
                painter.setOpacity(painter.opacity() * .6)
                painter.drawPixmap(bounds, backdrop, source)
                if refraction:
                    # One continuous edge lens, with a single outer stroke.
                    edge = min(3, radius*.22)
                    inside = QPainterPath()
                    inside.addRoundedRect(bounds.adjusted(edge,edge,-edge,-edge),max(1,radius-edge),max(1,radius-edge))
                    painter.setClipPath(path.subtracted(inside),Qt.IntersectClip)
                    lens_source = bounds.translated(origin)
                    dx,dy = min(8*refraction,bounds.width()*.10),min(6*refraction,bounds.height()*.10)
                    lens_source.adjust(dx,dy,-dx,-dy)
                    painter.drawPixmap(bounds,surface.backdrop(),lens_source)
                painter.restore()
    setting = QApplication.instance().property("glassOpacity")
    strength = (setting if setting is not None else 72) / 100 if strength is None else strength
    strength = max(0, min(1.2, strength))
    white = round(6 + strength * 60)
    gradient = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
    theme = palette()
    if theme["dark"]:
        tint = round((strength-.72)*60)
        gradient.setColorAt(0,color("surface",max(0,110+tint)))
        gradient.setColorAt(.45,color("surface",max(0,75+tint)))
        gradient.setColorAt(1,color("surface",max(0,95+tint)))
    else:
        gradient.setColorAt(0,QColor(255,255,255,white+24))
        gradient.setColorAt(.45,QColor(255,255,255,max(0,white-12 if active else white-24)))
        gradient.setColorAt(1,color("surface",max(0,white-8)))
    painter.fillPath(path, gradient)
    if hover:
        painter.fillPath(path,color("accent",round(hover*16)) if theme["dark"] else QColor(255,255,255,round(hover*32)))
    if danger:
        painter.fillPath(path, QColor(255, 224, 228, 95))
    # Broad upper sheen, then an asymmetric rim (bright top, shaded lower edge).
    sheen = QLinearGradient(0, bounds.top(), 0, bounds.bottom())
    sheen.setColorAt(0, QColor(255,255,255,18 if theme["dark"] else 70))
    sheen.setColorAt(.32, QColor(255,255,255,0))
    sheen.setColorAt(1, QColor(255,255,255,12))
    painter.fillPath(path, sheen)
    # A broad, off-centre reflection reads as curved glass, with no glare
    # painted across text. Its intensity follows the existing hover animation.
    light = QRadialGradient(light_pos if light_pos is not None else bounds.topLeft() + QPoint(round(bounds.width()*.24), 0), max(30, bounds.width()*.7))
    light.setColorAt(0,QColor(255,255,255,round((10 if theme["dark"] else 24)+hover*20)))
    light.setColorAt(1, QColor(255,255,255,0))
    painter.fillPath(path, light)
    painter.restore()
    rim = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
    rim.setColorAt(0, QColor(255,255,255,90 if theme["dark"] else 195))
    rim.setColorAt(.3, QColor(255,255,255,100) if neutral else QColor(228,247,255,100))
    rim.setColorAt(.52, QColor(255,255,255,55) if neutral else QColor(95,157,218,48))
    rim.setColorAt(.75, QColor(255,255,255,70 if theme["dark"] else 215))
    rim.setColorAt(1, QColor(255,255,255,55 if theme["dark"] else 145))
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(rim, 1.15))
    painter.drawPath(path)

