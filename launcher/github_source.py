"""Public GitHub Release JARs, checked as data before touching installed mods."""
import hashlib
import json
import os
import re
import shutil
import urllib.parse
import uuid
import zipfile

from .core import LauncherError, atomic_json, read_json, safe_path
from .diagnostics import Diagnostics, numeric_version, satisfies
from .providers import https_url, plain
from .progress import DownloadProgress

API = "https://api.github.com/repos/"
MAX_JAR = 512 * 1024 * 1024


def repository(value):
    value = str(value).strip()
    if value.startswith("github.com/"):
        value = "https://" + value
    if "://" in value:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in ("github.com", "www.github.com") or
                parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise LauncherError("请输入公开 GitHub 仓库地址或 owner/repo")
        pieces = parsed.path.strip("/").split("/")
        if len(pieces) > 2 and pieces[2] != "releases":
            raise LauncherError("请输入公开 GitHub 仓库地址或 owner/repo")
        value = "/".join(pieces[:2])
    value = value.rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    parts = value.split("/")
    if (len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", parts[0]) or
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", parts[1]) or parts[1] in (".", "..")):
        raise LauncherError("请输入公开 GitHub 仓库地址或 owner/repo")
    return value


def requirement_matches(version, requirement):
    if isinstance(requirement, list):
        answers = [requirement_matches(version, r) for r in requirement]
        return True if True in answers else None if None in answers else False
    text = str(requirement).strip()
    basic = satisfies(version, requirement)
    if basic is not None:
        return basic
    if "||" in text:
        return requirement_matches(version, text.split("||"))
    clauses = text.split()
    if len(clauses) > 1:
        answers = [requirement_matches(version, c) for c in clauses]
        return False if False in answers else None if None in answers else True
    exact = re.fullmatch(r"\[(\d+(?:\.\d+)*)\]", text)
    if exact:
        return satisfies(version, exact.group(1))
    special = re.fullmatch(r"([~^])(\d+(?:\.\d+)*)", text)
    if special:
        current, lower = numeric_version(version), numeric_version(special.group(2))
        if current is None or lower is None:
            return None
        parts = list(map(int, special.group(2).split(".")))
        index = max(0, len(parts) - 2) if special.group(1) == "~" else next((i for i, n in enumerate(parts) if n), len(parts)-1)
        upper = parts[:index+1]
        upper[index] += 1
        upper = tuple(upper) + (0,) * (5-len(upper))
        return lower <= current < upper
    return satisfies(version, requirement)


def file_sha256(path, task=None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if task:
                task.check()
            digest.update(chunk)
    return digest.hexdigest()


def inspect_mod(path):
    """Bounded metadata reads only; no class loading, Java process or extraction."""
    records = []
    try:
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > 100000:
                raise ValueError("too many entries")
            names = set(archive.namelist())
            def read(name):
                info = archive.getinfo(name)
                if info.file_size > 1024 * 1024:
                    raise ValueError("metadata too large")
                return archive.read(info).decode("utf-8-sig")
            for filename, loader in (("fabric.mod.json", "fabric"), ("quilt.mod.json", "quilt")):
                if filename not in names:
                    continue
                raw = json.loads(read(filename))
                data = raw.get("quilt_loader", {}) if loader == "quilt" else raw
                deps = data.get("depends", {})
                if loader == "quilt":
                    deps = {d["id"]: d.get("versions", "*") for d in deps if isinstance(d, dict) and
                            d.get("id") and not d.get("optional") and not d.get("unless")}
                if not isinstance(deps, dict):
                    raise ValueError("invalid dependencies")
                name = data.get("metadata", {}).get("name") if loader == "quilt" else data.get("name")
                environment = raw.get("minecraft", {}).get("environment", "*") if loader == "quilt" else data.get("environment", "*")
                records.append({"id": str(data.get("id", "")), "version": str(data.get("version", "?")),
                    "name": plain(name or data.get("id")), "loader": loader, "depends": deps,
                    "breaks": data.get("breaks", {}) if loader == "fabric" else {},
                    "server_only": environment == "server"})
            for filename, loader in (("META-INF/mods.toml", "forge"), ("META-INF/neoforge.mods.toml", "neoforge")):
                if filename not in names:
                    continue
                import tomli
                data = tomli.loads(read(filename))
                for mod in data.get("mods", []):
                    deps, breaks = {}, {}
                    for dep in data.get("dependencies", {}).get(mod.get("modId"), []):
                        if str(dep.get("side", "BOTH")).upper() == "SERVER":
                            continue
                        if dep.get("type") == "incompatible" and dep.get("modId"):
                            breaks[str(dep["modId"])] = str(dep.get("versionRange", "*"))
                        elif dep.get("mandatory", dep.get("type") == "required") and dep.get("modId"):
                            deps[str(dep["modId"])] = str(dep.get("versionRange", "*"))
                    selected_loader = "neoforge" if "neoforge" in deps else loader
                    records.append({"id": str(mod.get("modId", "")), "version": str(mod.get("version", "?")),
                        "name": plain(mod.get("displayName") or mod.get("modId")), "loader": selected_loader,
                        "depends": deps, "breaks": breaks, "server_only": False})
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        raise LauncherError("无法读取此附件的模组元数据，请确认选择的是正式模组 JAR。") from None
    if not records or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", r["id"]) for r in records):
        raise LauncherError("此 JAR 没有可识别的模组元数据，不能自动安装。")
    if any(r["id"] in ("minecraft", "java", "fabricloader", "quilt_loader", "forge", "neoforge") for r in records):
        raise LauncherError("此附件是加载器或运行组件，请在游戏安装页面选择。")
    return records


class GitHubSource:
    def __init__(self, content):
        self.content = content
        self.hub = content.providers

    def search(self, query, offset=0):
        if not query.strip():
            return {"hits": [], "total_hits": 0, "waiting_query": True,
                    "notice": "输入公开 GitHub 仓库地址或 owner/repo，浏览作者发布的模组。"}
        repo = repository(query)
        data = self.hub.get(API + repo)
        canonical = repository(data.get("full_name") or repo)
        if data.get("private"):
            raise LauncherError("仅支持公开 GitHub 仓库")
        hit = {"project_id": canonical, "title": plain(data.get("name") or canonical),
               "description": plain(data.get("description")), "author": str(data.get("owner", {}).get("login", canonical.split("/")[0])),
               "icon_url": str(data.get("owner", {}).get("avatar_url") or ""), "downloads": None, "project_type": "mod",
               "project_url": "https://github.com/" + canonical}
        return {"hits": [hit] if not offset else [], "total_hits": 1, "offset": offset, "order_label": "仓库发布"}

    @staticmethod
    def asset(repo, data):
        name = str(data.get("name") or "")
        if (not name.lower().endswith(".jar") or len(name) > 180 or re.search(r'[/\\:<>"|?*\x00-\x1f]', name) or
                re.search(r"(?i)(?:^|[-_.])(?:sources?|javadoc|dev|deobf|tests?)(?:[-_.]|$)", name)):
            return None
        if name.rstrip(" .") != name or re.fullmatch(r"(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name):
            return None
        size, identifier = data.get("size"), data.get("id")
        if (type(identifier) is not int or identifier <= 0 or type(size) is not int or not 0 < size <= MAX_JAR or
                data.get("state", "uploaded") != "uploaded"):
            return None
        url = https_url(data.get("browser_download_url"))
        parsed = urllib.parse.urlsplit(url)
        prefix = "/" + repo + "/releases/download/"
        if (parsed.hostname != "github.com" or parsed.query or parsed.fragment or not parsed.path.casefold().startswith(prefix.casefold()) or
                urllib.parse.unquote(parsed.path).rsplit("/", 1)[-1] != name):
            raise LauncherError("GitHub 附件地址与仓库不匹配")
        digest = str(data.get("digest") or "")
        if digest and not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
            raise LauncherError("GitHub 附件校验信息无效")
        return {"id": str(identifier), "filename": name, "url": url, "size": size,
                "sha256": digest[7:].lower() if digest else None}

    def versions(self, project):
        repo = repository(project)
        releases = self.hub.get(API + repo + "/releases?per_page=100")
        if not isinstance(releases, list):
            raise LauncherError("GitHub 返回了无效的发布列表")
        result = []
        for release in releases[:100]:
            if not isinstance(release, dict) or release.get("draft"):
                continue
            for raw in release.get("assets", []):
                asset = self.asset(repo, raw)
                if asset:
                    result.append({"id": asset["id"], "name": asset["filename"], "release_name": plain(release.get("name") or release.get("tag_name")),
                                   "version_number": str(release.get("tag_name") or ""), "game_versions": [],
                                   "date_published": str(release.get("published_at") or ""),
                                   "version_type": "beta" if release.get("prerelease") else "release"})
        return result[:600]

    def download(self, repo, asset_id, task):
        if not str(asset_id).isdigit():
            raise LauncherError("GitHub 附件标识无效")
        data = self.hub.get(API + repo + "/releases/assets/" + str(asset_id))
        asset = self.asset(repo, data)
        if not asset or asset["id"] != str(asset_id):
            raise LauncherError("请选择正式模组 JAR 附件")
        cache = self.content.store.root/"cache/github"/repo
        cache.mkdir(parents=True, exist_ok=True)
        target = safe_path(cache, asset["id"] + "-" + asset["filename"])
        receipt = safe_path(cache, asset["id"] + ".json")
        try:
            previous = read_json(receipt, {})
        except (OSError, ValueError):
            previous = {}
        digest = asset["sha256"]
        if not digest and isinstance(previous, dict) and previous.get("url") == asset["url"] and previous.get("size") == asset["size"]:
            value = str(previous.get("sha256") or "")
            digest = value if re.fullmatch(r"[a-f0-9]{64}", value) else None
        temporary = target.with_name(target.name + ".download-" + uuid.uuid4().hex)
        try:
            task.update("下载 GitHub 模组附件", 5)
            transfer = DownloadProgress(task, [{"size": asset["size"]}], "下载 GitHub 模组附件", 5, 92).file(0)
            self.content.net.download(asset["url"], target if digest else temporary,
                                      sha256=digest, size=asset["size"], task=transfer)
            task.check()
            if not digest:
                os.replace(str(temporary), str(target))
            actual = file_sha256(target, task)
            if target.stat().st_size != asset["size"] or digest and actual != digest:
                raise LauncherError("GitHub 附件校验失败")
            atomic_json(receipt, {"url": asset["url"], "size": asset["size"], "sha256": actual})
            return asset, target, actual
        finally:
            if temporary.exists():
                temporary.unlink()

    def validate(self, records, instance_id, task, allow_unknown=False):
        instance = self.content.store.instance(instance_id)
        matching = [r for r in records if r["loader"] == instance["loader"] and not r["server_only"]]
        if not matching:
            raise LauncherError("此附件的加载器或运行端与目标实例不匹配")
        snapshot, _ = Diagnostics(self.content.store, self.content.game).snapshot(instance_id, task)
        provided = {r["id"]: r["version"] for r in snapshot["mods"] if r.get("id")}
        for record in snapshot["mods"]:
            for alias in record.get("provides", []):
                if isinstance(alias, str):
                    provided[alias] = record["version"]
                elif isinstance(alias, dict) and alias.get("id"):
                    provided[alias["id"]] = str(alias.get("version") or record["version"])
        provided.update({r["id"]: r["version"] for r in matching})
        provided.update(minecraft=instance["game_version"], java=snapshot.get("java_required", "?"))
        provided[instance["loader"] if instance["loader"] in ("forge", "neoforge") else "fabricloader" if instance["loader"] == "fabric" else "quilt_loader"] = instance.get("loader_version") or "?"
        unknown = False
        for record in matching:
            for identifier, required in record.get("breaks", {}).items():
                if identifier in provided and requirement_matches(provided[identifier], required) is True:
                    raise LauncherError("模组声明与已安装内容不兼容：" + identifier)
            if "minecraft" not in record["depends"]:
                unknown = True
            for identifier, required in record["depends"].items():
                if identifier not in provided:
                    raise LauncherError("缺少必需前置模组，请先安装：" + identifier)
                answer = requirement_matches(provided[identifier], required)
                if answer is False:
                    raise LauncherError("模组版本要求不匹配：{} {}，当前为 {}".format(identifier, required, provided[identifier]))
                if answer is None:
                    unknown = True
        if unknown and not allow_unknown:
            raise LauncherError("无法确认此模组的全部版本要求，请检查元数据或主动允许未确认的兼容性。")
        return matching, snapshot, unknown

    def install(self, project, asset_id, instance_id, task, allow_unknown=False):
        repo = repository(project)
        self.content._check_mod_instance(instance_id, "mod")
        asset, cached, digest = self.download(repo, asset_id, task)
        task.update("检查 GitHub 模组兼容性", 96)
        records, snapshot, unknown = self.validate(inspect_mod(cached), instance_id, task, allow_unknown)
        ids = {r["id"] for r in records}
        registry = self.content.store.instance_dir(instance_id)/"content.json"
        entries = read_json(registry, [])
        old = [r for r in entries if r.get("provider") == "github" and r.get("project_id", "").casefold() == repo.casefold()
               and ids.intersection(r.get("mod_ids", []))]
        owned = {r["filename"] for r in old}
        for record in snapshot["mods"]:
            if record.get("id") in ids and record["file"].split("!", 1)[0] not in owned:
                raise LauncherError("目标实例已存在相同模组 ID，请先移除旧模组：" + record["id"])
        folder = self.content._folder(instance_id, "mod")
        folder.mkdir(parents=True, exist_ok=True)
        disabled = any(safe_path(folder, name + ".disabled").is_file() and not safe_path(folder, name).exists() for name in owned)
        target = safe_path(folder, asset["filename"] + (".disabled" if disabled else ""))
        if target.exists() and asset["filename"] not in owned:
            raise LauncherError("目标实例中已有同名文件")
        record = {"provider": "github", "project_id": repo, "version_id": str(asset_id), "filename": asset["filename"],
                  "name": records[0]["name"], "kind": "mod", "mod_ids": sorted(ids), "sha256": digest,
                  "compatibility_unconfirmed": unknown}
        temporary = target.with_name(target.name + ".install-" + uuid.uuid4().hex)
        backups, installed = [], False
        try:
            shutil.copy2(str(cached), str(temporary))
            task.check()
            for name in owned:
                for original in (safe_path(folder, name), safe_path(folder, name + ".disabled")):
                    if original.is_file():
                        backup = original.with_name(original.name + ".github-backup-" + uuid.uuid4().hex)
                        os.replace(str(original), str(backup))
                        backups.append((original, backup))
            os.replace(str(temporary), str(target))
            installed = True
            task.check()
            atomic_json(registry, [r for r in entries if r not in old] + [record])
        except Exception:
            if installed and target.is_file() and file_sha256(target) == digest:
                target.unlink()
            for original, backup in reversed(backups):
                if backup.exists() and not original.exists():
                    os.replace(str(backup), str(original))
            raise
        finally:
            if temporary.exists():
                temporary.unlink()
        for original, backup in backups:
            try:
                trash = self.content.store.instance_dir(instance_id)/"trash"
                trash.mkdir(exist_ok=True)
                shutil.move(str(backup), str(trash/(uuid.uuid4().hex + "-" + original.name)))
            except OSError:
                pass  # A sibling backup without a .jar extension is safe to retain.
        return {"instance_id": instance_id, "mod_ids": sorted(ids), "github_unverified": unknown}
