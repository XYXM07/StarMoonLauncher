"""Small cached project thumbnails, fetched outside the UI thread."""
import hashlib
import urllib.parse
import urllib.request
import uuid
import weakref
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from .core import SSL_CONTEXT, USER_AGENT
from .themes import color

MAX_BYTES = 3 * 1024 * 1024


def decode_preview(payload):
    buffer = QBuffer()
    buffer.setData(QByteArray(payload))
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid() or max(size.width(), size.height()) > 8192 or size.width() * size.height() > 16000000:
        return QImage()
    reader.setScaledSize(size.scaled(QSize(192, 192), Qt.KeepAspectRatio))
    return reader.read()


class ImageSignals(QObject):
    ready = Signal(str, object)


class ImageJob(QRunnable):
    def __init__(self, url, path):
        super().__init__()
        self.url, self.path = url, path
        self.signals = ImageSignals()

    def run(self):
        result = QImage()
        try:
            if self.path.is_file() and self.path.stat().st_size <= MAX_BYTES:
                result = decode_preview(self.path.read_bytes())
            if result.isNull():
                request = urllib.request.Request(self.url, headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
                with urllib.request.urlopen(request, timeout=7, context=SSL_CONTEXT) as response:
                    if urllib.parse.urlparse(response.geturl()).scheme != "https":
                        raise ValueError("Image redirect must use HTTPS")
                    payload = response.read(MAX_BYTES + 1)
                    if len(payload) > MAX_BYTES:
                        raise ValueError("Image is too large")
                result = decode_preview(payload)
                if not result.isNull():
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = self.path.with_suffix(".tmp-" + uuid.uuid4().hex)
                    try:
                        temporary.write_bytes(payload)
                        temporary.replace(self.path)
                    finally:
                        if temporary.exists():
                            temporary.unlink()
        except Exception:
            pass  # An unavailable picture must not hide the project or block installation.
        self.signals.ready.emit(self.url, result)


class ImageLoader(QObject):
    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = Path(root) / "cache" / "images"
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.cache, self.waiters, self.jobs = {}, {}, {}
        QApplication.instance().aboutToQuit.connect(self.pool.clear)

    def path_for(self, url):
        return self.root / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".image")

    def load(self, url, preview):
        parsed = urllib.parse.urlparse(url or "")
        if parsed.scheme != "https" or not parsed.hostname:
            return
        if url in self.cache:
            preview.set_image(self.cache[url])
            return
        self.waiters.setdefault(url, []).append(weakref.ref(preview))
        if url in self.jobs:
            return
        job = ImageJob(url, self.path_for(url))
        job.signals.ready.connect(self.received)
        self.jobs[url] = job
        self.pool.start(job)

    def received(self, url, image):
        self.jobs.pop(url, None)
        if not image.isNull():
            if len(self.cache) >= 128:
                self.cache.pop(next(iter(self.cache)))
            self.cache[url] = image
        for reference in self.waiters.pop(url, []):
            preview = reference()
            if preview is not None and isValid(preview):
                preview.set_image(image)


class ProjectPreview(QWidget):
    def __init__(self, loader, url="", size=72, parent=None):
        super().__init__(parent)
        self.setObjectName("project-preview")
        self.setFixedSize(size, size)
        self.image = QImage()
        self.source_url = url or ""
        self.setProperty("loaded", False)
        loader.load(self.source_url, self)

    def set_image(self, image):
        self.image = image
        self.setProperty("loaded", not image.isNull())
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color("surface",180))
        painter.drawRoundedRect(self.rect(), 9, 9)
        if self.image.isNull():
            icon = QApplication.windowIcon().pixmap(round(self.width() * .6))
            painter.drawPixmap((self.width()-icon.width())//2, (self.height()-icon.height())//2, icon)
            return
        clip = QPainterPath()
        clip.addRoundedRect(self.rect(), 9, 9)
        painter.setClipPath(clip)
        size = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
        painter.drawImage((self.width()-size.width())//2, (self.height()-size.height())//2,
                          self.image.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
