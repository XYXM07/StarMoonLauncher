"""Per-account offline textures, served only over an ephemeral loopback endpoint."""
import base64
import hashlib
import json
import shutil
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections import deque

from .core import LauncherError, safe_path

AGENT = "authlib-injector-1.2.8.jar"
AGENT_SHA256 = "9c7f4343e6c82034958ffb48c14a2cb0c85928be7283103ce17da00c6d5a7b10"


class SkinManager:
    def __init__(self, store, accounts):
        self.store, self.accounts = store, accounts

    def account(self, account_id):
        account = next((a for a in self.store.config()["accounts"] if a["id"] == account_id), None)
        if not account or account["type"] != "offline":
            raise LauncherError("只能为离线账号设置本地皮肤")
        return account

    def path(self, account):
        return safe_path(self.store.root, account["skin_path"]) if account.get("skin_path") else None

    def save(self, account_id, source, model="default"):
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage, QImageReader, QPainter
        account = self.account(account_id)
        if model not in ("default", "slim"):
            raise LauncherError("未知皮肤模型")
        source = Path(source)
        if not source.is_file() or source.stat().st_size > 1024*1024:
            raise LauncherError("请选择不超过 1 MB 的 PNG 皮肤")
        reader = QImageReader(str(source))
        if bytes(reader.format()).lower() != b"png" or (reader.size().width(), reader.size().height()) not in ((64,64),(64,32)):
            raise LauncherError("皮肤必须是 64×64 或 64×32 PNG")
        if reader.size().height() == 32 and model == "slim":
            raise LauncherError("64×32 旧格式皮肤仅支持宽臂")
        decoded = reader.read()
        if decoded.isNull():
            raise LauncherError("无法读取皮肤图片")
        # Re-encode pixels into a fresh image, excluding source metadata.
        clean = QImage(decoded.size(), QImage.Format_ARGB32)
        clean.fill(0)
        painter = QPainter(clean)
        painter.drawImage(0,0,decoded)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not clean.save(buffer, "PNG"):
            raise LauncherError("保存皮肤失败")
        payload = bytes(buffer.data())
        digest = hashlib.sha256(payload).hexdigest()
        relative = "skins/{}/{}.png".format(account_id,digest)
        destination = safe_path(self.store.root, relative)
        destination.parent.mkdir(parents=True,exist_ok=True)
        temporary = destination.with_suffix(".tmp-"+uuid.uuid4().hex)
        try:
            temporary.write_bytes(payload)
            temporary.replace(destination)
        finally:
            if temporary.exists(): temporary.unlink()
        return self.update(account_id,{"skin_path":relative,"skin_model":model})

    def update(self, account_id, changes):
        with self.accounts.lock:
            self.account(account_id)
            config = self.store.config()
            updated = []
            for account in config["accounts"]:
                if account["id"] == account_id:
                    account = dict(account)
                    for key in ("skin_path","skin_model"):
                        account.pop(key,None)
                    account.update(changes)
                updated.append(account)
            self.store.save({"accounts":updated})
        return self.account(account_id)

    def reset(self, account_id):
        return self.update(account_id,{})

    def prepare(self, account):
        if account["type"] != "offline" or not account.get("skin_path"):
            return None
        path = self.path(account)
        if not path.is_file():
            raise LauncherError("离线皮肤文件不存在，请重新选择或恢复默认")
        bundled = Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parents[1])) / "assets" / AGENT
        if not bundled.is_file() or hashlib.sha256(bundled.read_bytes()).hexdigest() != AGENT_SHA256:
            raise LauncherError("内置皮肤组件校验失败，请重新下载启动器")
        agent = self.store.root / "runtime" / "offline-skins" / AGENT
        agent.parent.mkdir(parents=True,exist_ok=True)
        if not agent.is_file() or hashlib.sha256(agent.read_bytes()).hexdigest() != AGENT_SHA256:
            shutil.copy2(str(bundled),str(agent))
        return SkinSession(account,path.read_bytes(),agent)


class SkinSession:
    def __init__(self, account, png, agent):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        self.account, self.png = dict(account), png
        self.requests = deque(maxlen=128)
        self.closed = False
        key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        public = key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")
        public_der = key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        self.metadata = {"meta":{"serverName":"StarMoon Offline Skins","implementationName":"StarMoon","implementationVersion":"0.7.5","feature.non_email_login":True,"feature.enable_profile_key":False},
                         "skinDomains":["127.0.0.1"],"signaturePublickey":public}
        self.token = uuid.uuid4().hex
        prefix = "/"+self.token
        session = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass

            def send_payload(self,value,status=200,content_type="application/json; charset=utf-8"):
                body = value if isinstance(value,bytes) else json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type",content_type)
                self.send_header("Content-Length",str(len(body)))
                self.send_header("Cache-Control","no-store")
                self.send_header("X-Content-Type-Options","nosniff")
                self.end_headers()
                self.wfile.write(body)

            def permitted(self):
                return (self.headers.get("Host") == "127.0.0.1:{}".format(session.server.server_port)
                        and not self.headers.get("Origin") and urllib.parse.urlsplit(self.path).path.startswith(prefix+"/"))

            def do_GET(self):
                if not self.permitted():
                    self.send_payload({},403); return
                path = urllib.parse.urlsplit(self.path).path[len(prefix):]
                session.requests.append(path)
                if path == "/":
                    self.send_payload(session.metadata)
                elif path == "/textures/"+session.digest:
                    self.send_payload(session.png,content_type="image/png")
                elif path.startswith("/sessionserver/session/minecraft/profile/") and path.rsplit("/",1)[1].replace("-","") == account["id"]:
                    self.send_payload(session.profile)
                elif path == "/api/users/profiles/minecraft/"+account["name"]:
                    self.send_payload({"id":account["id"],"name":account["name"]})
                elif path == "/minecraftservices/publickeys":
                    self.send_payload({"profilePropertyKeys":[{"publicKey":base64.b64encode(public_der).decode("ascii")}],"playerCertificateKeys":[]})
                else:
                    self.send_payload({},404)

            def do_POST(self):
                if not self.permitted():
                    self.send_payload({},403); return
                path = urllib.parse.urlsplit(self.path).path[len(prefix):]
                if path == "/api/profiles/minecraft":
                    try:
                        length = int(self.headers.get("Content-Length","0"))
                        if not 0 < length <= 4096: raise ValueError()
                        names = json.loads(self.rfile.read(length))
                        if not isinstance(names,list): raise ValueError()
                        self.send_payload([{"id":account["id"],"name":account["name"]}] if account["name"].lower() in [str(n).lower() for n in names] else [])
                    except (ValueError,TypeError): self.send_payload({},400)
                else:
                    # This endpoint supplies textures, never online sessions.
                    self.send_payload({"error":"ForbiddenOperationException","errorMessage":"Offline skin session"},403)

        self.server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.server.daemon_threads = True
        self.url = "http://127.0.0.1:{}{}/".format(self.server.server_port,prefix)
        self.digest = hashlib.sha256(png).hexdigest()
        texture = {"timestamp":int(time.time()*1000),"profileId":account["id"],"profileName":account["name"],
                   "textures":{"SKIN":{"url":self.url+"textures/"+self.digest,"metadata":{"model":account.get("skin_model","default")}}}}
        value = base64.b64encode(json.dumps(texture).encode("utf-8")).decode("ascii")
        signature = base64.b64encode(key.sign(value.encode("ascii"),padding.PKCS1v15(),hashes.SHA1())).decode("ascii")
        self.profile = {"id":account["id"],"name":account["name"],"properties":[{"name":"textures","value":value,"signature":signature}]}
        self.arguments = ["-javaagent:{}={}".format(agent,self.url),"-Dauthlibinjector.yggdrasil.prefetched="+base64.b64encode(json.dumps(self.metadata).encode()).decode("ascii"),"-Dauthlibinjector.side=client"]
        self.thread = threading.Thread(target=self.server.serve_forever,kwargs={"poll_interval":.1},daemon=True,name="StarMoon-offline-skin")
        self.thread.start()

    def close(self):
        if not self.closed:
            self.closed = True
            self.server.shutdown()
            self.server.server_close()
