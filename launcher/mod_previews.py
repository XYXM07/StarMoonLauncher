"""Bounded, read-only mod metadata and raster thumbnails; never loads Java code."""
import hashlib
import copy
import json
import re
import threading
import time
import uuid
import zipfile
from pathlib import Path

from .core import LauncherError, valid_id
from .providers import plain, https_url
from .mod_sites import metadata_sites


class ModPreviews:
    def __init__(self, content):
        self.content = content
        self.root = content.store.root / "cache/mod-previews"
        self.cache, self.projects, self.lock = {}, {}, threading.RLock()

    def local(self, path):
        result = {"mod_name": "", "mod_id": "", "mod_version": "", "description": "", "image_path": "", "websites": []}
        try:
            path = Path(path)
            info = path.stat()
            original = path.name[:-9] if path.name.endswith(".disabled") else path.name
            key = hashlib.sha256((str(path.parent.resolve() / original) + "\0" + str(info.st_size) + "\0" + str(info.st_mtime_ns)).encode()).hexdigest()
            with self.lock:
                if key in self.cache:
                    return copy.deepcopy(self.cache[key])
            if info.st_size > 512 * 1024 ** 2:
                return result
            with zipfile.ZipFile(path) as archive:
                if len(archive.infolist()) > 100000:
                    return result
                def read(name, limit=1024 * 1024):
                    entry = archive.getinfo(name)
                    if entry.file_size > limit or entry.is_dir():
                        raise ValueError("mod metadata/image too large")
                    return archive.read(entry)
                names = set(archive.namelist())
                icons, metadata = [], []
                if "litemod.json" in names:
                    lite = json.loads(read("litemod.json").decode("utf-8-sig"))
                    metadata.append(dict(lite, name=lite.get("displayName") or lite.get("name", "")))
                if "fabric.mod.json" in names:
                    metadata.append(json.loads(read("fabric.mod.json").decode("utf-8-sig")))
                if "quilt.mod.json" in names:
                    quilt = json.loads(read("quilt.mod.json").decode("utf-8-sig")).get("quilt_loader", {})
                    metadata.append(dict(quilt.get("metadata", {}), id=quilt.get("id", ""), version=quilt.get('version', '')))
                for name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                    if name in names:
                        import tomli
                        data = tomli.loads(read(name).decode("utf-8-sig"))
                        for mod in data.get("mods", []):
                            metadata.append(dict(mod, name=mod.get("displayName", ""), icon=mod.get("logoFile") or data.get("logoFile", ""),
                                                 issueTrackerURL=mod.get("issueTrackerURL") or data.get("issueTrackerURL", "")))
                if "mcmod.info" in names:
                    legacy = json.loads(read("mcmod.info").decode("utf-8-sig"))
                    for mod in (legacy if isinstance(legacy, list) else legacy.get("modList", [])):
                        metadata.append(dict(mod, icon=mod.get("logoFile", "")))
                for data in metadata:
                    result["mod_name"] = result["mod_name"] or plain(data.get("name"), 160)
                    version = str(data.get('version') or '')
                    if not result['mod_version'] and '${' not in version: result['mod_version'] = version[:100]
                    identifier = str(data.get("id") or data.get("modId") or data.get("modid") or "")
                    if not result["mod_id"] and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", identifier):
                        result["mod_id"] = identifier
                    for link in metadata_sites(data):
                        if link not in result["websites"] and len(result["websites"]) < 16:
                            result["websites"].append(link)
                    result["description"] = result["description"] or plain(data.get("description"), 400)
                    icon = data.get("icon", "")
                    if isinstance(icon, dict):
                        icons.extend(value for _, value in sorted(icon.items(), key=lambda item:
                            (0 if int(item[0]) >= 128 else 1, abs(int(item[0]) - 128)) if str(item[0]).isdigit() else (2, 10000)))
                    elif isinstance(icon, str):
                        icons.append(icon)
                icons.extend(("icon.png", "logo.png", "pack.png"))
                for icon in icons:
                    if not isinstance(icon, str):
                        continue
                    icon = icon.replace("\\", "/").lstrip("/")
                    if ":" in icon or ".." in icon.split("/") or icon not in names:
                        continue
                    from .images import decode_preview
                    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
                    from PySide6.QtGui import QImage, QImageReader, QPainter
                    try:
                        payload = read(icon, 3 * 1024 ** 2)
                    except (ValueError, KeyError, OSError, zipfile.BadZipFile):
                        continue
                    buffer = QBuffer()
                    buffer.setData(QByteArray(payload)); buffer.open(QIODevice.ReadOnly)
                    if bytes(QImageReader(buffer).format()).lower() not in (b"png", b"jpeg", b"jpg", b"webp", b"gif", b"bmp"):
                        continue
                    image = decode_preview(payload)
                    if image.isNull():
                        continue
                    clean = QImage(image.size(), QImage.Format_ARGB32)
                    clean.fill(0)
                    painter = QPainter(clean); painter.drawImage(0, 0, image); painter.end()
                    self.root.mkdir(parents=True, exist_ok=True)
                    target = self.root / (key + ".png")
                    temporary = target.with_suffix(".tmp-" + uuid.uuid4().hex)
                    try:
                        if not clean.save(str(temporary), "PNG"):
                            continue
                        temporary.replace(target)
                    finally:
                        if temporary.exists():
                            temporary.unlink()
                    result["image_path"] = str(target)
                    break
        except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, RuntimeError):
            pass
        with self.lock:
            if len(self.cache) >= 2048:
                self.cache.pop(next(iter(self.cache)))
            if "key" in locals():
                self.cache[key] = copy.deepcopy(result)
        return result

    def project_image(self, provider, project):
        if provider not in ("modrinth", "curseforge"):
            return ""
        project = valid_id(str(project))
        key = provider, project
        with self.lock:
            cached = self.projects.get(key)
            if cached and time.monotonic() - cached[0] < 300:
                return cached[1]
        try:
            if provider == "modrinth":
                data = self.content.net.json("https://api.modrinth.com/v2/project/" + project)
                url = data.get("icon_url") or ""
            else:
                data = self.content.curse("/mods/" + project)["data"]
                url = (data.get("logo") or {}).get("thumbnailUrl") or (data.get("logo") or {}).get("url") or ""
            if url:
                url = https_url(url)
        except (LauncherError, OSError, ValueError, TypeError, KeyError, AttributeError):
            url = ""
        with self.lock:
            if len(self.projects) >= 2048:
                self.projects.pop(next(iter(self.projects)))
            self.projects[key] = (time.monotonic(), url)
        return url
