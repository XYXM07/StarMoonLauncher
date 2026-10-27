"""Public pack catalogs and a community-backed MCMOD search adapter.

ATLauncher: wiki.atlauncher.com/api-docs/v1/public/packs/
FTB: feed-the-beast.com/blog/p/ftb-api
MCMOD community search: mcmod.yisrime.de (not an official download API).
"""
import concurrent.futures
import copy
import datetime
import html
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import urllib.error

from . import __version__
from .core import LauncherError, SSL_CONTEXT, safe_path, valid_id

ATL_API = "https://api.atlauncher.com/v1"
ATL_CDN = "https://download.nodecdn.net/containers/atl"
FTB_API = "https://api.feed-the-beast.com/v1/modpacks/public/modpack"
MCMOD_API = "https://mcmod.yisrime.de/api"
PAGE_SIZE = 18
PROVIDER_AGENT = "StarMoonLauncher/{} (+https://github.com/XYXM07/StarMoonLauncher)".format(__version__)


def sources(kind):
    if kind == "map":
        return [("curseforge", "CurseForge")]
    values = [("modrinth", "Modrinth"), ("curseforge", "CurseForge")]
    if kind == "resourcepack":
        values += [("faithful", "Faithful")]
    if kind == "mod":
        values += [("github", "GitHub")]
    if kind == "modpack":
        values += [("atlauncher", "ATLauncher"), ("ftb", "FTB")]
    if kind in ("mod", "modpack"):
        values += [("mcmod", "MC百科")]
    return values


def plain(value, limit=400):
    return html.unescape(re.sub(r"<[^>]*>", "", str(value or ""))).strip()[:limit]


def https_url(value):
    value = str(value or "")
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or
            any(c in value for c in "\r\n\x00")):
        raise LauncherError("内容平台提供了无效的 HTTPS 地址")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc,
        urllib.parse.quote(parsed.path, safe="/%:@!$&'()*+,;=-._~"),
        urllib.parse.quote(parsed.query, safe="%=&;:+,/?@!$'()*-._~"), ""))


def component(value):
    value = str(value)
    if not value or len(value) > 180 or value in (".", "..") or re.search(r"[/\\\x00-\x1f]", value):
        raise LauncherError("内容平台的版本标识无效")
    return urllib.parse.quote(value, safe="")


def date_text(value):
    try:
        return datetime.datetime.fromtimestamp(int(value), datetime.timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def file_record(path, urls, size=None, hashes=None, **extra):
    # Validate against an imaginary root as well as the real fresh instance later.
    from pathlib import Path, PurePosixPath
    path = str(path).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if not path or path.endswith("/") or path == ".":
        raise LauncherError("整合包文件路径无效")
    safe_path(Path.cwd() / "provider-path-validation", path)
    path = str(PurePosixPath(path))
    for part in path.split("/"):
        if (part.rstrip(" .") != part or re.search(r'[<>"|?*\x00-\x1f]', part) or
                re.fullmatch(r"(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)):
            raise LauncherError("整合包文件路径无效")
    hashes = hashes or {}
    checks = {}
    for algorithm, length in (("sha512", 128), ("sha256", 64), ("sha1", 40), ("md5", 32)):
        value = str(hashes.get(algorithm) or "").lower()
        if value:
            if not re.fullmatch(r"[0-9a-f]{%d}" % length, value):
                raise LauncherError("整合包文件校验信息无效：" + path)
            checks[algorithm] = value
    if not checks:
        raise LauncherError("整合包文件缺少校验哈希：" + path)
    if size is not None and (type(size) is not int or not 0 <= size <= 8 * 1024 ** 3):
        raise LauncherError("整合包文件大小无效：" + path)
    urls = list(dict.fromkeys(https_url(url) for url in urls if url))
    if not urls:
        raise LauncherError("整合包文件没有下载地址：" + path)
    return dict(extra, path=path, urls=urls, size=size, **checks)


def unique_files(items):
    result = {}
    for item in items:
        key = item["path"].casefold()
        if key in result:
            previous = result[key]
            fields = ("sha1", "sha256", "sha512", "md5", "size")
            if any(previous.get(field) != item.get(field) for field in fields):
                raise LauncherError("整合包包含重复文件路径：" + item["path"])
            previous["urls"] = list(dict.fromkeys(previous["urls"] + item["urls"]))
        else:
            result[key] = item
    return list(result.values())


class ProviderHub:
    def __init__(self):
        self.cache = {}
        self.lock = threading.RLock()

    def fetch_json(self, url):
        request = urllib.request.Request(https_url(url), headers={"User-Agent": PROVIDER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=12, context=SSL_CONTEXT) as response:
            https_url(response.geturl())
            raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise LauncherError("内容平台的清单超过大小限制")
        return json.loads(raw.decode("utf-8-sig"))

    def get(self, url):
        with self.lock:
            cached = self.cache.get(url)
            if cached and time.monotonic() - cached[0] < 300:
                return copy.deepcopy(cached[1])
        for attempt in range(2):
            try:
                data = self.fetch_json(url)
                if not isinstance(data, (dict, list)):
                    raise ValueError("JSON object required")
                if isinstance(data, dict) and (data.get("error") or data.get("status") in ("error", "fail", "failure")):
                    raise ValueError("provider error")
                break
            except Exception as exc:
                if isinstance(exc, LauncherError):
                    raise
                no_retry = isinstance(exc, urllib.error.HTTPError) and exc.code in (400, 401, 403, 404, 429)
                status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
                if isinstance(exc, urllib.error.HTTPError):
                    exc.close()
                if urllib.parse.urlsplit(url).hostname == "api.github.com":
                    if status in (403, 429):
                        raise LauncherError("GitHub 请求受到限制，请稍后重试。") from None
                    if status == 404:
                        raise LauncherError("未找到公开的 GitHub 仓库或发布附件。") from None
                if attempt or no_retry:
                    raise LauncherError("内容平台暂不可用，请稍后重试：" + str(urllib.parse.urlsplit(url).hostname)) from None
                time.sleep(.2)
        with self.lock:
            if len(self.cache) >= 256:
                self.cache.pop(next(iter(self.cache)))
            self.cache[url] = (time.monotonic(), copy.deepcopy(data))
        return data

    def atl_catalog(self):
        data = self.get(ATL_API + "/packs/full/public")
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise LauncherError("内容平台返回了无效的整合包清单")
        return [p for p in items if isinstance(p, dict) and p.get("versions") and p.get("type", "public") == "public"]

    def atl_pack(self, project):
        return next((p for p in self.atl_catalog() if p.get("safeName") == project), None)

    @staticmethod
    def mcmod_search_url(query, kind):
        return "https://search.mcmod.cn/s?" + urllib.parse.urlencode({"key": query, "filter": 2 if kind == "modpack" else 1})

    def search(self, provider, query="", kind="modpack", offset=0, version="", loader=""):
        if provider not in ("atlauncher", "ftb", "mcmod") or provider not in dict(sources(kind)):
            raise LauncherError("此内容平台不支持所选内容类型")
        offset = max(0, min(int(offset), 100000))
        if provider == "mcmod":
            return self.mcmod_search(query, kind, offset)
        if provider == "atlauncher":
            items = self.atl_catalog()
            words = query.casefold().split()
            items = [p for p in items if all(w in (p.get("name", "") + " " + plain(p.get("description"), 8000)).casefold() for w in words)]
            if version:
                items = [p for p in items if any(v.get("minecraft") == version for v in p["versions"])]
            items.sort(key=lambda p: max(int(v.get("published") or 0) for v in p["versions"]), reverse=True)
            hits = [{"project_id": p["safeName"], "title": p["name"], "description": plain(p.get("description")),
                     "icon_url": "https://cdn.atlcdn.net/images/packs/" + component(p["safeName"].lower()) + ".png",
                     "author": "ATLauncher", "downloads": None, "project_type": "modpack",
                     "project_url": "https://atlauncher.com/pack/" + component(p["safeName"])} for p in items[offset:offset + PAGE_SIZE]]
            return {"hits": hits, "total_hits": len(items), "offset": offset, "order_label": "最近更新"}
        data = self.get(FTB_API + ("/search/1000?" + urllib.parse.urlencode({"term": query}) if query else "/all"))
        ids = data.get("packs")
        if not isinstance(ids, list):
            raise LauncherError("内容平台返回了无效的整合包清单")
        ids = [str(i) for i in ids if str(i).isdigit()]
        def fetch(pack_id):
            pack = self.get(FTB_API + "/" + pack_id)
            if pack.get("private"):
                return None
            art = next((a.get("url", "") for a in pack.get("art", []) if a.get("type") == "square"), "")
            return {"project_id": pack_id, "title": pack["name"], "description": plain(pack.get("synopsis") or pack.get("description")),
                    "icon_url": art, "author": ", ".join(a.get("name", "") for a in pack.get("authors", [])),
                    "downloads": int(pack.get("installs") or 0), "project_type": "modpack",
                    "project_url": "https://www.feed-the-beast.com/modpacks" + ("/" + pack_id + "-" + component(pack["slug"]) if pack.get("slug") else "")}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            hits = list(pool.map(fetch, ids[offset:offset + PAGE_SIZE]))
        return {"hits": [p for p in hits if p], "total_hits": len(ids), "offset": offset, "order_label": "平台顺序"}

    def mcmod_search(self, query, kind, offset):
        result = {"hits": [], "total_hits": 0, "offset": offset, "order_label": "相关程度",
                  "external_search_url": self.mcmod_search_url(query, kind), "notice": "MC百科社区检索，下载请前往原站。"}
        if not query:
            result["notice"] = "输入中文或英文关键词，查找百科条目。"
            result["waiting_query"] = True
            return result
        url = MCMOD_API + "/search?" + urllib.parse.urlencode({"q": query, "filter": 2 if kind == "modpack" else 1, "offset": offset})
        try:
            data = self.get(url)
            if not isinstance(data.get("results"), list):
                raise ValueError("invalid results")
            for item in data["results"][:PAGE_SIZE]:
                article = str(item.get("url") or "")
                if article.startswith("/"):
                    article = "https://www.mcmod.cn" + article
                parsed = urllib.parse.urlsplit(article)
                pattern = r"/modpack/(\d+)\.html" if kind == "modpack" else r"/class/(\d+)\.html"
                match = re.fullmatch(pattern, parsed.path)
                if parsed.scheme != "https" or parsed.hostname not in ("mcmod.cn", "www.mcmod.cn") or not match or parsed.username or parsed.password:
                    continue
                result["hits"].append({"project_id": match.group(1), "title": plain(item.get("name")),
                                       "description": plain(item.get("description")), "author": "MC百科", "downloads": None,
                                       "icon_url": "", "project_type": kind, "project_url": "https://www.mcmod.cn" + parsed.path,
                                       "action": "article"})
            # Pagination follows accepted rows, while the API offset handles a
            # fixed 18-result viewport even when its server returns 30 rows.
            result["total_hits"] = max(offset + len(result["hits"]), int(data.get("totalResults") or 0))
        except Exception:
            result["notice"] = "百科检索暂不可用，可在原站继续搜索。"
            result["unavailable"] = True
        return result

    def versions(self, provider, project):
        valid_id(project)
        if provider == "atlauncher":
            pack = self.atl_pack(project)
            if not pack:
                raise LauncherError("未找到公开的整合包")
            return [{"id": str(v["version"]), "name": str(v["version"]), "version_number": str(v["version"]),
                     "game_versions": [str(v.get("minecraft", ""))], "date_published": date_text(v.get("published")),
                     "version_type": "release"} for v in pack["versions"]]
        if provider != "ftb" or not project.isdigit():
            raise LauncherError("此内容平台不提供自动安装")
        pack = self.get(FTB_API + "/" + project)
        if pack.get("private"):
            raise LauncherError("未找到公开的整合包")
        values = [v for v in pack.get("versions", []) if not v.get("private") and v.get("type") not in ("archived", "private")]
        values.sort(key=lambda v: int(v.get("updated") or v.get("released") or 0), reverse=True)
        return [{"id": str(v["id"]), "name": str(v["name"]), "version_number": str(v["name"]),
                 "game_versions": [str(t["version"]) for t in v.get("targets", []) if t.get("name") == "minecraft"],
                 "date_published": date_text(v.get("released") or v.get("updated")),
                 "version_type": "release" if v.get("type") == "release" else "beta"} for v in values]

    def manifest(self, provider, project, version):
        if not any(v["id"] == str(version) for v in self.versions(provider, project)):
            raise LauncherError("内容版本和项目不匹配")
        try:
            plan = self.atl_manifest(project, version) if provider == "atlauncher" else self.ftb_manifest(project, version)
            plan["files"] = unique_files(plan["files"])
            return plan
        except (KeyError, TypeError, ValueError, AttributeError):
            raise LauncherError("内容平台返回了无效的整合包清单") from None

    def ftb_manifest(self, project, version):
        pack = self.get(FTB_API + "/" + project)
        data = self.get(FTB_API + "/" + project + "/" + component(version))
        if str(data.get("parent")) != project or str(data.get("id")) != str(version) or data.get("private"):
            raise LauncherError("内容版本和项目不匹配")
        targets = {t.get("name"): str(t.get("version", "")) for t in data.get("targets", [])}
        loaders = [t for t in data.get("targets", []) if t.get("type") == "modloader"]
        loader = loaders[0].get("name", "").lower() if loaders else "vanilla"
        if len(loaders) > 1 or loader not in ("vanilla", "fabric", "quilt", "forge", "neoforge") or not targets.get("minecraft"):
            raise LauncherError("此整合包使用了暂不支持的加载器")
        files = []
        for item in data.get("files", []):
            if item.get("serveronly") or item.get("optional"):
                continue
            name = str(item.get("name", ""))
            if not name or "/" in name or "\\" in name:
                raise LauncherError("整合包文件路径无效")
            mirrors = item.get("mirrors") or []
            # These are publisher-supplied mirrors, not guessed CurseForge URLs.
            mirrors = [u for u in mirrors if isinstance(u, str)]
            mirrors.sort(key=lambda u: urllib.parse.urlsplit(u).hostname not in ("files.feed-the-beast.com", "cdn.feed-the-beast.com"))
            hashes = dict(item.get("hashes") or {})
            if item.get("sha1"):
                hashes.setdefault("sha1", item["sha1"])
            directory = str(item.get("path") or "").rstrip("/")
            files.append(file_record((directory + "/" if directory else "") + name,
                                     mirrors + [item.get("url")], item.get("size"), hashes))
        if not files or len(files) > 20000:
            raise LauncherError("内容平台返回了无效的整合包清单")
        return {"name": pack["name"], "version_name": str(data.get("name") or version), "game_version": targets["minecraft"], "loader": loader,
                "loader_version": targets.get(loader, ""), "files": files, "archives": []}

    def atl_manifest(self, project, version):
        pack = self.atl_pack(project)
        base = ATL_CDN + "/packs/" + component(project) + "/versions/" + component(version) + "/"
        data = self.get(base + "Configs.json")
        if str(data.get("version")) != str(version):
            raise LauncherError("内容版本和项目不匹配")
        info = data.get("loader") or {}
        loader = str(info.get("type") or "vanilla").lower()
        if loader not in ("vanilla", "fabric", "quilt", "forge", "neoforge"):
            raise LauncherError("此整合包使用了暂不支持的加载器")
        meta = info.get("metadata") or {}
        loader_version = str(meta.get("loader") or meta.get("version") or "")
        if loader == "forge" and loader_version.startswith(str(data.get("minecraft")) + "-"):
            loader_version = loader_version.split("-", 1)[1]
        if data.get("mainClass") or data.get("extraArguments") or data.get("libraries"):
            raise LauncherError("此整合包需要额外安装步骤，请使用原平台安装后添加游戏文件夹。")
        if loader != "vanilla" and not loader_version and not (meta.get("latest") or meta.get("recommended")):
            raise LauncherError("整合包未提供加载器版本")
        mods = [m for m in data.get("mods", []) if m.get("client", True)]
        selected = {m.get("name") for m in mods if not m.get("optional") or m.get("recommended") or m.get("selected")}
        for _ in range(len(mods) + 1):
            previous = len(selected)
            for m in mods:
                if m.get("name") in selected:
                    selected.update(m.get("depends") or [])
            if len(selected) == previous:
                break
        paths = {"mods": "mods", "coremods": "coremods", "dependency": "mods", "depandency": "mods",
                 "resourcepack": "resourcepacks", "texturepack": "texturepacks", "shaderpack": "shaderpacks",
                 "root": "", "flan": "Flan", "ic2lib": "mods/ic2", "denlib": "mods/denlib"}
        files, archives = [], []
        for item in mods:
            if item.get("name") not in selected:
                continue
            typ = str(item.get("type") or "mods")
            if typ == "forge" and loader == "vanilla":
                raise LauncherError("此整合包需要额外安装步骤，请使用原平台安装后添加游戏文件夹。")
            if item.get("download") == "browser":
                raise LauncherError("此整合包包含需在浏览器手动下载的文件，请前往 ATLauncher 安装：" + plain(item.get("name")))
            if typ not in paths and typ != "extract":
                raise LauncherError("此整合包需要额外安装步骤，请使用原平台安装后添加游戏文件夹。")
            filename = str(item.get("file") or "")
            if not filename or "/" in filename or "\\" in filename:
                raise LauncherError("整合包文件路径无效")
            url = str(item.get("url") or "")
            mode = item.get("download")
            if mode == "server":
                url = ATL_CDN + "/" + url.lstrip("/")
            elif mode != "direct":
                raise LauncherError("此整合包需要额外安装步骤，请使用原平台安装后添加游戏文件夹。")
            hashes = {name: item.get(name) for name in ("sha512", "sha256", "sha1", "md5")}
            if typ == "extract":
                folder = paths.get(item.get("extractTo", "root"))
                if folder is None:
                    raise LauncherError("整合包文件路径无效")
                prefix = str(item.get("extractFolder") or "").replace("%s%", "/").strip("/")
                record = file_record(filename, [url], item.get("filesize"), hashes, destination=folder, prefix=prefix)
                archives.append(record)
            else:
                files.append(file_record((paths[typ] + "/" + filename).lstrip("/"), [url], item.get("filesize"), hashes))
        if not data.get("noConfigs", False):
            config = data.get("configs") or {}
            archives.insert(0, file_record("Configs.zip", [base + "Configs.zip"], config.get("filesize"),
                                          {"sha1": config.get("sha1")}, destination="", prefix=""))
        if len(files) + len(archives) > 20000:
            raise LauncherError("内容平台返回了无效的整合包清单")
        return {"name": pack["name"], "version_name": str(version), "game_version": str(data.get("minecraft") or ""), "loader": loader,
                "loader_version": loader_version, "files": files, "archives": archives}
