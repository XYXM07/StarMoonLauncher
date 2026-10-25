"""Small cached project thumbnails, fetched outside the UI thread."""
import hashlib
import time
import urllib.parse
import urllib.request
import uuid
import weakref
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QRunnable, QRectF, QSize, Qt, QThreadPool, Signal
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
    def __init__(self, url, path=None, resolver=None, local=False, skin=False):
        super().__init__()
        self.url, self.path = url, path
        self.resolver, self.local, self.skin = resolver, local, skin
        self.signals = ImageSignals()

    def run(self):
        result = QImage()
        try:
            source = self.resolver() if self.resolver else self.url
            if self.local:
                path = Path(source)
                if path.is_file() and path.stat().st_size <= MAX_BYTES:
                    payload = path.read_bytes()
                    if self.skin:
                        buffer = QBuffer()
                        buffer.setData(QByteArray(payload)); buffer.open(QIODevice.ReadOnly)
                        reader = QImageReader(buffer)
                        if bytes(reader.format()).lower() == b"png" and reader.size().width() == 64 and reader.size().height() in (32, 64):
                            result = reader.read()
                    else:
                        result = decode_preview(payload)
                self.signals.ready.emit(self.url, result)
                return
            parsed = urllib.parse.urlsplit(source)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Invalid image URL")
            path = self.path if not self.resolver else self.path.parent / (hashlib.sha256(source.encode()).hexdigest() + ".image")
            if path.is_file() and path.stat().st_size <= MAX_BYTES:
                result = decode_preview(path.read_bytes())
            if result.isNull():
                request = urllib.request.Request(source, headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
                with urllib.request.urlopen(request, timeout=7, context=SSL_CONTEXT) as response:
                    if urllib.parse.urlparse(response.geturl()).scheme != "https":
                        raise ValueError("Image redirect must use HTTPS")
                    payload = response.read(MAX_BYTES + 1)
                    if len(payload) > MAX_BYTES:
                        raise ValueError("Image is too large")
                result = decode_preview(payload)
                if not result.isNull():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix(".tmp-" + uuid.uuid4().hex)
                    try:
                        temporary.write_bytes(payload)
                        temporary.replace(path)
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
        self.failed = {}
        QApplication.instance().aboutToQuit.connect(self.pool.clear)

    def path_for(self, url):
        return self.root / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".image")

    def load(self, url, preview):
        parsed = urllib.parse.urlparse(url or "")
        if parsed.scheme != "https" or not parsed.hostname:
            return
        self.enqueue(url, preview, ImageJob(url, self.path_for(url)))

    def load_local(self, path, preview):
        path = Path(path).resolve()
        if path.parent != self.root.parent / "mod-previews" or path.suffix.lower() != ".png":
            return
        key = "local:" + str(path)
        self.enqueue(key, preview, ImageJob(key, resolver=lambda: str(path), local=True))

    def load_project(self, provider, project, resolver, preview):
        key = "project:" + provider + ":" + str(project)
        self.enqueue(key, preview, ImageJob(key, self.path_for(key), resolver=resolver))

    def load_avatar(self, service, account, preview):
        key = "avatar:" + str(account["id"]) + ":" + str(account.get("skin_url", ""))
        self.enqueue(key, preview, ImageJob(key, resolver=lambda: service.account_skin(dict(account)), local=True, skin=True))

    def enqueue(self, url, preview, job):
        preview.setProperty("imageSourceKey", url)
        if url in self.cache:
            preview.set_image(self.cache[url])
            return
        if time.monotonic() - self.failed.get(url, -1000) < 300:
            return
        self.waiters.setdefault(url, []).append(weakref.ref(preview))
        if url in self.jobs:
            return
        job.signals.ready.connect(self.received)
        self.jobs[url] = job
        self.pool.start(job)

    def received(self, url, image):
        self.jobs.pop(url, None)
        if not image.isNull():
            if len(self.cache) >= 128:
                self.cache.pop(next(iter(self.cache)))
            self.cache[url] = image
            self.failed.pop(url, None)
        else:
            if len(self.failed) >= 2048:
                self.failed.pop(next(iter(self.failed)))
            self.failed[url] = time.monotonic()
        for reference in self.waiters.pop(url, []):
            preview = reference()
            if preview is not None and isValid(preview) and preview.property("imageSourceKey") == url:
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
        clip = QPainterPath()
        clip.addRoundedRect(self.rect(), 9, 9)
        painter.setClipPath(clip)
        if self.image.isNull():
            icon = self.window().windowIcon()
            if icon.isNull():
                icon = QApplication.windowIcon()
            image = icon.pixmap(self.size()).toImage()
            if not image.isNull():
                # Cover the entire slot while preserving aspect ratio and rounded corners.
                ratio = max(self.width() / image.width(), self.height() / image.height())
                width, height = self.width() / ratio, self.height() / ratio
                source = QRectF((image.width()-width)/2, (image.height()-height)/2, width, height)
                painter.drawImage(QRectF(self.rect()), image, source)
            return
        size = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
        painter.drawImage((self.width()-size.width())//2, (self.height()-size.height())//2,
                          self.image.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
