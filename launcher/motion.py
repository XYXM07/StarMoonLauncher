"""Shared native transition timing for pages, sidebars and dialog content."""
from PySide6.QtCore import (QEasingCurve, QPoint, QParallelAnimationGroup,
                            QPropertyAnimation, QSequentialAnimationGroup)
from PySide6.QtWidgets import QGraphicsOpacityEffect

PAGE_DURATION = 380


def entrance(widget, *, parent=None, offset=QPoint(), duration=PAGE_DURATION, delay=0):
    """Create before showing; position and opacity always settle exactly."""
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    group = QParallelAnimationGroup(parent or widget)
    fade = QPropertyAnimation(effect, b"opacity", group)
    fade.setDuration(duration)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setEasingCurve(QEasingCurve.OutCubic)
    group.addAnimation(fade)
    if not offset.isNull():
        destination = widget.pos()
        widget.move(destination + offset)
        slide = QPropertyAnimation(widget, b"pos", group)
        slide.setDuration(duration)
        slide.setStartValue(destination + offset)
        slide.setEndValue(destination)
        slide.setEasingCurve(QEasingCurve.OutQuint)
        group.addAnimation(slide)
    if delay:
        sequence = QSequentialAnimationGroup(parent or widget)
        sequence.addPause(delay)
        sequence.addAnimation(group)
        return sequence
    return group

