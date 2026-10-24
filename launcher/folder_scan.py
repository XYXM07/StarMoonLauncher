"""Cancellable, read-only discovery of .minecraft folders on local disks."""
import ctypes
import os
import stat
import threading
from pathlib import Path


def local_scan_roots():
    if os.name == "nt":
        kernel = ctypes.windll.kernel32
        kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        kernel.GetDriveTypeW.restype = ctypes.c_uint
        mask = kernel.GetLogicalDrives()
        return [Path(chr(65 + index) + ":\\") for index in range(26)
                if mask & (1 << index) and kernel.GetDriveTypeW(chr(65 + index) + ":\\") in (2, 3, 6)]
    return [Path.home()]


def is_directory_link(metadata):
    # Junctions and other Windows reparse points can also leave a scan's scope.
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


class MinecraftFolderScanner:
    def __init__(self, roots=None):
        candidates = [Path(os.path.abspath(os.path.expanduser(str(root)))) for root in
                      (local_scan_roots() if roots is None else roots)]
        self.roots, root_keys = [], set()
        for root in sorted(candidates, key=lambda path: len(path.parts)):
            key = os.path.normcase(str(root))
            if key in root_keys or any(os.path.normcase(str(parent)) in root_keys for parent in root.parents):
                continue
            self.roots.append(root)
            root_keys.add(key)
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.folders = []
        self.progress = {"visited": 0, "skipped": 0, "found": 0, "current": "", "complete": False}

    def cancel(self):
        self.cancelled.set()

    def snapshot(self):
        with self.lock:
            return dict(self.progress, cancelled=self.cancelled.is_set())

    def run(self):
        pending = list(reversed(self.roots))
        while pending and not self.cancelled.is_set():
            directory = pending.pop()
            with self.lock:
                self.progress["current"] = str(directory)
            try:
                metadata = directory.lstat()
                if not stat.S_ISDIR(metadata.st_mode) or is_directory_link(metadata):
                    continue
                with self.lock:
                    self.progress["visited"] += 1
                if directory.name.casefold() == ".minecraft":
                    versions = directory / "versions"
                    available, count, issue = False, 0, "缺少 versions 文件夹"
                    try:
                        available = versions.is_dir() and not is_directory_link(versions.lstat())
                        if available:
                            with os.scandir(str(versions)) as entries:
                                for entry in entries:
                                    if self.cancelled.is_set():
                                        break
                                    if entry.is_dir(follow_symlinks=False) and not is_directory_link(entry.stat(follow_symlinks=False)):
                                        if (Path(entry.path) / (entry.name + ".json")).is_file():
                                            count += 1
                    except OSError:
                        available, issue = False, "无法读取 versions 文件夹"
                        with self.lock:
                            self.progress["skipped"] += 1
                    self.folders.append({"path": str(directory), "versions": count, "available": available,
                                         "issue": "" if available else issue})
                    with self.lock:
                        self.progress["found"] = len(self.folders)
                with os.scandir(str(directory)) as entries:
                    for entry in entries:
                        if self.cancelled.is_set():
                            break
                        try:
                            if entry.is_dir(follow_symlinks=False) and not is_directory_link(entry.stat(follow_symlinks=False)):
                                pending.append(Path(entry.path))
                        except OSError:
                            with self.lock:
                                self.progress["skipped"] += 1
            except OSError:
                with self.lock:
                    self.progress["skipped"] += 1
        with self.lock:
            self.progress["complete"] = True
        return dict(self.snapshot(), folders=sorted(self.folders, key=lambda item: item["path"].casefold()))
