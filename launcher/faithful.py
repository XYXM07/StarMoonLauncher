"""Faithful Java packs from the public catalog used by faithfulpack.net."""
import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from .core import LauncherError, atomic_json, read_json, safe_path

SITE = "https://www.faithfulpack.net"
LICENSE = SITE + "/license"
AUTHOR = "Faithful Resource Pack"
# Only Java catalogs linked by the official archive; never Bedrock or Dungeons.
PACKS = {
    "faithful_32x_java": ("Faithful 32x", "faithful32x", "f32", "32x · Java Edition · Faithful"),
    "faithful_64x_java": ("Faithful 64x", "faithful64x", "f64", "64x · Java Edition · Faithful"),
    "classic_32x_java": ("Classic Faithful 32x", "classic32x", "cf32", "32x · Java Edition · Classic"),
    "classic_32x_jappa_java": ("Classic Faithful 32x Jappa", "classic32x-jappa", "cf32j", "32x · Java Edition · Classic Jappa"),
    "classic_64x_java": ("Classic Faithful 64x", "classic64x", "cf64", "64x · Java Edition · Classic"),
    "classic_64x_jappa_java": ("Classic Faithful 64x Jappa", "classic64x-jappa", "cf64j", "64x · Java Edition · Classic Jappa"),
}


def compatible(declared, minecraft):
    if not minecraft or declared == minecraft:
        return True
    if declared.endswith(".x"):
        base = declared[:-2]
        return minecraft == base or bool(re.fullmatch(re.escape(base) + r"\.\d+", minecraft))
    return False


def download_url(value):
    if not isinstance(value, str):
        return ""
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None,443)
            or any(c in value for c in "\r\n\x00") or not parsed.path.lower().endswith(".zip")):
        return ""
    official = parsed.hostname == "database.faithfulpack.net" and parsed.path.startswith("/packs/")
    github = (parsed.hostname == "github.com" and parsed.path.lower().startswith("/faithful-resource-pack/")
              and "/releases/download/" in parsed.path)
    modrinth = parsed.hostname == "cdn.modrinth.com" and parsed.path.startswith("/data/")
    return urllib.parse.urlunsplit(parsed._replace(fragment="")) if official or github or modrinth else ""


def published_link(links):
    for key in ("download","modrinth"):
        value = links.get(key)
        try:
            direct = download_url(value)
            if direct:
                return direct, direct, "", ""
            parsed = urllib.parse.urlsplit(value or "")
            parts = parsed.path.strip("/").split("/")
            if (parsed.scheme=="https" and parsed.hostname=="modrinth.com" and not parsed.username and not parsed.password
                    and len(parts)==4 and parts[0]=="resourcepack" and parts[2]=="version"
                    and all(re.fullmatch(r"[A-Za-z0-9_.+\-]+",parts[index]) for index in (1,3))):
                return value, "", parts[1], parts[3]
        except ValueError:
            continue
    return "", "", "", ""


class FaithfulSource:
    def __init__(self, content):
        self.content = content
        self.cache = {}
        self.locks = {key: threading.RLock() for key in PACKS}
        self.install_lock = threading.RLock()

    def catalog(self, project):
        if project not in PACKS:
            raise LauncherError("未知的 Faithful 资源包")
        with self.locks[project]:
            cached = self.cache.get(project)
            if cached and time.monotonic() - cached[0] < 300:
                return copy.deepcopy(cached[1])
            relative = "/data/downloads/" + project + ".json"
            endpoints = (SITE + relative, "https://raw.githubusercontent.com/Faithful-Resource-Pack/Website/main/public" + relative)
            for endpoint in endpoints:
                try:
                    data = self.content.net.json(endpoint)
                    if not isinstance(data, dict) or len(data) > 1000 or any(not isinstance(rows,list) for rows in data.values()):
                        raise ValueError("Invalid catalog")
                    self.cache[project] = (time.monotonic(), data)
                    return copy.deepcopy(data)
                except (LauncherError, ValueError, TypeError):
                    continue
        raise LauncherError("无法读取 Faithful 官方下载目录，请稍后重试")

    def versions(self, project, minecraft=""):
        result = []
        for game_version, entries in self.catalog(project).items():
            if not compatible(game_version, minecraft):
                continue
            for entry in entries:
                if not isinstance(entry,dict):
                    continue
                links = entry.get("links")
                if not isinstance(links,dict):
                    continue
                reference, url, mr_project, mr_version = published_link(links)
                # CurseForge-only entries remain on the original site, never guessed.
                if not reference:
                    continue
                release = str(entry.get("file_name") or entry.get("file_version") or entry.get("file_type") or "Release")[:160]
                identifier = hashlib.sha256(json.dumps([project,game_version,entry.get("file_version"),release,
                    entry.get("date"),reference], ensure_ascii=False, separators=(",",":")).encode("utf-8")).hexdigest()[:24]
                status = str(entry.get("file_type", "Release")).lower()
                result.append({"id":identifier, "name": release + " · " + game_version,
                    "version_number":str(entry.get("file_version") or release), "game_versions":[minecraft or game_version],
                    "loaders":[], "date_published":str(entry.get("date") or ""),
                    "version_type":"release" if status in ("release","github") else "alpha" if status == "alpha" else "beta",
                    "url":url, "modrinth_project":mr_project, "modrinth_version":mr_version, "declared_game_version":game_version})
        result.sort(key=lambda item:(item["date_published"], item["version_type"]=="release"),reverse=True)
        return list({item["id"]:item for item in result}.values())

    def search(self, query="", version="", offset=0):
        hits, failures = [], 0
        query = query.casefold().strip().replace("经典","classic").replace("忠实","faithful")
        offset = max(0,int(offset))
        for key, (name, page, image, description) in PACKS.items():
            if query not in (name + " " + description).casefold():
                continue
            if version:
                try:
                    if not self.versions(key,version):
                        continue
                except LauncherError:
                    failures += 1
                    continue
            hits.append({"project_id":key, "slug":key, "title":name, "description":description,
                "author":AUTHOR, "downloads":None, "icon_url":SITE + "/image/banners/" + image + ".jpg",
                "project_url":SITE + "/" + page, "license_url":LICENSE, "project_type":"resourcepack"})
        if failures and not hits:
            raise LauncherError("无法读取 Faithful 官方下载目录，请稍后重试")
        result = {"hits":hits[offset:offset+18], "total_hits":len(hits), "offset":offset, "order_label":"系列名称"}
        if failures:
            result["notice"] = "部分 Faithful 版本目录暂不可用，请稍后重试。"
        return result

    def validate_archive(self, path, task):
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries)>100000 or sum(item.file_size for item in entries)>2*1024**3:
                    raise ValueError("Archive too large")
                if "pack.mcmeta" not in archive.namelist() or not any(item.filename.startswith("assets/") and not item.is_dir() for item in entries):
                    raise ValueError("Missing resource pack files")
                if archive.getinfo("pack.mcmeta").file_size>1024*1024:
                    raise ValueError("Metadata too large")
                metadata = json.loads(archive.read("pack.mcmeta").decode("utf-8-sig"))
                if not isinstance(metadata,dict) or not isinstance(metadata.get("pack"),dict):
                    raise ValueError("Invalid metadata")
                names = set()
                for item in entries:
                    task.check()
                    name = item.filename.replace("\\","/")
                    if (name.startswith("/") or ":" in name or ".." in PurePosixPath(name).parts
                            or name in names or (item.external_attr >> 16) & 0o170000 == 0o120000):
                        raise ValueError("Unsafe archive path")
                    names.add(name)
                    if not item.is_dir():
                        with archive.open(item) as stream:
                            while stream.read(256*1024):
                                task.check()
        except (OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile, NotImplementedError, RuntimeError):
            raise LauncherError("Faithful 下载文件不是有效的 Java 资源包") from None

    def install(self, project, version, instance_id, task):
        self.content._check_mod_instance(instance_id,"resourcepack")
        instance = self.content.store.instance(instance_id)
        chosen = next((item for item in self.versions(project,instance["game_version"]) if item["id"]==str(version)),None)
        if not chosen:
            raise LauncherError("所选 Faithful 版本不存在或不支持此 Minecraft 版本")
        filename = project + "-" + chosen["id"] + ".zip"
        cache = self.content.store.root / "cache"
        cache.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="faithful-",dir=str(cache)) as temporary:
            downloaded = Path(temporary) / filename
            task.update("下载 Faithful 资源包",10)
            url, checks = self.resolve_download(chosen,instance["game_version"])
            self.content.net.download(url,downloaded,task=task,**checks)
            task.update("检查 Faithful 资源包",98)
            self.validate_archive(downloaded,task)
            digest = hashlib.sha256()
            with downloaded.open("rb") as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b""):
                    task.check()
                    digest.update(chunk)
            with self.install_lock:
                self.content._check_mod_instance(instance_id,"resourcepack")
                self.commit(project,chosen,instance_id,downloaded,filename,digest.hexdigest(),task)
        return {"instance_id":instance_id}

    def resolve_download(self, chosen, minecraft):
        api = "https://api.modrinth.com/v2"
        project_id = chosen.get("modrinth_project")
        if project_id:
            data = self.content.net.json(api+"/project/"+urllib.parse.quote(project_id,safe="")+
                                         "/version/"+urllib.parse.quote(chosen["modrinth_version"],safe=""))
        elif urllib.parse.urlsplit(chosen["url"]).hostname=="cdn.modrinth.com":
            parts = urllib.parse.urlsplit(chosen["url"]).path.strip("/").split("/")
            if len(parts)<5 or parts[2]!="versions":
                raise LauncherError("Faithful 下载文件不是有效的 Java 资源包")
            project_id = parts[1]
            data = self.content.net.json(api+"/version/"+urllib.parse.quote(parts[3],safe=""))
        else:
            return chosen["url"], {}
        project = self.content.net.json(api+"/project/"+urllib.parse.quote(project_id,safe=""))
        if (not isinstance(data,dict) or not isinstance(project,dict) or project.get("project_type")!="resourcepack"
                or data.get("project_id")!=project.get("id") or minecraft not in data.get("game_versions",[])):
            raise LauncherError("所选 Faithful 版本不存在或不支持此 Minecraft 版本")
        files = [item for item in data.get("files",[]) if isinstance(item,dict) and download_url(item.get("url"))]
        if chosen["url"]:
            files = [item for item in files if urllib.parse.unquote(urllib.parse.urlsplit(item["url"]).path)
                     == urllib.parse.unquote(urllib.parse.urlsplit(chosen["url"]).path)]
        if not files:
            raise LauncherError("Faithful 下载文件不是有效的 Java 资源包")
        selected = next((item for item in files if item.get("primary")),files[0])
        hashes = selected.get("hashes") or {}
        checks = {name:hashes[name] for name,length in (("sha512",128),("sha1",40))
                  if isinstance(hashes.get(name),str) and re.fullmatch("[0-9a-fA-F]{%d}"%length,hashes[name])}
        if type(selected.get("size")) is int and selected["size"]>0:
            checks["size"] = selected["size"]
        return selected["url"], checks

    def commit(self, project, chosen, instance_id, downloaded, filename, digest, task):
        registry = self.content.store.instance_dir(instance_id) / "content.json"
        records = read_json(registry,[])
        old = [item for item in records if item.get("provider")=="faithful" and item.get("project_id")==project]
        owned = {item["filename"] for item in old}
        folder = self.content._folder(instance_id,"resourcepack")
        folder.mkdir(parents=True,exist_ok=True)
        if any((folder/name).is_symlink() for name in (filename,filename+".disabled")):
            raise LauncherError("目标文件不能是符号链接")
        disabled = any((folder / (name+".disabled")).is_file() and not (folder/name).exists() for name in owned)
        target = safe_path(folder,filename + (".disabled" if disabled else ""))
        if any((folder/name).exists() for name in (filename,filename+".disabled")) and filename not in owned:
            raise LauncherError("目标实例中已有同名文件")
        staged = target.with_name(target.name + ".install-" + uuid.uuid4().hex)
        backups, installed = [], False
        record = {"provider":"faithful", "project_id":project, "version_id":chosen["id"], "filename":filename,
            "name":PACKS[project][0]+" · "+chosen["name"], "kind":"resourcepack", "installed":time.time(),
            "author":AUTHOR, "project_url":SITE+"/"+PACKS[project][1], "license_url":LICENSE, "sha256":digest}
        try:
            shutil.copy2(str(downloaded),str(staged))
            task.check()
            for name in owned:
                for original_name in (name,name+".disabled"):
                    original = safe_path(folder,original_name)
                    if (folder/original_name).is_symlink():
                        raise LauncherError("目标文件不能是符号链接")
                    if original.is_file():
                        backup = original.with_name(original.name+".faithful-backup-"+uuid.uuid4().hex)
                        os.replace(str(original),str(backup))
                        backups.append((original,backup))
            os.replace(str(staged),str(target))
            installed = True
            task.check()
            atomic_json(registry,[item for item in records if item not in old]+[record])
        except Exception:
            if installed and target.is_file():
                target.unlink()
            for original,backup in reversed(backups):
                if backup.exists() and not original.exists():
                    os.replace(str(backup),str(original))
            raise
        finally:
            if staged.exists():
                staged.unlink()
        for original,backup in backups:
            try:
                trash = self.content.store.instance_dir(instance_id)/"trash"
                trash.mkdir(exist_ok=True)
                shutil.move(str(backup),str(trash/(uuid.uuid4().hex+"-"+original.name)))
            except OSError:
                pass
