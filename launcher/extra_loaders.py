"""Catalog-backed standalone OptiFine, LiteLoader and Cleanroom installations."""
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.parse
import uuid
import zipfile

from .core import LauncherError, atomic_json, safe_path, valid_id
from .game import maven_path

NAMES = {"optifine": "OptiFine", "liteloader": "LiteLoader", "cleanroom": "Cleanroom"}
MIRROR = "https://bmclapi2.bangbang93.com"


def catalog(game, key, url):
    cache = getattr(game, "_extra_loader_cache", None)
    if cache is None:
        game._extra_loader_cache = cache = {}
    cached = cache.get(key)
    if cached and time.monotonic() - cached[0] < 300:
        return copy.deepcopy(cached[1])
    data = game.net.json(url)
    cache[key] = time.monotonic(), copy.deepcopy(data)
    return data


def versions(game, minecraft, loader):
    valid_id(minecraft)
    if loader == "cleanroom":
        if minecraft != "1.12.2":
            return []
        releases = catalog(game, "cleanroom", "https://api.github.com/repos/CleanroomMC/Cleanroom/releases?per_page=50")
        result = []
        for release in releases:
            if release.get("draft"):
                continue
            for asset in release.get("assets", []):
                if asset["name"].endswith("-installer.jar"):
                    version = valid_id(release["tag_name"].lstrip("v"))
                    url = asset["browser_download_url"]
                    if not url.startswith("https://github.com/CleanroomMC/Cleanroom/releases/download/"):
                        raise LauncherError("Cleanroom 下载来源无效")
                    digest = asset.get("digest", "") or ""
                    result.append({"version": version, "stable": not release.get("prerelease", False), "url": url,
                        "size": asset.get("size"), "sha256": digest[7:] if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) else None})
        return result
    if loader == "optifine":
        data = catalog(game, "optifine:" + minecraft, MIRROR + "/optifine/" + minecraft)
        result = []
        for item in data:
            if item.get("mcversion") != minecraft:
                continue
            kind, patch = valid_id(item["type"]), valid_id(item["patch"])
            result.append({"version": kind + "_" + patch, "stable": "pre" not in patch.lower(),
                           "url": MIRROR + "/optifine/{}/{}/{}".format(minecraft, kind, patch)})
        return list(reversed(result))
    if not re.fullmatch(r"1\.(?:[5-9]|1[0-2])(?:\.\d+)?", minecraft):
        return []
    data = catalog(game, "liteloader", MIRROR + "/maven/com/mumfrey/liteloader/versions.json")
    section = data.get("versions", {}).get(minecraft)
    if not section:
        return []
    result, seen = [], set()
    for stream in ("artefacts", "snapshots"):
        group = section.get(stream, {})
        for coordinate, entries in group.items():
            if not isinstance(entries, dict) or not coordinate.startswith("com.mumfrey:liteloader"):
                continue
            ordered = ([entries["latest"]] if isinstance(entries.get("latest"), dict) else []) + list(entries.values())
            for item in ordered:
                if not isinstance(item, dict) or not item.get("version"):
                    continue
                artifact = valid_id(item["version"])
                build = valid_id(item.get("build") or artifact)
                if build in seen:
                    continue
                seen.add(build)
                record = dict(item, version=build, artifact_version=artifact, stable=stream == "artefacts")
                record["libraries"] = list(group.get("libraries", [])) + list(item.get("libraries", []))
                result.append(record)
    return result


def artifact(name, target, url=""):
    payload = target.read_bytes()
    return {"name": name, "downloads": {"artifact": {"path": maven_path(name), "url": url,
            "sha1": hashlib.sha1(payload).hexdigest(), "size": len(payload)}}}


def write_library(target, payload):
    # A second instance may use this same immutable library while a game runs.
    if target.is_file() and target.stat().st_size == len(payload) and target.read_bytes() == payload:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp-" + uuid.uuid4().hex)
    try:
        temporary.write_bytes(payload)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()


def run_patcher(game, instance, command, task):
    task.check()
    log_path = game.store.instance_dir(instance["id"]) / "installer.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=str(game.mc), stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        started = time.monotonic()
        while process.poll() is None:
            if task.cancel.wait(.2) or time.monotonic() - started > 900:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                task.check()
                raise LauncherError("加载器安装超时，请查看 installer.log")
        if process.returncode:
            raise LauncherError("加载器安装失败，请查看 installer.log")


def install(game, instance, task):
    minecraft, loader = instance["game_version"], instance["loader"]
    choices = versions(game, minecraft, loader)
    selected = instance.get("loader_version")
    record = next((item for item in choices if item["version"] == selected), None) if selected else next(
        (item for item in choices if item.get("stable")), choices[0] if choices else None)
    if record is None:
        raise LauncherError("此游戏版本没有所选加载器版本")
    selected = valid_id(record["version"])
    game.store.update_instance(instance["id"], {"loader_version": selected})
    task.update("安装 " + NAMES[loader] + " · " + selected, 8)
    profile_id = valid_id(minecraft + "-" + loader + "-" + selected)
    if loader == "liteloader":
        coordinate = "com.mumfrey:liteloader:" + record["artifact_version"]
        path = maven_path(coordinate)
        target = safe_path(game.mc / "libraries", path)
        url = MIRROR + "/maven/" + path
        md5 = record.get("md5")
        if md5 and not re.fullmatch(r"[a-fA-F0-9]{32}", md5):
            raise LauncherError("LiteLoader 校验信息无效")
        game.net.download(url, target, md5=md5, task=task)
        libraries = []
        for library in record.get("libraries", []):
            library = copy.deepcopy(library)
            if str(library.get("url", "")).startswith("http:"):
                library["url"] = MIRROR + "/maven/"
            libraries.append(library)
        libraries.append(artifact(coordinate, target, url))
        libraries = list({library["name"]: library for library in libraries}.values())
        base = game.profile(minecraft)
        profile = {"id": profile_id, "inheritsFrom": minecraft, "mainClass": "net.minecraft.launchwrapper.Launch",
                   "minecraftArguments": base.get("minecraftArguments", "") + " --tweakClass com.mumfrey.liteloader.launch.LiteLoaderTweaker", "libraries": libraries}
    else:
        installer = safe_path(game.store.root / "cache", loader + "-" + minecraft + "-" + selected + ".jar")
        game.net.download(record["url"], installer, sha256=record.get("sha256"), size=record.get("size"), task=task)
        if not zipfile.is_zipfile(installer):
            raise LauncherError("下载的加载器不是有效安装器")
        if loader == "cleanroom":
            with zipfile.ZipFile(installer) as archive:
                if "install_profile.json" not in archive.namelist() or "version.json" not in archive.namelist():
                    raise LauncherError("此 Cleanroom 安装器格式尚不支持，请选择较新版本")
                if archive.getinfo("install_profile.json").file_size > 4*1024*1024 or archive.getinfo("version.json").file_size > 4*1024*1024:
                    raise LauncherError("Cleanroom 安装清单超过限制")
                manifest = json.loads(archive.read("install_profile.json"))
                profile = json.loads(archive.read("version.json"))
                if manifest.get("minecraft") != minecraft or manifest.get("processors"):
                    raise LauncherError("此 Cleanroom 安装清单不受支持")
                profile["id"] = profile_id
                profile["jar"] = minecraft
                profile["clientVersion"] = minecraft
                profile["javaVersion"] = dict(profile.get("javaVersion", {}), majorVersion=int(manifest.get("java", {}).get("minimum", profile.get("javaVersion", {}).get("majorVersion", 21))))
                for library in profile.get("libraries", []):
                    file = library.get("downloads", {}).get("artifact")
                    if not file:
                        continue
                    file.setdefault("path", maven_path(library["name"]))
                    target = safe_path(game.mc / "libraries", file["path"])
                    embedded = "maven/" + file["path"]
                    if not file.get("url") and embedded in archive.namelist():
                        if archive.getinfo(embedded).file_size > 64*1024*1024:
                            raise LauncherError("Cleanroom 内置库超过大小限制")
                        payload = archive.read(embedded)
                        if file.get("sha1") and hashlib.sha1(payload).hexdigest() != file["sha1"]:
                            raise LauncherError("Cleanroom 内置库校验失败")
                        write_library(target, payload)
                    elif not file.get("url"):
                        file["url"] = "https://maven.cleanroommc.com/" + file["path"]
        else:
            base = game.profile(minecraft)
            client = base.get("downloads", {}).get("client")
            if not client:
                raise LauncherError("原版客户端清单缺失")
            client_path = safe_path(game.mc / "versions", minecraft + "/" + minecraft + ".jar")
            game.net.download(client["url"], client_path, sha1=client.get("sha1"), size=client.get("size"), task=task)
            coordinate = "optifine:OptiFine:" + minecraft + "_" + selected
            target = safe_path(game.mc / "libraries", maven_path(coordinate))
            target.parent.mkdir(parents=True, exist_ok=True)
            libraries = []
            with zipfile.ZipFile(installer) as archive:
                if "optifine/Patcher.class" in archive.namelist():
                    java = game.ensure_java(base.get("javaVersion", {}).get("majorVersion", 8), task, instance)
                    temporary = target.with_name(target.name + ".patch-" + uuid.uuid4().hex)
                    try:
                        run_patcher(game, instance, [java, "-cp", str(installer), "optifine.Patcher", str(client_path), str(installer), str(temporary)], task)
                        if not zipfile.is_zipfile(temporary):
                            raise LauncherError("OptiFine 补丁未生成有效文件")
                        temporary.replace(target)
                    finally:
                        if temporary.exists():
                            temporary.unlink()
                else:
                    shutil.copy2(str(installer), str(target))
                if not zipfile.is_zipfile(target):
                    raise LauncherError("OptiFine 补丁未生成有效文件")
                wrapper = None
                if "launchwrapper-of.txt" in archive.namelist():
                    if archive.getinfo("launchwrapper-of.txt").file_size > 128:
                        raise LauncherError("OptiFine 启动库版本无效")
                    wrapper_version = valid_id(archive.read("launchwrapper-of.txt").decode().strip())
                    wrapper = "optifine:launchwrapper-of:" + wrapper_version, "launchwrapper-of-" + wrapper_version + ".jar"
                elif "launchwrapper-2.0.jar" in archive.namelist():
                    wrapper = "optifine:launchwrapper:2.0", "launchwrapper-2.0.jar"
                if wrapper:
                    name, entry = wrapper
                    if entry not in archive.namelist() or archive.getinfo(entry).file_size > 4*1024*1024:
                        raise LauncherError("OptiFine 启动库缺失或无效")
                    wrapper_path = safe_path(game.mc / "libraries", maven_path(name))
                    write_library(wrapper_path, archive.read(entry))
                    libraries.append(artifact(name, wrapper_path))
                else:
                    libraries.append({"name": "net.minecraft:launchwrapper:1.12"})
            libraries.insert(0, artifact(coordinate, target))
            profile = {"id": profile_id, "inheritsFrom": minecraft, "mainClass": "net.minecraft.launchwrapper.Launch", "libraries": libraries}
            tweak = ["--tweakClass", "optifine.OptiFineTweaker"]
            if base.get("arguments", {}).get("game"):
                profile["arguments"] = {"game": tweak}
            else:
                profile["minecraftArguments"] = base.get("minecraftArguments", "") + " " + " ".join(tweak)
    atomic_json(game.profile_path(profile_id), profile)
    return profile_id
