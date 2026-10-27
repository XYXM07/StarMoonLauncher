"""Explicit, cancellable exports of selected game content into portable pack formats."""
import json
import os
from pathlib import Path
import re
import time
import uuid
import zipfile

from .core import LauncherError, safe_path

GROUPS = {
    "mods": ("mods",), "config": ("config", "defaultconfigs"), "resourcepacks": ("resourcepacks",),
    "shaderpacks": ("shaderpacks",), "scripts": ("kubejs", "scripts"), "saves": ("saves",),
    "screenshots": ("screenshots",), "options": ("options.txt", "optionsof.txt", "optionsshaders.txt"),
}
DEFAULT_GROUPS = ("mods", "config", "resourcepacks", "shaderpacks", "scripts")
FORMATS = ("starmoon", "modrinth", "curseforge")


def manifests(instance, options):
    format_name = options.get("format", "starmoon")
    if format_name not in FORMATS:
        raise LauncherError("未知整合包导出格式")
    name = str(options.get("name") or instance["name"]).strip()[:100]
    pack_version = str(options.get("version") or "1.0.0").strip()[:80]
    author = str(options.get("author") or "").strip()[:100]
    summary = str(options.get("summary") or "").strip()[:1000]
    loader = instance["loader"]
    loader_version = instance.get("loader_version", "")
    if format_name != "starmoon" and loader not in ("vanilla", "forge", "fabric", "quilt", "neoforge"):
        raise LauncherError("此加载器请使用星月 ZIP 格式导出")
    if format_name == "curseforge" and loader == "quilt":
        raise LauncherError("Quilt 请使用星月 ZIP 或 Modrinth 格式导出")
    if format_name == "starmoon":
        data = {key: instance.get(key, "") for key in ("name", "game_version", "loader", "loader_version", "window_title")}
        data.update(name=name, pack_version=pack_version, author=author, summary=summary)
        return "blockcraft.json", data, ".zip"
    if format_name == "modrinth":
        dependencies = {"minecraft": instance["game_version"]}
        if loader != "vanilla":
            dependencies[{"fabric": "fabric-loader", "quilt": "quilt-loader"}.get(loader, loader)] = loader_version
        return "modrinth.index.json", {"formatVersion": 1, "game": "minecraft", "versionId": pack_version,
            "name": name, "summary": summary, "files": [], "dependencies": dependencies}, ".mrpack"
    loaders = [] if loader == "vanilla" else [{"id": loader + "-" + loader_version, "primary": True}]
    return "manifest.json", {"minecraft": {"version": instance["game_version"], "modLoaders": loaders},
        "manifestType": "minecraftModpack", "manifestVersion": 1, "name": name, "version": pack_version,
        "author": author, "files": [], "overrides": "overrides"}, ".zip"


def export_pack(content, instance_id, task, options=None):
    options = dict(options or {})
    instance = content.store.instance(instance_id)
    content._check_mod_instance(instance_id, "resourcepack")
    selected = options.get("include", DEFAULT_GROUPS)
    if not isinstance(selected, (list, tuple)) or not selected or any(key not in GROUPS for key in selected):
        raise LauncherError("请至少选择一项有效的导出内容")
    metadata_name, metadata, suffix = manifests(instance, options)
    game = content.store.game_dir(instance_id).resolve()
    candidates = {}
    task.update("扫描导出内容", 2)
    for key in dict.fromkeys(selected):
        for relative in GROUPS[key]:
            start = safe_path(game, relative)
            if not start.exists() or start.is_symlink():
                continue
            if start.is_file():
                candidates[relative] = start
                continue
            for directory, dirs, files in os.walk(str(start), followlinks=False):
                task.check()
                parent = Path(directory)
                dirs[:] = [name for name in dirs if not (parent / name).is_symlink() and game in (parent / name).resolve().parents]
                for name in files:
                    path = parent / name
                    if path.is_symlink() or game not in path.resolve().parents:
                        continue
                    relative_file = path.relative_to(game).as_posix()
                    if key == "mods" and not options.get("include_disabled", True) and name.endswith(".disabled"):
                        continue
                    candidates[relative_file] = path
    if len(candidates) > 100000 or sum(path.stat().st_size for path in candidates.values()) > 8 * 1024 ** 3:
        raise LauncherError("导出内容超过 8GB 或文件数量限制")
    directory = Path(options.get("destination") or content.store.root / "exports").expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", metadata["name"]).strip(" .")[:70] or "modpack"
    filename = stem + "-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6] + suffix
    destination = directory / filename
    temporary = directory / (".export-" + uuid.uuid4().hex + ".tmp")
    compression = options.get("compression", 6)
    if type(compression) is not int or not 0 <= compression <= 9:
        raise LauncherError("压缩级别须为 0–9")
    try:
        with zipfile.ZipFile(temporary, "x", zipfile.ZIP_DEFLATED, compresslevel=compression) as archive:
            archive.writestr(metadata_name, json.dumps(metadata, ensure_ascii=False, indent=2))
            for index, (relative, path) in enumerate(sorted(candidates.items())):
                task.check()
                if path.is_symlink() or game not in path.resolve().parents:
                    raise LauncherError("导出文件已变化，请重试")
                task.update("导出文件 · " + relative, 5 + 92 * index / max(1, len(candidates)))
                with path.open("rb") as source, archive.open("overrides/" + relative, "w") as target:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        task.check()
                        target.write(block)
        task.check()
        os.replace(str(temporary), str(destination))
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"path": str(destination), "filename": filename, "format": options.get("format", "starmoon"), "files": len(candidates)}
