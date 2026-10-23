import base64
import copy
import ctypes
from collections import deque
import hashlib
import json
import os
import platform
import re
import shutil
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

USER_AGENT = "StarMoonLauncher/0.7.5 (native desktop Minecraft launcher)"
MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
MIRROR = "https://bmclapi2.bangbang93.com"
SSL_CONTEXT = ssl.create_default_context()


class LauncherError(Exception):
    pass


class Cancelled(LauncherError):
    pass


def safe_path(root, relative):
    root = Path(root).resolve()
    relative = str(relative).replace("\\", "/")
    if ":" in relative or relative.startswith("/") or ".." in relative.split("/"):
        raise LauncherError("文件路径不安全：" + relative)
    result = (root / relative).resolve()
    if result != root and root not in result.parents:
        raise LauncherError("文件路径超出目标目录")
    return result


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.+\-]{1,120}", value) or value in (".", ".."):
        raise LauncherError("标识只能包含英文、数字、点、加号、横线和下划线")
    return value


def version_id(value):
    """Existing launcher version folders may contain Chinese names and spaces."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 180 or value in (".", "..") or
            value[-1:] in (" ", ".") or re.search(r'[\\/:*?"<>|\x00-\x1f]', value)):
        raise LauncherError("版本文件夹名称无效")
    return value


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():
            tmp.unlink()


def read_json(path, default=None):
    if not Path(path).exists():
        return copy.deepcopy(default)
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def request_json(url, data=None, headers=None, form=False, timeout=35):
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    h.update(headers or {})
    payload = None
    if data is not None:
        payload = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(url, data=payload, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as response:
        return json.load(response)


def mirror_url(url):
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if host == "resources.download.minecraft.net":
        return MIRROR + "/assets" + parsed.path
    if host == "libraries.minecraft.net":
        return MIRROR + "/maven" + parsed.path
    if host in ("launchermeta.mojang.com", "launcher.mojang.com", "piston-meta.mojang.com", "piston-data.mojang.com"):
        return MIRROR + parsed.path
    return url


class Network:
    def __init__(self, config):
        self.config = config
        from .downloader import DownloadEngine
        self.engine = DownloadEngine(config)

    def urls(self, url):
        mirrored = mirror_url(url)
        return list(dict.fromkeys([mirrored, url] if self.config()["source"] == "bmclapi" else [url]))

    def json(self, url):
        error = None
        for endpoint in self.urls(url):
            try:
                return request_json(endpoint)
            except Exception as exc:
                error = exc
        raise LauncherError("无法获取在线数据：{} ({})".format(urllib.parse.urlparse(url).hostname, type(error).__name__))

    def download(self, url, target, sha1=None, sha512=None, task=None, size=None):
        target = Path(target)
        if task:
            from .progress import DownloadProgress, FileProgress
            if not isinstance(task, FileProgress):
                task = DownloadProgress(task, [{"size": size}], target.name, task.progress, 98).file(0)
        algorithm, expected = ("sha512", sha512) if sha512 else ("sha1", sha1)
        if target.is_file():
            if expected:
                digest = hashlib.new(algorithm)
                with target.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        if task:
                            task.check()
                        digest.update(chunk)
                if digest.hexdigest() == expected:
                    if task: task.complete_download(target.stat().st_size)
                    return
            elif size and target.stat().st_size == size:
                if task: task.complete_download(target.stat().st_size)
                return
            elif not expected and size is None and target.stat().st_size > 0:
                # Forge processors create local Maven artifacts without a remote URL/hash.
                if task: task.complete_download(target.stat().st_size)
                return
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part-" + uuid.uuid4().hex)
        last_error = None
        for endpoint in self.urls(url):
            if urllib.parse.urlparse(endpoint).scheme != "https":
                raise LauncherError("仅允许 HTTPS 下载")
            for attempt in range(3):
                try:
                    if task:
                        task.check()
                        task.reset_download()
                    digest = hashlib.new(algorithm)
                    downloaded = self.engine.fetch(endpoint, partial, task, size, bool(expected))
                    with partial.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024*1024), b""):
                            if task:
                                task.check()
                            digest.update(chunk)
                    if expected and digest.hexdigest() != expected:
                        raise LauncherError("文件校验失败：" + target.name)
                    if size is not None and downloaded != size:
                        raise LauncherError("文件大小校验失败：" + target.name)
                    os.replace(str(partial), str(target))
                    if task: task.complete_download(downloaded)
                    return
                except Cancelled:
                    if partial.exists():
                        partial.unlink()
                    raise
                except Exception as exc:
                    last_error = exc
                    if partial.exists():
                        partial.unlink()
                    if task:
                        task.check()
                    if attempt < 2:
                        time.sleep(0.4 * (attempt + 1))
        raise LauncherError("下载失败：{} ({})".format(target.name, str(last_error)[:160]))


class Task:
    def __init__(self, title, operation, payload=None):
        self.id = uuid.uuid4().hex[:12]
        self.title = title
        self.operation = operation
        self.payload = payload or {}
        self.status = "queued"
        self.message = "等待执行"
        self.progress = 0
        self.download = None
        self.bytes = 0
        self._transfers = deque()
        self.created = time.time()
        self.started = None
        self.finished = None
        self.error = None
        self.result = None
        self.cancel = threading.Event()
        self.lock = threading.RLock()

    def check(self):
        if self.cancel.is_set():
            raise Cancelled("任务已取消")

    def update(self, message, progress=None):
        self.check()
        with self.lock:
            self.message = message
            self.download = None
            if progress is not None:
                self.progress = min(100, max(0, progress))

    def set_download_state(self, state, progress):
        self.check()
        with self.lock:
            self.download = dict(state)
            self.progress = min(99.9, max(0, progress))

    def add_bytes(self, size):
        with self.lock:
            self.bytes += size
            now = time.monotonic()
            self._transfers.append((now, size))
            self._trim_transfers(now)

    def _trim_transfers(self, now):
        while self._transfers and self._transfers[0][0] < now - 2:
            self._transfers.popleft()

    def public(self):
        with self.lock:
            result = {key: getattr(self, key) for key in ("id", "title", "status", "message", "progress", "bytes", "created", "started", "finished", "error", "result")}
            now = time.monotonic()
            self._trim_transfers(now)
            # Recent actual bytes across every parallel file; idle, queued and
            # finished tasks report zero rather than a lifetime average.
            result["speed"] = (sum(size for _, size in self._transfers) / max(.5, now-self._transfers[0][0])
                               if self.status == "running" and self._transfers else 0)
            result["download"] = dict(self.download) if self.download else None
            return result


class TaskManager:
    def __init__(self):
        self.tasks = {}
        self.lock = threading.RLock()
        self.worker_lock = threading.Lock()

    def submit(self, title, operation, payload=None):
        task = Task(title, operation, payload)
        with self.lock:
            # Keep recent history without retaining unlimited task objects.
            finished = [key for key, t in self.tasks.items() if t.finished]
            for key in finished[:-50]:
                del self.tasks[key]
            self.tasks[task.id] = task

        def run():
            # Serialize disk mutations so repair, modpack import and launch never race.
            with self.worker_lock:
                try:
                    task.check()
                    task.status = "running"
                    task.started = time.time()
                    task.result = operation(task)
                    task.check()
                    task.status = "completed"
                    task.progress = 100
                    task.message = "已完成"
                except Cancelled:
                    task.status = "cancelled"
                    task.message = "任务已取消，可重新执行以复用已下载文件"
                except Exception as exc:
                    task.status = "failed"
                    task.error = str(exc)[:600]
                    task.message = task.error
                finally:
                    task.finished = time.time()

        threading.Thread(target=run, daemon=True).start()
        return {"task_id": task.id}

    def list(self):
        with self.lock:
            return [t.public() for t in reversed(list(self.tasks.values()))]

    def cancel_task(self, task_id):
        with self.lock:
            if task_id not in self.tasks:
                raise LauncherError("任务不存在")
            self.tasks[task_id].cancel.set()

    def dismiss(self, task_id=None):
        with self.lock:
            if task_id is not None and task_id not in self.tasks:
                raise LauncherError("任务不存在")
            keys = [task_id] if task_id is not None else list(self.tasks)
            for key in keys:
                task = self.tasks[key]
                if task.status in ("queued", "running") or not task.finished:
                    if task_id is not None:
                        raise LauncherError("只能关闭已结束的项目")
                    continue
                del self.tasks[key]


class Store:
    def __init__(self, root, minecraft_root=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.path = self.root / "config.json"
        self.minecraft_root = Path(minecraft_root).resolve() if minecraft_root is not None else self.root / "minecraft"
        self.instances_root = self.minecraft_root / "versions" if minecraft_root is not None else self.root / "instances"
        self.legacy_instances = self.root / "instances"
        if minecraft_root is not None and self.minecraft_root.is_dir():
            self._reuse_legacy_cache()
        self.defaults = {"source": "bmclapi", "memory": 4096, "java_path": "", "java_auto": True,
                         "width": 1280, "height": 720, "jvm_args": "", "game_args": "",
                         "client_id": os.environ.get("BLOCKCRAFT_MS_CLIENT_ID", ""), "curseforge_key": "", "concurrency": 8,
                         "selected_instance": "", "selected_account": "", "accounts": [], "theme": "glass", "animations": True,
                         "login_mode": "", "offline_name": "", "memory_auto": True,
                         "background_path": "", "background_mode":"default", "background_preset":"default", "background_opacity": 55, "glass_opacity": 72, "glass_refraction": 65,
                         "download_parallel": True, "download_segments": min(8,os.cpu_count() or 4),
                         "minecraft_folders": [], "library_root": "", "hidden_external_instances": [],
                         "launcher_width": 900, "launcher_height": 600, "compact_window_default": True,
                         "color_scheme": "starmoon", "ai_base_url": "", "ai_model": "",
                         "ai_crash_analysis": True, "ai_auto_preflight": False}
        saved = read_json(self.path, {})
        self.data = dict(self.defaults, **saved)
        if "background_mode" not in saved and saved.get("background_path"):
            self.data["background_mode"] = "custom"
        if "compact_window_default" not in saved and (saved.get("launcher_width", 1100), saved.get("launcher_height", 720)) == (1100, 720):
            self.data.update(launcher_width=900, launcher_height=600)
        if "memory_auto" not in saved and "memory" in saved:
            self.data["memory_auto"] = False

    def create_minecraft(self):
        """Explicit user action; merely opening the launcher never creates it."""
        (self.minecraft_root / "versions").mkdir(parents=True, exist_ok=True)
        self._reuse_legacy_cache()
        self.save({"library_root": str(self.minecraft_root)})
        return {"path": str(self.minecraft_root)}

    def require_minecraft(self):
        if not self.minecraft_root.is_dir():
            raise LauncherError("请先在版本选择中点击「创建 .minecraft 文件夹」，或添加已有文件夹")

    def _reuse_legacy_cache(self):
        source = self.root / "minecraft"
        if source.resolve() == self.minecraft_root or not source.is_dir():
            return
        # Only immutable downloaded jars/objects share storage. Metadata is copied.
        for directory in ("versions", "libraries", "assets"):
            for path in (source / directory).rglob("*"):
                if not path.is_file() or path.is_symlink() or source.resolve() not in path.resolve().parents:
                    continue
                target = safe_path(self.minecraft_root, path.relative_to(source))
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if path.suffix == ".jar" or "/objects/" in path.as_posix():
                    try:
                        os.link(str(path), str(target))
                        continue
                    except OSError:
                        pass
                shutil.copy2(str(path), str(target))

    def config(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def save(self, changes):
        with self.lock:
            self.data.update(changes)
            atomic_json(self.path, self.data)

    def public_config(self):
        data = self.config()
        data.pop("ai_api_key",None)
        data.pop("ai_clear_key",None)
        data["curseforge_key"] = ""  # Keys are never returned to the frontend.
        data["curseforge_configured"] = bool(self.data["curseforge_key"])
        try:
            ai_keys = read_json(self.root / "ai-credentials.json",{})
            data["ai_key_configured"] = isinstance(ai_keys,dict) and bool(ai_keys.get("key"))
        except (OSError,ValueError):
            data["ai_key_configured"] = False
        return data

    def instance_dir(self, instance_id):
        external = safe_path(self.root / "external-instances", valid_id(instance_id))
        if (external / "instance.json").is_file():
            return external
        path = safe_path(self.instances_root, valid_id(instance_id))
        legacy = safe_path(self.legacy_instances, instance_id)
        return legacy if not path.exists() and (legacy / "instance.json").is_file() else path

    def game_dir(self, instance_id):
        instance = self.instance(instance_id)
        if instance.get("external_minecraft"):
            root = Path(instance["external_minecraft"]).resolve()
            if not root.is_dir():
                raise LauncherError("外部 .minecraft 文件夹不可用，请检查磁盘或重新添加")
            return safe_path(root, instance.get("external_game_directory", ""))
        return self.instance_dir(instance_id) / "game"

    def minecraft_dir(self, instance_id):
        instance = self.instance(instance_id)
        return Path(instance.get("external_minecraft") or self.minecraft_root).resolve()

    def folders(self):
        paths = [str(self.minecraft_root)] + self.config().get("minecraft_folders", [])
        result, seen = [], set()
        for raw in paths:
            path = Path(raw).resolve()
            key = os.path.normcase(str(path))
            if key not in seen:
                seen.add(key)
                result.append({"path": str(path), "default": path == self.minecraft_root,
                               "available": path.is_dir(), "name": "默认 .Minecraft" if path == self.minecraft_root else path.parent.name + " / " + path.name})
        return result

    def selected_minecraft(self):
        selected = self.config().get("library_root")
        key = os.path.normcase(str(Path(selected).resolve())) if selected else ""
        return next((Path(f["path"]) for f in self.folders() if os.path.normcase(f["path"]) == key), self.minecraft_root)

    def instances(self):
        result = []
        paths = list(self.instances_root.glob("*/instance.json"))
        if self.instances_root != self.legacy_instances:
            paths += list(self.legacy_instances.glob("*/instance.json"))
        paths += list((self.root / "external-instances").glob("*/instance.json"))
        seen = set()
        for path in paths:
            try:
                item = read_json(path)
                if item.get("external_visible") is False:
                    continue
                item["id"] = path.parent.name
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                item["path"] = str(path.parent)
                item["minecraft_path"] = item.get("external_minecraft") or str(self.minecraft_root)
                result.append(item)
            except (ValueError, OSError):
                continue
        return sorted(result, key=lambda x: x.get("last_played", x.get("created", 0)), reverse=True)

    def instance(self, instance_id):
        data = read_json(self.instance_dir(instance_id) / "instance.json")
        if data is None:
            raise LauncherError("游戏实例不存在")
        return data

    def update_instance(self, instance_id, changes):
        with self.lock:
            data = self.instance(instance_id)
            data.update(changes)
            atomic_json(self.instance_dir(instance_id) / "instance.json", data)
            return data


def protect(data):
    """Use current Windows user's DPAPI, never a hardcoded encryption key."""
    if os.name != "nt":
        raise LauncherError("此版本仅在 Windows 上持久化微软凭据")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise LauncherError("Windows 凭据加密失败")
    try:
        return base64.b64encode(ctypes.string_at(target.data, target.size)).decode()
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def unprotect(value):
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    data = base64.b64decode(value)
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise LauncherError("无法读取微软凭据，请重新登录")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def extract_zip(archive, destination, prefix="", exclude=(), task=None):
    total = 0
    with zipfile.ZipFile(archive) as zf:
        for entry in zf.infolist():
            if task:
                task.check()
            name = entry.filename.replace("\\", "/")
            if prefix and not name.startswith(prefix):
                continue
            name = name[len(prefix):]
            if not name or any(name.startswith(e) for e in exclude):
                continue
            path = safe_path(destination, name)
            if ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                raise LauncherError("压缩包包含不支持的符号链接")
            total += entry.file_size
            if total > 8 * 1024 ** 3:
                raise LauncherError("压缩包解压大小超过 8GB 限制")
            if entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(entry) as source, path.open("wb") as out:
                    shutil.copyfileobj(source, out)
