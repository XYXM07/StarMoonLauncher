"""GitHub stable-release checks and verified portable Windows update downloads."""
import copy
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import __version__
from .core import Cancelled, LauncherError, SSL_CONTEXT, USER_AGENT, atomic_json, read_json, safe_path
from .progress import DownloadProgress
from .update_install import prepare_install, validate_executable

REPOSITORY = "XYXM07/StarMoonLauncher"
RELEASES_URL = "https://github.com/" + REPOSITORY + "/releases"
LATEST_URL = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
CHECK_INTERVAL = 12 * 60 * 60


class UpdateProgress:
    """Bound wire bytes even if a CDN omits or lies about Content-Length."""
    def __init__(self, progress, limit):
        self.progress, self.limit, self.received = progress, limit, 0
        self.lock = threading.Lock()

    def check(self):
        self.progress.check()

    def set_download_size(self, size):
        if size is not None and size != self.limit:
            raise LauncherError("更新文件大小不匹配或文件不可用")
        self.progress.set_download_size(self.limit)

    def reset_download(self):
        with self.lock:
            self.received = 0
            self.progress.reset_download()

    def add_bytes(self, size):
        with self.lock:
            self.received += size
            if self.received > self.limit:
                raise LauncherError("更新文件大小不匹配或文件不可用")
            self.progress.add_bytes(size)

    def complete_download(self, size):
        self.progress.complete_download(size)


def version_key(value):
    match = re.fullmatch(r"[vV]?(\d{1,6})\.(\d{1,6})\.(\d{1,6})(?:\.(\d{1,6}))?", str(value))
    if not match:
        raise LauncherError("无法识别正式版本号")
    return tuple(int(part or 0) for part in match.groups())


def asset_url(tag, name):
    return RELEASES_URL + "/download/" + urllib.parse.quote(tag, safe="") + "/" + urllib.parse.quote(name, safe="")


def read_url(url, limit=2 * 1024 * 1024, api=False):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json" if api else "text/plain"}
    if api:
        headers["X-GitHub-Api-Version"] = "2026-03-10"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=12, context=SSL_CONTEXT) as response:
        if urllib.parse.urlsplit(response.geturl()).scheme != "https":
            raise LauncherError("更新下载必须使用 HTTPS")
        content = response.read(limit + 1)
        if len(content) > limit:
            raise LauncherError("更新信息超过大小限制")
        return content


def parse_release(release, current_version):
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        raise LauncherError("更新信息不是可用的正式版本")
    tag = str(release.get("tag_name", ""))
    latest = version_key(tag)
    version = tag.lstrip("vV")
    link = RELEASES_URL + "/tag/" + urllib.parse.quote(tag, safe="")
    if release.get("html_url") != link:
        raise LauncherError("更新来源不属于此启动器仓库")
    info = {"available": latest > version_key(current_version), "version": version, "tag": tag,
            "release_url": link, "notes": str(release.get("body") or "")[:64000]}
    if not info["available"]:
        return info
    expected_name = "StarMoonLauncher-" + version + "-windows-x64.exe"
    candidates = [item for item in release.get("assets", []) if isinstance(item, dict) and item.get("name") == expected_name and item.get("state") == "uploaded"]
    if len(candidates) != 1:
        raise LauncherError("新版尚未提供可自动更新的 Windows 64 位程序，请查看发布页")
    asset = candidates[0]
    size = asset.get("size")
    if type(size) is not int or not 1 <= size <= 512 * 1024 * 1024 or asset.get("browser_download_url") != asset_url(tag, expected_name):
        raise LauncherError("更新文件信息或下载地址无效")
    info["asset"] = {"name": expected_name, "url": asset_url(tag, expected_name), "size": size}
    digest = str(asset.get("digest") or "")
    if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        info["sha256"] = digest[7:].lower()
    else:
        checksums = next((item for item in release.get("assets", []) if isinstance(item, dict) and item.get("name") == "SHA256SUMS.txt" and item.get("state") == "uploaded"), None)
        if not checksums or checksums.get("browser_download_url") != asset_url(tag, "SHA256SUMS.txt"):
            raise LauncherError("新版未提供 SHA-256 校验信息，无法自动安装")
        info["checksum_url"] = asset_url(tag, "SHA256SUMS.txt")
    return info


class UpdateManager:
    def __init__(self, store, network, current_version=__version__):
        self.store, self.network, self.current_version = store, network, current_version
        self.cache = store.root / "updates"
        self.lock = threading.RLock()
        self.latest = None
        self.ready = None

    def startup_due(self, now=None):
        if not self.store.config().get("auto_check_updates", True):
            return False
        now = time.time() if now is None else now
        try:
            last = float(read_json(self.cache / "check.json", {}).get("time", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            return True
        return last <= 0 or now - last >= CHECK_INTERVAL or last > now + 300

    def check(self):
        try:
            release = json.loads(read_url(LATEST_URL, api=True).decode("utf-8"))
            info = parse_release(release, self.current_version)
            if info.get("checksum_url"):
                text = read_url(info["checksum_url"], limit=65536).decode("utf-8-sig")
                matches = [match.group(1).lower() for line in text.splitlines()
                           for match in [re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())]
                           if match and match.group(2) == info["asset"]["name"]]
                if len(matches) != 1:
                    raise LauncherError("校验文件中没有唯一匹配的更新程序")
                info["sha256"] = matches[0]
            with self.lock:
                self.latest = info
            atomic_json(self.cache / "check.json", {"time": time.time(), "version": info["version"]})
            return copy.deepcopy(info)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code == 404 and exc.url == LATEST_URL:
                info = {"available": False, "version": self.current_version, "release_url": RELEASES_URL, "notes": ""}
                with self.lock:
                    self.latest = info
                atomic_json(self.cache / "check.json", {"time": time.time()})
                return info
            if code in (403, 429):
                raise LauncherError("更新检查受到 GitHub 限制，请稍后重试") from None
            raise LauncherError("更新检查失败（HTTP {}）".format(code)) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            raise LauncherError("无法连接更新服务器，请检查网络或稍后重试") from None
        except (ValueError, KeyError, TypeError):
            raise LauncherError("无法解析更新信息，请稍后重试") from None

    def download(self, task):
        with self.lock:
            info = copy.deepcopy(self.latest)
        if not info or not info.get("available") or not info.get("sha256"):
            raise LauncherError("请先检查并选择可用的启动器更新")
        asset = info["asset"]
        destination = safe_path(self.cache, "packages/" + info["version"] + "/" + asset["name"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".part-" + uuid.uuid4().hex)
        try:
            cached = False
            if destination.is_file():
                try:
                    validate_executable(destination, info["sha256"], asset["size"], task)
                    cached = True
                except LauncherError:
                    task.check()
            if not cached:
                progress = UpdateProgress(DownloadProgress(task, [{"size": asset["size"]}], "下载启动器更新", 0, 95).file(0), asset["size"])
                received = self.network.engine.fetch(asset["url"], temporary, progress, expected_size=asset["size"], has_hash=True)
                task.check()
                if received != asset["size"]:
                    raise LauncherError("更新文件大小不匹配或文件不可用")
                progress.complete_download(received)
                task.update("校验启动器更新", 96)
                validate_executable(temporary, info["sha256"], asset["size"], task)
                task.check()
                os.replace(str(temporary), str(destination))
            ready = {"path": str(destination), "sha256": info["sha256"], "size": asset["size"], "version": info["version"]}
            with self.lock:
                self.ready = ready
            return dict(ready, launcher_update_ready=True)
        finally:
            if temporary.exists():
                temporary.unlink()

    def prepare(self, executable=None, frozen=None):
        frozen = getattr(sys, "frozen", False) if frozen is None else frozen
        if not frozen or os.name != "nt":
            raise LauncherError("源码运行可检查更新，自动安装请使用单文件 EXE")
        with self.lock:
            ready = copy.deepcopy(self.ready)
        if not ready:
            raise LauncherError("请先下载启动器更新")
        return prepare_install(self.cache, executable or sys.executable, ready, self.store.root)

    def take_outcome(self):
        path = self.cache / "last-install.json"
        try:
            result = read_json(path, {})
            if isinstance(result, dict) and result and not result.get("seen"):
                atomic_json(path, dict(result, seen=True))
                return result
        except (OSError, ValueError, TypeError):
            pass
        return None
