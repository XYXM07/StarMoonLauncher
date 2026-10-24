"""Portable EXE replacement. The same executable runs as its detached helper."""
import ctypes
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .core import LauncherError, atomic_json


def sha256_file(path, task=None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if task:
                task.check()
            digest.update(chunk)
    return digest.hexdigest()


def validate_executable(path, digest, size=None, task=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or (size is not None and path.stat().st_size != size):
        raise LauncherError("更新文件大小不匹配或文件不可用")
    if not re.fullmatch(r"[0-9a-f]{64}", str(digest)) or sha256_file(path, task) != digest:
        raise LauncherError("更新文件 SHA-256 校验失败，请重新下载")
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise LauncherError("更新包不是有效的 Windows 64 位程序")
        offset = struct.unpack_from("<I", header, 60)[0]
        if not 64 <= offset <= min(path.stat().st_size - 6, 16 * 1024 * 1024):
            raise LauncherError("更新包不是有效的 Windows 64 位程序")
        stream.seek(offset)
        if stream.read(6) != b"PE\0\0d\x86":
            raise LauncherError("更新包不是有效的 Windows 64 位程序")


def launch_executable(executable, arguments=()):
    options = {"cwd": str(Path(executable).parent), "stdin": subprocess.DEVNULL,
               "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen([str(executable)] + list(arguments), **options)


def wait_for_parent(pid, timeout=120):
    if os.name != "nt" or type(pid) is not int or pid <= 0 or pid == os.getpid():
        raise LauncherError("更新进程参数无效")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    if not handle:
        if ctypes.get_last_error() == 87:  # Parent already exited.
            return
        raise LauncherError("无法等待旧启动器退出，请完全退出后重试")
    try:
        if kernel.WaitForSingleObject(handle, round(timeout * 1000)) != 0:
            raise LauncherError("等待旧启动器退出超时，原程序未替换")
    finally:
        kernel.CloseHandle(handle)


def move_when_unlocked(source, target, timeout=45):
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.replace(str(source), str(target))
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.2)


def prepare_install(cache, target, ready, data_root, parent_pid=None):
    cache, target, data_root = Path(cache).resolve(), Path(target).resolve(), Path(data_root).resolve()
    source = Path(ready["path"]).resolve()
    if cache != data_root / "updates" or cache not in source.parents or cache in target.parents or target.suffix.lower() != ".exe":
        raise LauncherError("更新安装路径无效")
    validate_executable(source, ready["sha256"], ready["size"])
    if not target.is_file() or target.is_symlink():
        raise LauncherError("原启动器程序不可用")
    token = uuid.uuid4().hex
    work = cache / ("install-" + token)
    work.mkdir(parents=True, exist_ok=False)
    # Probe the actual target directory before closing a working launcher.
    probe = target.parent / (".starmoon-write-" + token)
    try:
        with probe.open("xb"):
            pass
    except OSError as exc:
        raise LauncherError("启动器目录不可写，请移到可写目录后更新") from exc
    finally:
        if probe.exists():
            probe.unlink()
    helper = work / "update-helper.exe"
    shutil.copy2(str(target), str(helper))
    plan = {"schema": 1, "token": token, "cache": str(cache), "data_root": str(data_root),
            "source": str(source), "target": str(target), "sha256": ready["sha256"], "size": ready["size"],
            "old_sha256": sha256_file(target), "version": ready["version"],
            "parent_pid": parent_pid if parent_pid is not None else os.getpid()}
    path = work / "install.json"
    atomic_json(path, plan)
    return path, helper


def load_plan(path, helper=None):
    path = Path(path).resolve()
    if path.name != "install.json" or path.stat().st_size > 16384:
        raise LauncherError("更新计划无效")
    plan = json.loads(path.read_text(encoding="utf-8"))
    token = plan.get("token", "")
    cache, data = Path(plan["cache"]).resolve(), Path(plan["data_root"]).resolve()
    target, source = Path(plan["target"]).resolve(), Path(plan["source"]).resolve()
    if (plan.get("schema") != 1 or not re.fullmatch(r"[a-f0-9]{32}", token) or cache != data / "updates" or
            path.parent != cache / ("install-" + token) or target.suffix.lower() != ".exe" or
            cache in target.parents or cache not in source.parents or source == target or
            type(plan.get("parent_pid")) is not int or not 0 < plan["parent_pid"] <= 0xffffffff or
            type(plan.get("size")) is not int or not 1 <= plan["size"] <= 512 * 1024 * 1024 or
            not re.fullmatch(r"[0-9a-f]{64}", str(plan.get("old_sha256", "")))):
        raise LauncherError("更新安装路径无效")
    if helper is not None and Path(helper).resolve() != path.parent / "update-helper.exe":
        raise LauncherError("更新辅助程序路径无效")
    return plan


def apply_install(path, helper=None, wait=wait_for_parent, launch=launch_executable):
    plan = load_plan(path, helper)
    source, target = Path(plan["source"]), Path(plan["target"])
    token = plan["token"]
    temporary = target.parent / ("." + target.name + "." + token + ".new")
    backup = target.parent / ("." + target.name + "." + token + ".backup")
    receipt = Path(plan["cache"]) / "last-install.json"
    moved, parent_exited = False, False
    try:
        validate_executable(source, plan["sha256"], plan["size"])
        wait(plan["parent_pid"])
        parent_exited = True
        if sha256_file(target) != plan["old_sha256"] or backup.exists():
            raise LauncherError("原程序已发生变化，已停止自动替换")
        with source.open("rb") as incoming, temporary.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        validate_executable(temporary, plan["sha256"], plan["size"])
        move_when_unlocked(target, backup)
        moved = True
        os.replace(str(temporary), str(target))
        result = {"status": "updated", "version": plan["version"], "backup": str(backup), "time": time.time()}
        atomic_json(receipt, result)
        process = launch(target, ["--data-dir", plan["data_root"]])
        if type(getattr(process, "pid", None)) is int:
            try:
                atomic_json(receipt, dict(result, launched_pid=process.pid))
            except OSError:
                pass
        return True
    except Exception:
        # Roll back file replacement and immediate process creation failures.
        restored = not moved
        if moved:
            try:
                move_when_unlocked(backup, target)
                restored = True
            except OSError:
                restored = False
        message = "更新未完成，已保留原程序，请重新打开启动器" if restored else "更新未完成，请使用保留的旧版备份恢复程序"
        try:
            atomic_json(receipt, {"status": "failed", "message": message, "backup": str(backup),
                                  "restored": restored, "time": time.time()})
        except OSError:
            pass
        if restored and parent_exited:
            try:
                if target.is_file() and sha256_file(target) == plan["old_sha256"]:
                    launch(target, ["--data-dir", plan["data_root"]])
            except OSError:
                pass
        return False
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
