"""StarMoon native desktop entry point."""
import argparse
import ctypes
import json
import os
import sys
from pathlib import Path


def acquire_lock(data_root):
    data_root.mkdir(parents=True, exist_ok=True)
    stream = (data_root / ".launcher.lock").open("a+b")
    if stream.tell() == 0:
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError:
        stream.close()
        return None


def storage_paths(root, requested=""):
    root = Path(root).resolve()
    minecraft = root / ".Minecraft"
    old = root / "BlockCraft" / "data"
    data = Path(requested).resolve() if requested else old if old.is_dir() and not (root / "data").exists() else root / "data"
    return data, minecraft


def main():
    parser = argparse.ArgumentParser(description="星月启动器 · Minecraft Java")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--smoke-test", action="store_true", help="用于自动化验收，启动窗口后自动退出")
    parser.add_argument("--screenshot", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()
    frozen = getattr(sys, "frozen", False)
    root = Path(sys.executable).parent if frozen else Path(__file__).resolve().parent
    if not frozen and (root / ".build-env").is_dir():
        sys.path.insert(0, str(root / ".build-env"))
    publisher = Path(getattr(sys, "_MEIPASS", root)) / "publisher.json"
    if publisher.is_file():
        config = json.loads(publisher.read_text(encoding="utf-8"))
        if not os.environ.get("BLOCKCRAFT_MS_CLIENT_ID"):
            os.environ["BLOCKCRAFT_MS_CLIENT_ID"] = config.get("microsoft_client_id", "")
    data_root, minecraft_root = storage_paths(root, args.data_dir)
    lock = acquire_lock(data_root)
    if lock is None:
        if os.name == "nt":
            window = ctypes.windll.user32.FindWindowW(None, "星月启动器") or ctypes.windll.user32.FindWindowW(None, "BlockCraft · 我的世界 Java 启动器")
            if window:
                ctypes.windll.user32.ShowWindow(window, 9)
                ctypes.windll.user32.SetForegroundWindow(window)
            else:
                ctypes.windll.user32.MessageBoxW(None, "此数据目录已有启动器进程，请稍后重试。", "星月启动器", 0x40)
        return
    try:
        if args.smoke_test:
            # Exercise the packaged native broker DLL without accessing accounts.
            import msal.broker
            import pymsalruntime
        from launcher.desktop import run_desktop
        result = run_desktop(data_root, minecraft_root=minecraft_root, quit_after=3 if args.smoke_test else 0, screenshot=args.screenshot)
        if args.smoke_test:
            from launcher.core import atomic_json
            atomic_json(data_root / "native-smoke-result.json", {"success": result == 0, "native": True, "wam_runtime": True, "version": "0.7.5", "minecraft_path": str(minecraft_root)})
        return result
    except Exception:
        import traceback
        log = data_root / "desktop-error.log"
        log.write_text(traceback.format_exc(), encoding="utf-8")
        if os.name == "nt" and frozen:
            ctypes.windll.user32.MessageBoxW(None, "启动失败，请查看日志：" + str(log), "星月启动器", 0x10)
        else:
            raise
        return 1
    finally:
        lock.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
