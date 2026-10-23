"""Shared color tokens for native glass, controls and application styles."""
import re
from functools import lru_cache
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPalette, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget


SCHEMES = {
    "starmoon": {"name": "星月蓝白", "colors": ("#F6FBFF", "#E1F1FF", "#8AB8F0", "#4B91E8"), "dark": False},
    "aurora": {"name": "极光绿", "colors": ("#04151F", "#0B3B3C", "#1F7A6D", "#7CFFB2"), "dark": True},
    "morandi": {"name": "莫兰迪灰粉", "colors": ("#EDE4E0", "#D6C6C9", "#B8A9C9", "#6B5B95"), "dark": False},
    "graphite": {"name": "石墨黑金", "colors": ("#0E0E10", "#1C1C22", "#2B2B33", "#E8C77B"), "dark": True},
    "glacier": {"name": "冰川蓝", "colors": ("#0A84FF", "#5AC8FA", "#E6F7FF", "#007AFF"), "dark": False},
    "twilight": {"name": "暮光紫粉", "colors": ("#5E5CE6", "#BF5AF2", "#FF9EB5", "#FF375F"), "dark": True},
    "night": {"name": "夜空深色", "colors": ("#0B0F1A", "#1B2440", "#2E3A66", "#64D2FF"), "dark": True},
}


def blend(first, second, amount):
    a, b = QColor(first), QColor(second)
    return QColor(*(round(x*(1-amount)+y*amount) for x,y in zip(a.getRgb()[:3],b.getRgb()[:3])))


def palette(scheme=None):
    app = QApplication.instance()
    key = scheme or (app.property("colorScheme") if app else None) or "starmoon"
    return _tokens(key)


@lru_cache(maxsize=16)
def _tokens(key):
    data = SCHEMES.get(key, SCHEMES["starmoon"])
    a,b,c,d = data["colors"]
    dark = data["dark"]
    bg, bg2, accent, glow = QColor(a), QColor(b), QColor(d), QColor(c)
    if key == "glacier":
        bg, bg2, glow = QColor(c), blend(c,b,.23), QColor(a)
    elif key == "twilight":
        bg, bg2, accent, glow = blend(a,"#0B0F1A",.66), blend(b,"#0B0F1A",.74), QColor(c), QColor(d)
    text = blend(bg,"#FFFFFF",.93) if dark else blend(QColor(d),"#182331",.64)
    muted = blend(bg,"#FFFFFF",.68) if dark else blend(text,b,.40)
    surface = blend(bg,b,.55) if dark else blend(bg,"#FFFFFF",.65)
    return {"key":key, "dark":dark, "bg":bg, "bg2":bg2, "accent":accent, "glow":glow,
            "surface":surface, "text":text, "muted":muted,
            "primary":accent if dark else blend(accent,text,.42),
            "danger":QColor("#FFABB7" if dark else "#A04D64"),
            "success":QColor("#7CFFB2" if dark else "#3A9A88")}


def color(role, alpha=None):
    result = QColor(palette()[role])
    if alpha is not None:
        result.setAlpha(alpha)
    return result


def rgba(value, alpha):
    c = QColor(value)
    return "rgba({},{},{},{})".format(c.red(),c.green(),c.blue(),alpha)


def stylesheet(base):
    p = palette()
    text, muted, accent = p["text"].name(), p["muted"].name(), p["accent"].name()
    replacements = {}
    for value in ("#294768","#244b7f","#24476a","#294f77","#285b94","#3568a1","#315a84"):
        replacements[value] = text
    for value in ("#6f8aaa","#6f88a3","#5e7fa2","#7793b1"):
        replacements[value] = muted
    for value in ("#3174c7","#80b3f4","#7ca9de","#6aa2eb","#5c96e1","#6ca5ea","#3c7bca","#76a8ea","#81b1f0","#5495e6"):
        replacements[value] = accent
    for value in ("#b8d9ff","#d7eaff","#d3e8ff","#e8f3ff","#b8d0ed","#f4f9ff","#f3f9ff","#c4dcf7","#c5dcf7"):
        replacements[value] = blend(p["surface"],p["accent"],.17).name()
    base = re.sub(r"#[0-9a-fA-F]{6}", lambda m: replacements.get(m.group(0).lower(),m.group(0)),base)
    if p["dark"]:
        base = re.sub(r"rgba\(255,255,255,(\d+)\)",lambda m: rgba(p["surface"], min(230,int(m.group(1))+35)),base)
    base += "\nQWidget { color:%s; } QLabel#badge {color:%s;background:%s;} QLineEdit:focus,QSpinBox:focus,QPlainTextEdit:focus {background:%s;} QToolTip {background:%s;color:%s;}" % (
        text, p["primary"].name(),rgba(p["accent"],40),rgba(p["surface"],220),p["bg2"].name(),text)
    base += "\nQListView,QListWidget {outline:none;}"
    return base


def apply(scheme):
    app = QApplication.instance()
    app.setProperty("colorScheme",scheme if scheme in SCHEMES else "starmoon")
    p = palette()
    native = QPalette(app.palette())
    for role in (QPalette.WindowText,QPalette.Text,QPalette.ButtonText,QPalette.ToolTipText):
        native.setColor(role,p["text"])
    for role in (QPalette.Window,QPalette.Base,QPalette.Button,QPalette.ToolTipBase):
        native.setColor(role,p["surface"])
    native.setColor(QPalette.Highlight,p["accent"])
    native.setColor(QPalette.HighlightedText,p["bg"] if p["dark"] else p["text"])
    app.setPalette(native)


class PalettePreview(QWidget):
    def __init__(self,scheme="starmoon",parent=None):
        super().__init__(parent)
        self.scheme = scheme if scheme in SCHEMES else "starmoon"
        self.setMinimumHeight(64)

    def set_scheme(self,scheme):
        self.scheme = scheme if scheme in SCHEMES else "starmoon"
        self.update()

    def paintEvent(self,event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        step = self.width()/4
        for index,value in enumerate(SCHEMES[self.scheme]["colors"]):
            rect = QRectF(index*step+2,3,step-8,54)
            c = QColor(value)
            p.setPen(QPen(QColor(255,255,255,100),.8))
            p.setBrush(c)
            p.drawRoundedRect(rect,11,11)
            p.setPen(QColor("#FFFFFF" if c.lightnessF()<.52 else "#25313E"))
            p.drawText(rect,Qt.AlignCenter,value)
