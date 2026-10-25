"""Read public MCMOD search results and related links, without executing page scripts."""
import base64
import re
import threading
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from .core import Cancelled, LauncherError, SSL_CONTEXT, USER_AGENT
from .diagnostics import NoRedirect
from .github_source import repository
from .providers import MCMOD_API, plain


def project_link(value):
    """Decode MCMOD's published outbound target, then accept supported project pages only."""
    try:
        url = urllib.parse.urljoin("https://www.mcmod.cn/", str(value))
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname == "link.mcmod.cn" and parsed.path.startswith("/target/"):
            encoded = urllib.parse.unquote(parsed.path[len("/target/"):])
            url = base64.b64decode(encoded, validate=True).decode("utf-8")
            parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or
                parsed.query or parsed.fragment or re.search(r"[\x00-\x20]", url)):
            return None
        pieces = parsed.path.strip("/").split("/")
        if parsed.hostname in ("github.com", "www.github.com") and len(pieces) >= 2:
            key = repository("/".join(pieces[:2]))
            return {"provider": "github", "project": key, "url": "https://github.com/" + key}
        if (parsed.hostname in ("modrinth.com", "www.modrinth.com") and len(pieces) >= 2 and pieces[0] == "mod" and
                re.fullmatch(r"[A-Za-z0-9_-]{1,128}", pieces[1])):
            return {"provider": "modrinth", "project": pieces[1], "url": "https://modrinth.com/mod/" + pieces[1]}
    except (ValueError, UnicodeError, LauncherError):
        pass
    return None


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.links, self.anchor = [], [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        related = bool(self.stack and self.stack[-1][1]) or "common-link-icon-frame" in attrs.get("class", "").split()
        if tag == "a" and len(self.links) < 2000:
            self.anchor = {"url": attrs.get("href", ""), "label": attrs.get("data-original-title") or attrs.get("title", ""),
                           "text": [], "related": related}
        if tag not in ("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"):
            self.stack.append((tag, related))

    def handle_data(self, data):
        if self.anchor:
            self.anchor["text"].append(data[:500])

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor:
            self.anchor["title"] = plain(" ".join(self.anchor.pop("text")) or self.anchor["label"], 200)
            self.links.append(self.anchor)
            self.anchor = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


class MCModLinks:
    def __init__(self, hub):
        self.hub, self.cache, self.lock = hub, {}, threading.RLock()

    def fetch_page(self, url):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in ("www.mcmod.cn", "search.mcmod.cn") or
                parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise LauncherError("无效的 MC百科条目地址")
        opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=SSL_CONTEXT))
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        with opener.open(request, timeout=10) as response:
            raw = response.read(2 * 1024 ** 2 + 1)
            encoding = response.headers.get_content_charset() or "utf-8"
        if len(raw) > 2 * 1024 ** 2:
            raise LauncherError("MC百科页面超过大小限制")
        return raw.decode(encoding, errors="replace")

    def page(self, url, task=None):
        if task:
            task.check()
        with self.lock:
            value = self.cache.get(url)
            if value and time.monotonic() - value[0] < 300:
                return value[1]
        text = self.fetch_page(url)
        if task:
            task.check()
        with self.lock:
            if len(self.cache) >= 64:
                self.cache.pop(next(iter(self.cache)))
            self.cache[url] = (time.monotonic(), text)
        return text

    @staticmethod
    def articles(items, limit=30):
        result = {}
        for item in items:
            url = urllib.parse.urljoin("https://www.mcmod.cn/", str(item.get("url", "")))
            try:
                parsed = urllib.parse.urlsplit(url)
                port = parsed.port
            except ValueError:
                continue
            match = re.fullmatch(r"/class/(\d{1,10})\.html", parsed.path)
            if (parsed.scheme != "https" or parsed.hostname not in ("www.mcmod.cn", "mcmod.cn") or not match or
                    parsed.username or parsed.password or port not in (None, 443) or parsed.query or parsed.fragment):
                continue
            key = match.group(1)
            title = plain(item.get("title") or item.get("name"), 160)
            if re.match(r"^(?:https?://)?(?:www\.)?mcmod\.cn/", title):
                title = ""
            if key not in result or not result[key]["title"] and title:
                result[key] = {"id": key, "title": title, "url": "https://www.mcmod.cn/class/" + key + ".html"}
        return list(result.values())[:limit]

    def search(self, query, task=None):
        query = str(query).strip()[:100]
        if not query:
            return []
        try:
            parser = PageLinks()
            parser.feed(self.page("https://search.mcmod.cn/s?" + urllib.parse.urlencode({"key": query, "filter": 1}), task))
            pages = {int(match.group(1)) for item in parser.links for match in [re.search(r"[?&]page=([23])(?:&|$)", item["url"])] if match}
            items = list(parser.links)
            for page in sorted(pages):
                try:
                    more = PageLinks()
                    more.feed(self.page("https://search.mcmod.cn/s?" + urllib.parse.urlencode({"key": query, "filter": 1, "page": page}), task))
                    items.extend(more.links)
                except Cancelled:
                    raise
                except (LauncherError, OSError, ValueError, LookupError):
                    continue
            found = self.articles(items, 90)
            # Exact names can appear after numerous addons in the site's default ordering.
            exact = re.compile(r"(?:^|\()\s*" + re.escape(query.casefold()) + r"\s*(?:\)|$|\()")
            found.sort(key=lambda item: not bool(exact.search(item["title"].casefold().replace("（", "(").replace("）", ")"))))
            if found:
                return found[:30]
        except Cancelled:
            raise
        except (LauncherError, OSError, ValueError, LookupError):
            pass
        try:
            if task:
                task.check()
            data = self.hub.get(MCMOD_API + "/search?" + urllib.parse.urlencode({"q": query, "filter": 1, "offset": 0}))
            return self.articles(data.get("results", [])[:30])
        except Cancelled:
            raise
        except Exception:
            return []

    def detail(self, identifier, task=None):
        if not re.fullmatch(r"\d{1,10}", str(identifier)):
            raise LauncherError("无效的 MC百科条目地址")
        url = "https://www.mcmod.cn/class/" + str(identifier) + ".html"
        links = []
        try:
            parser = PageLinks()
            parser.feed(self.page(url, task))
            links = [item for item in parser.links if item["related"]]
        except Cancelled:
            raise
        except (LauncherError, OSError, ValueError, LookupError):
            pass
        if not links:
            try:
                if task:
                    task.check()
                data = self.hub.get(MCMOD_API + "/class?id=" + str(identifier))
                links = data.get("links", [])[:30]
            except Cancelled:
                raise
            except Exception:
                pass
        result = {}
        for link in links:
            item = project_link(link.get("url"))
            if item:
                item.update(source=url, label=plain(link.get("label") or link.get("title"), 160))
                result[item["url"]] = item
        return list(result.values())[:12]
