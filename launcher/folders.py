"""References to existing .minecraft trees; registration never copies game files."""
import copy
import hashlib
import os
import re
import time
from pathlib import Path

from .core import LauncherError, atomic_json, read_json, safe_path, version_id


def folder_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def game_directory(root, directory, loader):
    # Recognize this launcher's own layout, version isolation, and shared roots.
    markers = ("mods", "config", "saves", "resourcepacks", "shaderpacks", "options.txt")
    if (directory / "game").is_dir():
        return directory / "game"
    pcl = directory / "PCL/Setup.ini"
    if pcl.is_file():
        text = pcl.read_text(encoding="utf-8-sig", errors="replace")
        values = dict(re.findall(r"(?m)^\s*([A-Za-z0-9]+)\s*[:=]\s*([^\r\n]*)", text))
        modern = values.get("VersionArgumentIndieV2", "").strip().lower()
        if modern in ("true", "1", "false", "0"):
            return directory if modern in ("true", "1") else root
        legacy = values.get("VersionArgumentIndie", "").strip()
        if legacy in ("1", "2"):
            return directory if legacy == "1" else root
    try:
        hmcl = read_json(directory / "hmclversion.cfg", {})
    except (OSError, ValueError):
        hmcl = {}
    if hmcl.get("usesGlobal") is False:
        if hmcl.get("gameDirType") == "ROOT_FOLDER":
            return root
        if hmcl.get("gameDirType") == "VERSION_FOLDER":
            return directory
    if any((directory / marker).exists() for marker in markers):
        return directory
    if any((root / marker).exists() for marker in markers):
        return root
    return directory if loader != "vanilla" else root


def add_folder(manager, path, select=True):
    root = Path(path).expanduser().resolve()
    if not root.is_dir() or not (root / "versions").is_dir():
        raise LauncherError("请选择包含 versions 文件夹的 .minecraft 目录")
    store = manager.store
    root = next((Path(item["path"]) for item in store.folders() if folder_key(item["path"]) == folder_key(root)), root)
    bound = copy.copy(manager)
    bound.mc = root
    found, errors = [], []
    hidden = set(store.config().get("hidden_external_instances", []))
    managed = {folder_key(store.instance_dir(i["id"])): i["id"] for i in store.instances() if not i.get("external_minecraft")}
    for directory in sorted((root / "versions").iterdir()):
        if not directory.is_dir() or directory.is_symlink():
            continue
        profile_path = directory / (directory.name + ".json")
        if not profile_path.is_file():
            continue
        try:
            version = version_id(directory.name)
            if folder_key(directory) in managed:
                found.append(managed[folder_key(directory)])
                continue
            profile = bound.profile(version)
            if not profile.get("mainClass"):
                continue
            identifier = (version + " " + profile.get("mainClass", "") + " " + " ".join(lib.get("name", "") for lib in profile.get("libraries", []))).lower()
            loader = next((name for name in ("neoforge", "fabric", "quilt", "forge") if name in identifier), "vanilla")
            chain, current = [], version
            while current:
                if current in chain:
                    raise LauncherError("版本继承存在循环")
                chain.append(current)
                current = read_json(bound.profile_path(current), {}).get("inheritsFrom")
            base = read_json(bound.profile_path(chain[-1]))
            game_version = base.get("clientVersion") or base.get("id", chain[-1])
            match = re.search(r"(?:^|[^\d])(1\.\d+(?:\.\d+)?)(?:$|[^\d])", game_version)
            if match:
                game_version = match.group(1)
            client = profile.get("jar")
            if not client:
                client = next((v for v in chain if (root / "versions" / v / (v + ".jar")).is_file()), chain[-1])
            version_id(client)
            game = game_directory(root, directory, loader)
            relative = game.relative_to(root).as_posix()
            external_id = "external-" + hashlib.sha256((folder_key(root) + "\0" + version).encode("utf-8")).hexdigest()[:20]
            if external_id in hidden:
                continue
            destination = store.root / "external-instances" / external_id / "instance.json"
            previous = read_json(destination, {})
            item = dict(previous)
            item.update({"id": external_id, "name": previous.get("name") or version,
                         "version": version, "game_version": game_version, "loader": loader,
                         "loader_version": previous.get("loader_version", ""), "loader_installed": True,
                         "ready": (root / "versions" / client / (client + ".jar")).is_file(),
                         "external_minecraft": str(root), "external_visible": True,
                         "external_game_directory": "" if relative == "." else relative,
                         "client_jar_version": client, "created": previous.get("created", time.time()),
                         "last_played": previous.get("last_played", 0), "play_seconds": previous.get("play_seconds", 0),
                         "memory": previous.get("memory", 0), "java_path": previous.get("java_path", ""),
                         "jvm_args": previous.get("jvm_args", ""), "game_args": previous.get("game_args", "")})
            atomic_json(destination, item)
            found.append(external_id)
        except (ValueError, OSError, TypeError, KeyError, LauncherError) as exc:
            errors.append({"version": directory.name, "error": str(exc)[:200]})
    for record in (store.root / "external-instances").glob("*/instance.json"):
        item = read_json(record, {})
        if item.get("external_minecraft") and folder_key(item["external_minecraft"]) == folder_key(root) and item.get("id") not in found:
            if item.get("external_visible", True):
                item["external_visible"] = False
                atomic_json(record, item)
    folders = store.config().get("minecraft_folders", [])
    if root != store.minecraft_root and all(folder_key(p) != folder_key(root) for p in folders):
        folders.append(str(root))
    changes = {"minecraft_folders": folders}
    if select:
        changes["library_root"] = str(root)
    store.save(changes)
    return {"path": str(root), "instances": found, "skipped": errors}
