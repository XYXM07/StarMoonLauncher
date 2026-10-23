import hashlib
import json
import os
import shutil
import time
import urllib.parse
import uuid
import zipfile
from pathlib import Path

from .core import LauncherError, atomic_json, extract_zip, read_json, request_json, safe_path, valid_id

MODRINTH = "https://api.modrinth.com/v2"
CURSEFORGE = "https://api.curseforge.com/v1"


class ContentManager:
    def __init__(self, store, network, game):
        self.store = store
        self.net = network
        self.game = game

    def curse(self, path):
        key = self.store.config()["curseforge_key"]
        if not key:
            raise LauncherError("使用 CurseForge 前请在设置中填写 API Key；Modrinth 无需密钥")
        return request_json(CURSEFORGE + path, headers={"x-api-key": key})

    def search(self, query="", kind="mod", version="", loader="", offset=0, provider="modrinth", sort="downloads"):
        if kind not in ("mod", "modpack", "resourcepack", "shader"):
            raise LauncherError("不支持的内容类型")
        if provider == "curseforge":
            params = {"gameId": 432, "classId": {"mod": 6, "modpack": 4471, "resourcepack": 12, "shader": 6552}[kind],
                      "searchFilter": query, "index": offset, "pageSize": 18, "sortField": 6, "sortOrder": "desc"}
            if version:
                params["gameVersion"] = version
            if loader in ("forge", "fabric", "quilt", "neoforge"):
                params["modLoaderType"] = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}[loader]
            data = self.curse("/mods/search?" + urllib.parse.urlencode(params))
            hits = [{"project_id": str(p["id"]), "title": p["name"], "description": p["summary"], "slug": p["slug"],
                     "downloads": p["downloadCount"], "icon_url": (p.get("logo") or {}).get("thumbnailUrl", ""),
                     "author": ", ".join(a["name"] for a in p.get("authors", [])), "categories": [c["name"] for c in p.get("categories", [])],
                     "project_type": kind} for p in data["data"]]
            return {"hits": hits, "total_hits": data["pagination"]["totalCount"], "offset": offset}
        facets = [["project_type:" + kind]]
        if version:
            facets.append(["versions:" + version])
        if loader and loader != "vanilla" and kind == "mod":
            facets.append(["categories:" + loader])
        params = {"query": query, "facets": json.dumps(facets), "index": sort if sort in ("downloads", "relevance", "updated", "newest", "follows") else "downloads", "limit": 18, "offset": offset}
        return self.net.json(MODRINTH + "/search?" + urllib.parse.urlencode(params))

    def versions(self, project_id, instance_id="", kind="mod", provider="modrinth"):
        valid_id(str(project_id))
        instance = self.store.instance(instance_id) if instance_id else None
        if provider == "curseforge":
            params = {"pageSize": 50}
            if instance and kind != "modpack":
                params["gameVersion"] = instance["game_version"]
                if kind == "mod" and instance["loader"] != "vanilla":
                    params["modLoaderType"] = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}[instance["loader"]]
            data = self.curse("/mods/{}/files?{}".format(project_id, urllib.parse.urlencode(params)))
            return [{"id": str(f["id"]), "name": f["displayName"], "version_number": f["fileName"],
                     "game_versions": f.get("gameVersions", []), "loaders": [], "date_published": f["fileDate"],
                     "version_type": {1: "release", 2: "beta", 3: "alpha"}.get(f.get("releaseType"), "release")} for f in data["data"]]
        params = {}
        if instance and kind != "modpack":
            params["game_versions"] = json.dumps([instance["game_version"]])
            if kind == "mod":
                params["loaders"] = json.dumps([instance["loader"]])
        suffix = "?" + urllib.parse.urlencode(params) if params else ""
        return self.net.json(MODRINTH + "/project/" + str(project_id) + "/version" + suffix)

    def install_project(self, project_id, version_id, instance_id, kind, provider, task):
        if provider == "curseforge":
            file = self.curse("/mods/{}/files/{}".format(valid_id(str(project_id)), valid_id(str(version_id))))["data"]
            if kind == "modpack":
                archive = self.store.root / "cache" / ("cf-" + str(file["id"]) + ".zip")
                self._cf_download(file, archive, task)
                return self.import_pack(archive, task)
            self._check_mod_instance(instance_id, kind)
            self._cf_install(project_id, file, instance_id, kind, task, set())
            return {"instance_id": instance_id}
        version = self.net.json(MODRINTH + "/version/" + valid_id(str(version_id)))
        if str(version["project_id"]) != str(project_id):
            raise LauncherError("内容版本和项目不匹配")
        project = self.net.json(MODRINTH + "/project/" + valid_id(str(project_id)))
        if project["project_type"] != kind:
            raise LauncherError("内容类型不匹配")
        if kind == "modpack":
            file = next((f for f in version["files"] if f["filename"].endswith(".mrpack")), None)
            if not file:
                raise LauncherError("此版本没有可用的 mrpack 文件")
            target = self.store.root / "cache" / (valid_id(version_id) + ".mrpack")
            self.net.download(file["url"], target, sha512=file.get("hashes", {}).get("sha512"), sha1=file.get("hashes", {}).get("sha1"), task=task, size=file.get("size"))
            return self.import_pack(target, task)
        self._check_mod_instance(instance_id, kind)
        self._mr_install(version, instance_id, kind, task, set())
        return {"instance_id": instance_id}

    def _check_mod_instance(self, instance_id, kind):
        instance = self.store.instance(instance_id)
        if any(p["instance_id"] == instance_id and p["running"] for p in self.game.process_status()):
            raise LauncherError("请先关闭此实例的游戏再修改内容")
        if kind == "mod" and instance["loader"] == "vanilla":
            raise LauncherError("此实例是原版，请先创建 Fabric / Quilt / Forge / NeoForge 实例")

    def _folder(self, instance_id, kind):
        return self.store.game_dir(instance_id) / {"mod": "mods", "resourcepack": "resourcepacks", "shader": "shaderpacks"}[kind]

    def _record(self, instance_id, item):
        path = self.store.instance_dir(instance_id) / "content.json"
        items = read_json(path, [])
        old = [i for i in items if i.get("project_id") == item["project_id"] and i["provider"] == item["provider"]]
        for previous in old:
            if previous["filename"] != item["filename"]:
                directory = self._folder(instance_id, previous["kind"])
                for filename in (previous["filename"], previous["filename"] + ".disabled"):
                    target = safe_path(directory, filename)
                    if target.is_file():
                        target.unlink()
        items = [i for i in items if i not in old]
        items.append(item)
        atomic_json(path, items)

    def _mr_install(self, version, instance_id, kind, task, seen):
        if version["id"] in seen:
            return
        seen.add(version["id"])
        instance = self.store.instance(instance_id)
        if instance["game_version"] not in version.get("game_versions", []):
            raise LauncherError("所选内容版本不支持此 Minecraft 版本")
        if kind == "mod" and instance["loader"] not in version.get("loaders", []):
            raise LauncherError("所选模组与此实例的加载器不兼容")
        for dep in version.get("dependencies", []):
            if dep["dependency_type"] != "required":
                continue
            if dep.get("version_id"):
                child = self.net.json(MODRINTH + "/version/" + valid_id(dep["version_id"]))
            elif dep.get("project_id"):
                choices = self.versions(dep["project_id"], instance_id, "mod")
                if not choices:
                    raise LauncherError("找不到兼容的必需依赖：" + dep["project_id"])
                child = choices[0]
            else:
                raise LauncherError("此模组包含无法自动解析的必需依赖，请手动安装")
            self._mr_install(child, instance_id, "mod", task, seen)
        file = next((f for f in version["files"] if f.get("primary")), version["files"][0])
        task.update("安装内容 · " + file["filename"], 50)
        target = safe_path(self._folder(instance_id, kind), file["filename"])
        if Path(file["filename"]).name != file["filename"]:
            raise LauncherError("内容文件名不合法")
        self.net.download(file["url"], target, sha1=file.get("hashes", {}).get("sha1"), sha512=file.get("hashes", {}).get("sha512"), task=task, size=file.get("size"))
        self._record(instance_id, {"provider": "modrinth", "project_id": version["project_id"], "version_id": version["id"],
                                  "filename": file["filename"], "name": version["name"], "kind": kind, "installed": time.time()})

    def _cf_download(self, file, target, task):
        url = file.get("downloadUrl")
        if not url:
            raise LauncherError("作者禁止第三方自动下载，请到 CurseForge 项目页面手动下载并导入：" + file["fileName"])
        sha1 = next((h["value"] for h in file.get("hashes", []) if h["algo"] == 1), None)
        self.net.download(url, target, sha1=sha1, task=task, size=file.get("fileLength"))

    def _cf_install(self, project_id, file, instance_id, kind, task, seen):
        if file["id"] in seen:
            return
        seen.add(file["id"])
        for dep in file.get("dependencies", []):
            if dep["relationType"] != 3:
                continue
            choices = self.versions(str(dep["modId"]), instance_id, "mod", "curseforge")
            if not choices:
                raise LauncherError("找不到必需依赖：" + str(dep["modId"]))
            child = self.curse("/mods/{}/files/{}".format(dep["modId"], choices[0]["id"]))["data"]
            self._cf_install(str(dep["modId"]), child, instance_id, "mod", task, seen)
        filename = file["fileName"]
        if Path(filename).name != filename:
            raise LauncherError("内容文件名不合法")
        task.update("安装内容 · " + filename, 50)
        self._cf_download(file, safe_path(self._folder(instance_id, kind), filename), task)
        self._record(instance_id, {"provider": "curseforge", "project_id": str(project_id), "version_id": str(file["id"]),
                                  "filename": filename, "name": file["displayName"], "kind": kind, "installed": time.time()})

    def list_local(self, instance_id):
        result = []
        records = read_json(self.store.instance_dir(instance_id) / "content.json", [])
        for kind in ("mod", "resourcepack", "shader"):
            for path in self._folder(instance_id, kind).glob("*"):
                if path.is_file() and path.name.endswith((".jar", ".zip", ".disabled")):
                    original = path.name[:-9] if path.name.endswith(".disabled") else path.name
                    record = next((r for r in records if r["filename"] == original), {})
                    result.append(dict(record, filename=path.name, kind=kind, size=path.stat().st_size, enabled=not path.name.endswith(".disabled")))
        return result

    def toggle(self, instance_id, filename, kind, remove=False):
        self._check_mod_instance(instance_id, "resourcepack")
        if Path(filename).name != filename:
            raise LauncherError("无效的文件名")
        path = safe_path(self._folder(instance_id, kind), filename)
        if not path.is_file():
            raise LauncherError("文件不存在")
        if remove:
            recycle = self.store.instance_dir(instance_id) / "trash"
            recycle.mkdir(exist_ok=True)
            shutil.move(str(path), str(recycle / (uuid.uuid4().hex[:8] + "-" + path.name)))
        else:
            target = path.with_name(path.name[:-9] if path.name.endswith(".disabled") else path.name + ".disabled")
            if target.exists():
                raise LauncherError("目标文件已存在")
            path.rename(target)

    def import_pack(self, archive, task):
        with zipfile.ZipFile(archive) as zf:
            entries = zf.infolist()
            if len(entries) > 100000 or sum(entry.file_size for entry in entries) > 8 * 1024 ** 3:
                raise LauncherError("整合包解压大小或文件数量超过限制")
            def metadata(name):
                if zf.getinfo(name).file_size > 8 * 1024 * 1024:
                    raise LauncherError("整合包索引过大")
                return json.loads(zf.read(name))
            names = zf.namelist()
            if "modrinth.index.json" in names:
                index = metadata("modrinth.index.json")
                if index.get("formatVersion") != 1 or index.get("game") != "minecraft":
                    raise LauncherError("不支持此整合包格式版本")
                deps = index["dependencies"]
                loader_key = next((key for key in ("fabric-loader", "quilt-loader", "forge", "neoforge") if key in deps), None)
                loader = {"fabric-loader": "fabric", "quilt-loader": "quilt"}.get(loader_key, loader_key or "vanilla")
                instance = self.game.create(index["name"], deps["minecraft"], loader, deps.get(loader_key, ""))
                self.store.update_instance(instance["id"], {"pack": index["name"], "pack_format": "mrpack", "pack_install_pending": True})
                self.game.install(instance["id"], task)
                self.store.update_instance(instance["id"], {"ready": False})
                directory = self.store.instance_dir(instance["id"]) / "game"
                jobs = []
                for item in index.get("files", []):
                    if item.get("env", {}).get("client") == "unsupported":
                        continue
                    if not item.get("downloads"):
                        raise LauncherError("整合包文件没有下载地址：" + item["path"])
                    hashes = item.get("hashes", {})
                    if not hashes.get("sha1") and not hashes.get("sha512"):
                        raise LauncherError("整合包文件缺少校验哈希")
                    jobs.append({"url": item["downloads"][0], "target": safe_path(directory, item["path"]),
                                 "sha1": hashes.get("sha1"), "sha512": hashes.get("sha512"), "size": item.get("fileSize")})
                self.game.download_batch(jobs, task, "下载整合包文件", 20, 90)
                extract_zip(archive, directory, prefix="overrides/", task=task)
                extract_zip(archive, directory, prefix="client-overrides/", task=task)
            elif "manifest.json" in names:
                index = metadata("manifest.json")
                if index.get("manifestType") != "minecraftModpack" or index.get("manifestVersion") != 1:
                    raise LauncherError("不支持此 CurseForge 整合包格式")
                if not self.store.config()["curseforge_key"]:
                    raise LauncherError("导入 CurseForge 整合包需要在设置中填写 API Key")
                loaders = index.get("minecraft", {}).get("modLoaders", [])
                loader_id = next((l["id"] for l in loaders if l.get("primary")), loaders[0]["id"] if loaders else "")
                loader, _, loader_version = loader_id.partition("-")
                instance = self.game.create(index.get("name", "CurseForge 整合包"), index["minecraft"]["version"], loader or "vanilla", loader_version)
                self.store.update_instance(instance["id"], {"pack_install_pending": True})
                self.game.install(instance["id"], task)
                self.store.update_instance(instance["id"], {"ready": False, "pack": index.get("name")})
                for i, item in enumerate(index.get("files", [])):
                    task.update("下载整合包模组 · {} / {}".format(i + 1, len(index["files"])), 50)
                    file = self.curse("/mods/{}/files/{}".format(item["projectID"], item["fileID"]))["data"]
                    self._cf_install(str(item["projectID"]), file, instance["id"], "mod", task, set())
                prefix = index.get("overrides", "overrides").rstrip("/") + "/"
                extract_zip(archive, self.store.instance_dir(instance["id"]) / "game", prefix=prefix, task=task)
            elif "blockcraft.json" in names:
                index = metadata("blockcraft.json")
                instance = self.game.create(index["name"], index["game_version"], index["loader"], index.get("loader_version", ""))
                self.store.update_instance(instance["id"], {"pack_install_pending": True})
                self.game.install(instance["id"], task)
                self.store.update_instance(instance["id"], {"ready": False})
                extract_zip(archive, self.store.instance_dir(instance["id"]) / "game", prefix="overrides/", task=task)
            else:
                raise LauncherError("请选择 Modrinth .mrpack、CurseForge .zip 或本启动器导出的整合包")
        task.check()
        self.store.update_instance(instance["id"], {"ready": True, "pack_install_pending": False})
        return {"instance_id": instance["id"]}

    def export_pack(self, instance_id, task):
        instance = self.store.instance(instance_id)
        self._check_mod_instance(instance_id, "resourcepack")
        destination = self.store.root / "exports" / (instance_id + "-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6] + ".zip")
        destination.parent.mkdir(parents=True, exist_ok=True)
        task.update("导出模组与配置", 20)
        game_dir = self.store.game_dir(instance_id)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("blockcraft.json", json.dumps({k: instance[k] for k in ("name", "game_version", "loader", "loader_version")}, ensure_ascii=False))
            for folder in ("mods", "config", "resourcepacks", "shaderpacks", "defaultconfigs", "kubejs", "scripts"):
                for path in (game_dir / folder).rglob("*"):
                    task.check()
                    if path.is_file() and not path.is_symlink():
                        zf.write(str(path), "overrides/" + path.relative_to(game_dir).as_posix())
        return {"path": str(destination), "filename": destination.name}

    def check_updates(self, instance_id):
        updates = []
        records = read_json(self.store.instance_dir(instance_id) / "content.json", [])
        for item in records:
            if not safe_path(self._folder(instance_id, item["kind"]), item["filename"]).is_file():
                continue
            versions = self.versions(item["project_id"], instance_id, item["kind"], item["provider"])
            if versions and str(versions[0]["id"]) != str(item["version_id"]):
                updates.append(dict(item, new_version=versions[0]["id"], new_name=versions[0]["name"]))
        return updates
