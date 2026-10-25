"""Public mod website links from installed metadata and recorded download sources."""
import re
import urllib.parse

from .core import LauncherError, valid_id
from .github_source import repository
from .providers import ProviderHub


def website_url(value):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if len(value) > 2048 or re.search(r"[\x00-\x20\x7f]", value):
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or
                parsed.port is not None and not 0 < parsed.port < 65536):
            return ""
        if parsed.hostname in ("mcmod.cn", "www.mcmod.cn", "search.mcmod.cn"):
            host = parsed.hostname if parsed.port in (None, 80, 443) else parsed.netloc
            value = urllib.parse.urlunsplit(("https", host, parsed.path, parsed.query, parsed.fragment))
        return value
    except ValueError:
        return ""


def site_title(url, default="项目主页"):
    host = urllib.parse.urlsplit(url).hostname
    if host in ("mcmod.cn", "www.mcmod.cn"):
        return "MC百科"
    if host == "search.mcmod.cn":
        return "在MC百科搜索"
    if host in ("modrinth.com", "www.modrinth.com"):
        return "Modrinth"
    if host in ("curseforge.com", "www.curseforge.com"):
        return "CurseForge"
    if host in ("github.com", "www.github.com"):
        return "GitHub"
    return default


def metadata_sites(data):
    result = []
    def add(value, title="项目主页"):
        url = website_url(value)
        if url and all(item["url"].rstrip("/") != url.rstrip("/") for item in result):
            result.append({"title": site_title(url, title), "url": url})
    contact = data.get("contact", {})
    if isinstance(contact, dict):
        for key, title in (("homepage", "项目主页"), ("sources", "源代码"), ("issues", "问题反馈"), ("wiki", "文档")):
            add(contact.get(key), title)
    for key in ("displayURL", "url", "homepage"):
        add(data.get(key))
    add(data.get("issueTrackerURL"), "问题反馈")
    custom = data.get("custom", {})
    if isinstance(custom, dict):
        menu = custom.get("modmenu", {})
        if isinstance(menu, dict) and isinstance(menu.get("links"), dict):
            for value in list(menu["links"].values())[:12]:
                add(value)
    return result[:16]


def mod_websites(item):
    result = []
    def add(value, title="项目主页"):
        url = website_url(value)
        if url and all(link["url"].rstrip("/") != url.rstrip("/") for link in result):
            result.append({"title": site_title(url, title), "url": url})
    links = item.get("websites", [])
    for link in (links[:16] if isinstance(links, list) else []):
        if isinstance(link, dict):
            add(link.get("url"), link.get("title", "项目主页"))
    add(item.get("project_url"))
    provider, project = item.get("provider"), str(item.get("project_id") or "")
    try:
        if provider == "github":
            add("https://github.com/" + repository(project))
        elif provider == "modrinth":
            add("https://modrinth.com/mod/" + valid_id(project))
        elif provider == "curseforge" and re.fullmatch(r"\d+", project):
            add("https://www.curseforge.com/projects/" + project)
        elif provider == "mcmod" and re.fullmatch(r"\d+", project):
            add("https://www.mcmod.cn/class/" + project + ".html")
    except LauncherError:
        pass
    if not any(link["title"] in ("MC百科", "在MC百科搜索") for link in result):
        name = item.get("mod_name") or item.get("mod_id")
        if not name:
            name = str(item.get("filename") or "")
            if name.lower().endswith(".disabled"):
                name = name[:-9]
            name = re.sub(r"(?i)\.(?:jar|zip)$", "", name)
            name = re.sub(r"(?i)[-_ ](?:v?\d.*|(?:forge|neoforge|fabric|quilt)(?:[-_ ].*)?)$", "", name)
        add(ProviderHub.mcmod_search_url(str(name).strip()[:160], "mod"), "在MC百科搜索")
    return result[:20]
