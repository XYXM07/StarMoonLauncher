"""Install data packs into an explicit world and import maps without replacing saves."""
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import zipfile

from .core import LauncherError, extract_zip, safe_path, valid_id


def worlds(store, instance_id):
    root = (store.game_dir(instance_id) / "saves").resolve()
    if not root.is_dir():
        return []
    return [{"id": path.name, "name": path.name} for path in sorted(root.iterdir(), key=lambda p: p.name.casefold())
            if not path.name.startswith(".install-") and path.is_dir() and not path.is_symlink()
            and root in path.resolve().parents and (path / "level.dat").is_file()]


def validate_zip(archive):
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > 100000 or sum(item.file_size for item in entries) > 8 * 1024 ** 3:
            raise LauncherError("地图或数据包解压大小超过限制")
        names, seen = [], set()
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            safe_path(Path.cwd() / "zip-validation", name)
            if ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                raise LauncherError("压缩包包含不支持的符号链接")
            if not entry.is_dir():
                if name.casefold() in seen:
                    raise LauncherError("压缩包包含重复文件路径")
                seen.add(name.casefold())
                names.append(name)
        return names


def install(content, project_id, version_id, instance_id, kind, provider, task, world=""):
    content._check_mod_instance(instance_id, "resourcepack")
    instance = content.store.instance(instance_id)
    game = content.store.game_dir(instance_id).resolve()
    if kind == "datapack":
        if world not in {item["id"] for item in worlds(content.store, instance_id)}:
            raise LauncherError("请选择此版本中已有的目标存档")
        target_folder = safe_path(safe_path(game / "saves", world), "datapacks")
    elif kind == "map":
        target_folder = game / "saves"
    else:
        raise LauncherError("不支持的存档内容类型")
    if provider == "modrinth" and kind == "datapack":
        version = content.net.json("https://api.modrinth.com/v2/version/" + valid_id(str(version_id)))
        if str(version.get("project_id")) != str(project_id) or "datapack" not in version.get("loaders", []):
            raise LauncherError("请选择数据包格式的版本")
        if instance["game_version"] not in version.get("game_versions", []):
            raise LauncherError("所选数据包不支持此 Minecraft 版本")
        choices = [item for item in version.get("files", []) if item["filename"].lower().endswith(".zip")]
        if not choices:
            raise LauncherError("此版本没有可安装的数据包 ZIP")
        file = next((item for item in choices if item.get("primary")), choices[0])
        filename = file["filename"]
        hashes = file.get("hashes", {})
        def download(path):
            content.net.download(file["url"], path, sha512=hashes.get("sha512"), sha1=hashes.get("sha1"),
                                 size=file.get("size"), task=task)
    elif provider == "curseforge":
        project = content.curse("/mods/" + valid_id(str(project_id)))["data"]
        if project.get("classId") != {"map": 17, "datapack": 6945}[kind]:
            raise LauncherError("内容类型不匹配")
        file = content.curse("/mods/{}/files/{}".format(valid_id(str(project_id)), valid_id(str(version_id))))["data"]
        if file.get("modId", int(project_id)) != int(project_id) or instance["game_version"] not in file.get("gameVersions", []):
            raise LauncherError("所选内容版本与项目或游戏版本不匹配")
        filename = file["fileName"]
        download = lambda path: content._cf_download(file, path, task)
    else:
        raise LauncherError("此来源不支持所选地图或数据包")
    if Path(filename).name != filename or not filename.lower().endswith(".zip"):
        raise LauncherError("请选择 ZIP 格式的地图或数据包")
    cache = (content.store.root / "cache" / "world-downloads").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    target_folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="download-", dir=str(cache)) as temporary:
        archive = Path(temporary) / "content.zip"
        download(archive)
        task.check()
        try:
            names = validate_zip(archive)
        except zipfile.BadZipFile:
            raise LauncherError("下载文件不是有效的 ZIP 压缩包") from None
        if kind == "datapack":
            task.update("安装数据包", 90)
            prefixes = [name[:-len("pack.mcmeta")] for name in names if name.endswith("pack.mcmeta")]
            prefixes = [prefix for prefix in prefixes if any(name.startswith(prefix + "data/") for name in names)]
            if len(prefixes) != 1:
                raise LauncherError("数据包缺少唯一的 pack.mcmeta 与 data 目录")
            prefix = prefixes[0]
            with zipfile.ZipFile(archive) as source:
                lookup = {entry.filename.replace("\\", "/"): entry for entry in source.infolist()}
                entry = lookup[prefix + "pack.mcmeta"]
                metadata = json.loads(source.read(entry)) if entry.file_size <= 1024 * 1024 else None
                if not isinstance(metadata, dict) or not isinstance(metadata.get("pack"), dict) or not any(
                        key in metadata["pack"] for key in ("pack_format", "min_format", "supported_formats")):
                    raise LauncherError("数据包元数据无效")
            staged = target_folder / (".install-" + uuid.uuid4().hex + ".tmp")
            try:
                with zipfile.ZipFile(archive) as source, zipfile.ZipFile(staged, "x", zipfile.ZIP_DEFLATED) as output:
                    for entry in source.infolist():
                        task.check()
                        normalized = entry.filename.replace("\\", "/")
                        if entry.is_dir() or not normalized.startswith(prefix):
                            continue
                        relative = normalized[len(prefix):]
                        with source.open(entry) as reader, output.open(relative, "w") as writer:
                            for block in iter(lambda: reader.read(1024 * 1024), b""):
                                task.check()
                                writer.write(block)
                destination = safe_path(target_folder, filename)
                if destination.exists():
                    destination = destination.with_name(destination.stem + "-" + uuid.uuid4().hex[:6] + ".zip")
                task.check()
                os.rename(str(staged), str(destination))
            finally:
                if staged.exists():
                    staged.unlink()
        else:
            task.update("解压地图", 90)
            roots = [name[:-len("level.dat")] for name in names if name == "level.dat" or name.endswith("/level.dat")]
            if len(roots) != 1:
                raise LauncherError("地图压缩包须包含一个存档及其 level.dat")
            prefix = roots[0]
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(filename).stem).strip(" .")[:80] or "Map"
            destination = safe_path(target_folder, name)
            if destination.exists():
                destination = safe_path(target_folder, name + "-" + uuid.uuid4().hex[:6])
            with tempfile.TemporaryDirectory(prefix=".install-", dir=str(target_folder)) as stage:
                extracted = Path(stage) / "world"
                extracted.mkdir()
                extract_zip(archive, extracted, prefix=prefix, task=task)
                task.check()
                if not (extracted / "level.dat").is_file():
                    raise LauncherError("地图存档不完整")
                os.rename(str(extracted), str(destination))
    return {"instance_id": instance_id, "path": str(destination), "kind": kind, "world": world}
