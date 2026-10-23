import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import uuid

from .core import Cancelled, LauncherError, atomic_json, protect, read_json, request_json, unprotect

from .wam import WamBroker


class Accounts:
    def __init__(self, store):
        self.store = store
        self.sessions = {}
        self.lock = threading.RLock()
        self.path = store.root / "credentials.json"
        self.broker = WamBroker(store.root)

    def offline(self, name):
        if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", name or ""):
            raise LauncherError("玩家名需要 3–16 位英文、数字或下划线")
        digest = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest())
        digest[6] = (digest[6] & 0x0f) | 0x30
        digest[8] = (digest[8] & 0x3f) | 0x80
        account = {"id": uuid.UUID(bytes=bytes(digest)).hex, "name": name, "type": "offline"}
        existing = next((a for a in self.store.config()["accounts"] if a["id"] == account["id"] and a["type"] == "offline"), {})
        account.update({key:existing[key] for key in ("skin_path","skin_model") if key in existing})
        self._save_account(account)
        return account

    def _save_account(self, account):
        with self.lock:
            accounts = [a for a in self.store.config()["accounts"] if a["id"] != account["id"]]
            accounts.append(account)
            self.store.save({"accounts": accounts, "selected_account": account["id"]})

    def delete(self, account_id):
        with self.lock:
            config = self.store.config()
            accounts = [a for a in config["accounts"] if a["id"] != account_id]
            config.update(accounts=accounts)
            if config["selected_account"] == account_id:
                config["selected_account"] = accounts[0]["id"] if accounts else ""
            credentials = read_json(self.path, {})
            credentials.pop(account_id, None)
            atomic_json(self.path, credentials)
            self.store.save(config)

    def _credentials(self, account_id, value=None):
        with self.lock:
            saved = read_json(self.path, {})
            if value is not None:
                saved[account_id] = protect(json.dumps(value).encode())
                atomic_json(self.path, saved)
                return
            if account_id not in saved:
                raise LauncherError("请重新登录微软账号")
            return json.loads(unprotect(saved[account_id]))

    def begin(self, parent_window_handle=0):
        client_id = (os.environ.get("BLOCKCRAFT_MS_CLIENT_ID", "") or self.store.config()["client_id"]).strip()
        if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", client_id):
            raise LauncherError("此发行版尚未配置正版登录，请使用已配置登录的发行版")
        if not isinstance(parent_window_handle, int) or parent_window_handle <= 0:
            raise LauncherError("登录窗口句柄无效")
        session_id = uuid.uuid4().hex
        session = {"status": "pending", "method": "wam", "cancel": threading.Event(), "error": None}
        with self.lock:
            if any(s["status"] == "pending" for s in self.sessions.values()):
                raise LauncherError("已有登录正在进行")
            self.sessions = {session_id: session}

        def login():
            try:
                token, home_id = self.broker.interactive(client_id, parent_window_handle)
                if session["cancel"].is_set():
                    session["status"] = "cancelled"
                    return
                account = self._exchange(token, client_id, {"auth_backend": "wam", "broker_home_account_id": home_id})
                if session["cancel"].is_set():
                    session["status"] = "cancelled"
                    return
                self._save_account(account)
                session.update(status="completed", account=account)
            except Cancelled:
                session["status"] = "cancelled"
            except Exception as exc:
                session.update(status="cancelled" if session["cancel"].is_set() else "failed", error=str(exc)[:500])

        threading.Thread(target=login, daemon=True).start()
        return {"id": session_id, "method": "wam"}

    def status(self, session_id, cancel=False):
        with self.lock:
            if session_id not in self.sessions:
                raise LauncherError("登录会话不存在")
            session = self.sessions[session_id]
            if cancel:
                session["cancel"].set()
            return {key: value for key, value in session.items() if key != "cancel"}

    def _exchange(self, token, client_id, auth_metadata=None):
        try:
            xbox = request_json("https://user.auth.xboxlive.com/user/authenticate", {
                "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": "d=" + token["access_token"]},
                "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"})
            xsts = request_json("https://xsts.auth.xboxlive.com/xsts/authorize", {
                "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbox["Token"]]},
                "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"})
            uhs = xsts["DisplayClaims"]["xui"][0]["uhs"]
            minecraft = request_json("https://api.minecraftservices.com/authentication/login_with_xbox", {
                "identityToken": "XBL3.0 x={};{}".format(uhs, xsts["Token"])})
            headers = {"Authorization": "Bearer " + minecraft["access_token"]}
            entitlements = request_json("https://api.minecraftservices.com/entitlements/mcstore", headers=headers)
            if not entitlements.get("items"):
                raise LauncherError("该微软账号没有可用的 Minecraft Java 版授权")
            profile = request_json("https://api.minecraftservices.com/minecraft/profile", headers=headers)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404):
                raise LauncherError("Xbox / Minecraft 授权失败。请确认拥有 Java 版、已建立 Xbox 档案，且此 Client ID 获准访问 Minecraft API；儿童账号还需家长许可")
            raise LauncherError("微软游戏服务暂时不可用（HTTP {}）".format(exc.code))
        account = {"id": profile["id"], "name": profile["name"], "type": "microsoft"}
        credentials = {"access_token": minecraft["access_token"],
            "expires": time.time() + minecraft.get("expires_in", 86400) - 120,
            "client_id": client_id, "xuid": xsts["DisplayClaims"]["xui"][0].get("xid", "")}
        credentials.update(auth_metadata or {})
        self._credentials(account["id"], credentials)
        return account

    def for_launch(self, account_id):
        account = next((a for a in self.store.config()["accounts"] if a["id"] == account_id), None)
        if account is None:
            raise LauncherError("请先添加并选择一个账号")
        account = dict(account)
        if account["type"] == "offline":
            account.update(access_token="0", user_type="legacy", client_id="", xuid="")
            return account
        credentials = self._credentials(account_id)
        if credentials["expires"] < time.time():
            if credentials.get("auth_backend") != "wam":
                raise LauncherError("请使用 Windows 账户重新登录")
            try:
                token = self.broker.silent(credentials["client_id"], credentials["broker_home_account_id"])
                refreshed = self._exchange(token, credentials["client_id"], {
                    "auth_backend": "wam", "broker_home_account_id": credentials["broker_home_account_id"]})
                self._save_account(refreshed)
                account.update(refreshed)
                credentials = self._credentials(account["id"])
            except Exception as exc:
                raise LauncherError("Windows 会话刷新失败，请重新登录") from exc
        account.update(access_token=credentials["access_token"], user_type="msa", client_id=credentials["client_id"], xuid=credentials.get("xuid", ""))
        return account
