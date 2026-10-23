"""Bounded multi-connection downloads with strict HTTP Range validation."""
import concurrent.futures
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

from .core import Cancelled, LauncherError, SSL_CONTEXT, USER_AGENT

MIN_SEGMENT = 1024 * 1024
MULTIPART_THRESHOLD = 4 * MIN_SEGMENT


class RangeUnsupported(Exception):
    pass


class DownloadEngine:
    def __init__(self, config):
        self.config = config
        self.condition = threading.Condition()
        self.active = 0

    @staticmethod
    def check(task, stop=None):
        if task:
            task.check()
        if stop and stop.is_set():
            raise LauncherError("下载分段已停止")

    @contextmanager
    def open(self, request, task, stop=None):
        with self.condition:
            while self.active >= max(1, min(16, int(self.config().get("concurrency", 8)))):
                self.check(task, stop)
                self.condition.wait(.08)
            self.check(task, stop)
            self.active += 1
        try:
            with urllib.request.urlopen(request, timeout=15, context=SSL_CONTEXT) as response:
                actual = response.geturl() if hasattr(response, "geturl") else request.full_url
                if urllib.parse.urlparse(actual).scheme != "https":
                    raise LauncherError("下载重定向必须使用 HTTPS")
                yield response
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()

    def request(self, endpoint, extra=None):
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        headers.update(extra or {})
        return urllib.request.Request(endpoint, headers=headers)

    def fetch(self, endpoint, target, task, expected_size=None, has_hash=False):
        config = self.config()
        segments = max(1, min(16, int(config.get("download_segments", 8)))) if config.get("download_parallel", True) else 1
        multipart = None
        with self.open(self.request(endpoint), task) as response:
            length = int(response.headers.get("Content-Length", 0))
            if task and hasattr(task, "set_download_size"):
                task.set_download_size(length if length > 0 else None)
            validator = response.headers.get("ETag", "")
            if validator.startswith("W/"):
                validator = ""
            validator = validator or response.headers.get("Last-Modified", "")
            if (segments > 1 and length >= MULTIPART_THRESHOLD and
                    response.headers.get("Accept-Ranges", "").lower() == "bytes" and
                    response.headers.get("Content-Encoding", "identity") == "identity" and (validator or has_hash)):
                if expected_size is not None and length != expected_size:
                    raise LauncherError("文件大小与元数据不一致")
                multipart = (length, validator)
            else:
                return self.stream(response, target, task, length)
        if multipart:
            length, validator = multipart
            try:
                return self.ranges(endpoint, target, task, length, validator, min(segments, length//MIN_SEGMENT))
            except RangeUnsupported:
                # Some CDNs advertise Range but return a full 200 response.
                # Restart as one stream; never append a full file to a segment.
                with self.open(self.request(endpoint), task) as response:
                    if task and hasattr(task, "reset_download"):
                        task.reset_download()
                    return self.stream(response, target, task, int(response.headers.get("Content-Length", 0)))

    def stream(self, response, target, task, expected):
        downloaded = 0
        with target.open("wb") as output:
            while True:
                self.check(task)
                chunk = response.read(256*1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if task:
                    task.add_bytes(len(chunk))
        if expected and downloaded != expected:
            raise LauncherError("下载不完整")
        return downloaded

    def ranges(self, endpoint, target, task, total, validator, count):
        with target.open("wb") as output:
            output.truncate(total)
        stop = threading.Event()

        def transfer(start, end):
            self.check(task, stop)
            headers = {"Range": "bytes={}-{}".format(start, end)}
            if validator:
                headers["If-Range"] = validator
            try:
                with self.open(self.request(endpoint, headers), task, stop) as response:
                    status = getattr(response, "status", None) or response.getcode()
                    content_range = response.headers.get("Content-Range", "")
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                    if status != 206 or not match or tuple(map(int, match.groups())) != (start, end, total):
                        raise RangeUnsupported()
                    if response.headers.get("Content-Encoding", "identity") != "identity":
                        raise RangeUnsupported()
                    current = response.headers.get("ETag") if validator.startswith('"') else response.headers.get("Last-Modified")
                    if validator and current and current != validator:
                        raise RangeUnsupported()
                    received = 0
                    with target.open("r+b") as output:
                        output.seek(start)
                        while received < end-start+1:
                            self.check(task, stop)
                            chunk = response.read(min(256*1024, end-start+1-received))
                            if not chunk:
                                raise LauncherError("下载分段不完整")
                            output.write(chunk)
                            received += len(chunk)
                            if task:
                                task.add_bytes(len(chunk))
                        if response.read(1):
                            raise RangeUnsupported()
            except urllib.error.HTTPError as exc:
                if exc.code == 416:
                    raise RangeUnsupported() from exc
                raise

        with concurrent.futures.ThreadPoolExecutor(max_workers=count, thread_name_prefix="StarMoon-range") as pool:
            futures = [pool.submit(transfer, index*total//count, (index+1)*total//count-1) for index in range(count)]
            try:
                for future in concurrent.futures.as_completed(futures):
                    future.result()
            except Exception:
                stop.set()
                for future in futures:
                    future.cancel()
                raise
        self.check(task)
        return total
