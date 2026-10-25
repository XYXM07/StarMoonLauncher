"""Download public Java profile skins using Mojang's profile and texture services."""
import base64
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .core import LauncherError, SSL_CONTEXT, USER_AGENT, atomic_json, read_json, safe_path


class OfficialRedirectsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise LauncherError("官方皮肤接口发生重定向，请稍后重试")


class PlayerSkins:
    def __init__(self,store):
        self.store = store
        self.cache = store.root/"cache/player-skins"

    def get(self,url,limit):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in ("api.mojang.com","api.minecraftservices.com","sessionserver.mojang.com","textures.minecraft.net") or parsed.username or parsed.password or parsed.port not in (None,443):
            raise LauncherError("皮肤地址不属于官方服务")
        request = urllib.request.Request(url,headers={"User-Agent":USER_AGENT,"Accept":"application/json, image/png"})
        opener = urllib.request.build_opener(OfficialRedirectsOnly,urllib.request.HTTPSHandler(context=SSL_CONTEXT))
        try:
            with opener.open(request,timeout=20) as response:
                if response.status == 204: raise LauncherError("未找到这个正版玩家")
                payload = response.read(limit+1)
            if len(payload)>limit: raise LauncherError("官方皮肤数据超过大小限制")
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code in (204,404): raise LauncherError("未找到这个正版玩家或皮肤") from None
            if exc.code == 429: raise LauncherError("官方皮肤接口请求过于频繁，请稍后重试") from None
            raise LauncherError("官方皮肤接口暂不可用（HTTP {}）".format(exc.code)) from None
        except (urllib.error.URLError,TimeoutError,OSError):
            raise LauncherError("无法连接官方皮肤服务，请检查网络后重试") from None

    @staticmethod
    def clean_png(payload):
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtGui import QImage, QImageReader, QPainter
        if len(payload)>1024*1024: raise LauncherError("皮肤图片超过 1 MB")
        source = QBuffer()
        source.setData(QByteArray(payload)); source.open(QIODevice.ReadOnly)
        reader = QImageReader(source)
        if bytes(reader.format()).lower()!=b"png" or (reader.size().width(),reader.size().height()) not in ((64,64),(64,32)):
            raise LauncherError("官方返回的皮肤不是有效的 64×64 或 64×32 PNG")
        decoded = reader.read()
        if decoded.isNull(): raise LauncherError("无法读取皮肤图片")
        clean = QImage(decoded.size(),QImage.Format_ARGB32)
        clean.fill(0)
        painter = QPainter(clean); painter.drawImage(0,0,decoded); painter.end()
        output = QBuffer(); output.open(QIODevice.WriteOnly)
        if not clean.save(output,"PNG"): raise LauncherError("保存皮肤图片失败")
        return bytes(output.data()),clean.height()

    @staticmethod
    def texture_url(value):
        p = urllib.parse.urlsplit(str(value))
        if (p.scheme not in ("http","https") or p.hostname != "textures.minecraft.net" or p.username or p.password or
                p.port not in (None,443) or p.query or p.fragment or not re.fullmatch(r"/texture/[a-fA-F0-9]{32,64}",p.path)):
            raise LauncherError("玩家档案中的皮肤地址无效")
        return "https://textures.minecraft.net"+p.path

    def download(self,player):
        player = str(player).strip()
        compact = player.replace("-","")
        try:
            if re.fullmatch(r"[a-fA-F0-9]{32}",compact) and (len(player)==32 or re.fullmatch(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}",player)):
                identifier = compact.lower()
            elif re.fullmatch(r"[A-Za-z0-9_]{1,16}",player):
                try:
                    raw = self.get("https://api.mojang.com/users/profiles/minecraft/"+player,64*1024)
                except LauncherError:
                    raw = self.get("https://api.minecraftservices.com/minecraft/profile/lookup/name/"+player,64*1024)
                lookup = json.loads(raw)
                identifier = lookup["id"]
            else:
                raise LauncherError("请输入有效的 Java 正版玩家名或 UUID")
            if not isinstance(identifier,str) or not re.fullmatch(r"[a-fA-F0-9]{32}",identifier): raise ValueError()
            identifier = identifier.lower()
            profile = json.loads(self.get("https://sessionserver.mojang.com/session/minecraft/profile/"+identifier,128*1024))
            if profile.get("id","").replace("-","").lower()!=identifier: raise ValueError()
            value = next((p.get("value") for p in profile.get("properties",[]) if isinstance(p,dict) and p.get("name")=="textures"),None)
            if not value: raise LauncherError("这个玩家没有公开的自定义皮肤")
            textures = json.loads(base64.b64decode(value,validate=True)).get("textures",{})
            skin = textures.get("SKIN")
            if not isinstance(skin,dict) or not skin.get("url"): raise LauncherError("这个玩家没有公开的自定义皮肤")
            url = self.texture_url(skin["url"])
            payload,height = self.clean_png(self.get(url,1024*1024))
            model = "slim" if height==64 and skin.get("metadata",{}).get("model")=="slim" else "default"
            name = profile.get("name",player)
            if not isinstance(name,str) or not re.fullmatch(r"[A-Za-z0-9_]{1,16}",name): name = identifier
        except (ValueError,KeyError,TypeError,AttributeError):
            raise LauncherError("官方玩家档案数据无效，请稍后重试") from None
        folder = self.cache/identifier
        folder.mkdir(parents=True,exist_ok=True)
        path = folder/(hashlib.sha256(payload).hexdigest()+".png")
        self.write_png(path,payload)
        return {"name":name,"uuid":identifier,"model":model,"path":str(path)}

    def account_skin(self, account):
        identifier = str(account.get("id", "")).replace("-", "").lower()
        if account.get("type") != "microsoft" or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise LauncherError("请输入有效的 Java 正版玩家名或 UUID")
        folder = self.cache / identifier
        receipt = folder / "avatar.json"
        url = self.texture_url(account["skin_url"]) if account.get("skin_url") else ""
        cached = None
        try:
            data = read_json(receipt, {})
            name = str(data.get("file", ""))
            if re.fullmatch(r"[a-f0-9]{64}\.png", name):
                path = safe_path(folder, name)
                if path.is_file() and path.stat().st_size <= 1024 * 1024:
                    cached = str(path)
                    if data.get("url", "") == url and time.time() - float(data.get("time", 0)) < 3600:
                        return cached
        except (OSError, ValueError, TypeError):
            pass
        try:
            if url:
                payload, _ = self.clean_png(self.get(url, 1024 * 1024))
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / (hashlib.sha256(payload).hexdigest() + ".png")
                self.write_png(path, payload)
            else:
                path = Path(self.download(identifier)["path"])
            atomic_json(receipt, {"file": path.name, "url": url, "time": time.time()})
            return str(path)
        except (LauncherError, OSError):
            if cached:
                return cached
            raise

    @staticmethod
    def write_png(path,payload):
        temporary = path.with_name(path.name+".tmp-"+uuid.uuid4().hex)
        try:
            temporary.write_bytes(payload)
            temporary.replace(path)
        finally:
            if temporary.exists(): temporary.unlink()

    def export(self,source,destination):
        source = Path(source).resolve()
        if self.cache.resolve() not in source.parents or source.is_symlink() or not source.is_file() or source.stat().st_size>1024*1024:
            raise LauncherError("请先获取玩家皮肤")
        payload,_ = self.clean_png(source.read_bytes())
        path = Path(destination)
        if path.suffix.lower()!=".png": path = path.with_name(path.name+".png")
        if not path.parent.is_dir() or path.is_dir(): raise LauncherError("请选择有效的 PNG 保存路径")
        self.write_png(path,payload)
        return {"path":str(path)}
