"""Read-only screenshot metadata restricted to an instance's screenshots directory."""
from pathlib import Path

from .core import LauncherError, safe_path

EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


def resolve(store, instance_id, filename):
    if not isinstance(filename, str) or Path(filename).name != filename or Path(filename).suffix.lower() not in EXTENSIONS:
        raise LauncherError("无效的截图文件名")
    root = (store.game_dir(instance_id) / "screenshots").resolve()
    path = safe_path(root, filename)
    if path.parent != root or path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        raise LauncherError("截图不存在或超过大小限制")
    return path


def listing(store, instance_id):
    root = (store.game_dir(instance_id) / "screenshots").resolve()
    if not root.is_dir():
        return []
    result = []
    for path in root.iterdir():
        if path.suffix.lower() not in EXTENSIONS or path.is_symlink() or not path.is_file():
            continue
        try:
            info = path.stat()
            if path.resolve().parent == root and info.st_size <= 64 * 1024 * 1024:
                result.append({"name": path.name, "path": str(path), "size": info.st_size, "modified": info.st_mtime})
        except OSError:
            continue
    return sorted(result, key=lambda item: (item["modified"], item["name"]), reverse=True)
