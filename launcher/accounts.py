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

    def _auth_request(self, stage, url, data=None, headers=None):
        try:
            return request_json(url, data, headers=headers)
        except urllib.error.HTTPError as exc:
            # Only retain bounded diagnostic codes, never response bodies,
            # requests, account details or any of the authentication tokens.
            status, code, registration = exc.code, "", False
            try:
                raw = exc.read(8192).decode("utf-8", errors="replace")
                registration = "invalid app registration" in raw.lower()
                response = json.loads(raw)
                if isinstance(response, dict):
                    xerr = response.get("XErr")
                    if type(xerr) is int and 0 <= xerr <= 0xffffffff:
                        code = "XErr=" + str(xerr)
                    else:
                        error = response.get("error")
                        if error in ("invalid_request", "invalid_grant", "invalid_client", "access_denied", "unauthorized_client",
                                     "Unauthorized", "Forbidden", "NotFound", "ForbiddenOperationException"):
                            code = error
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            finally:
                exc.close()
            if stage == "Minecraft 登录" and status == 403 and registration:
                code = "Invalid app registration"
                suggestion = "Minecraft 服务拒绝此应用注册，请检查启动器应用 ID 的 Minecraft API 访问审批。"
            elif stage == "Java 版玩家档案" and status == 404:
                suggestion = "未找到 Java 版玩家档案，请先在官方启动器确认游戏授权并创建玩家名。"
            elif status == 401:
                suggestion = "服务拒绝当前凭据，请重新登录后重试，并保留此错误码。"
            elif status == 403:
                suggestion = "服务拒绝此请求，请结合失败接口和错误码检查账号或应用权限。"
            else:
                suggestion = "认证服务请求失败，请保留此错误码后重试。"
            message = "{}失败（HTTP {}{}）\n{}\n接口：{}".format(stage, status, " · " + code if code else "", suggestion, url)
            try:
                atomic_json(self.store.root / "auth-last-error.json", {
                    "time": time.time(), "stage": stage, "endpoint": url,
                    "http_status": status, "error_code": code, "suggestion": suggestion})
            except OSError:
                pass
            raise LauncherError(message) from None

    def _exchange(self, token, client_id, auth_metadata=None):
        xbox = self._auth_request("Xbox Live 登录", "https://user.auth.xboxlive.com/user/authenticate", {
            "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": "d=" + token["access_token"]},
            "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"})
        xsts = self._auth_request("Xbox XSTS 授权", "https://xsts.auth.xboxlive.com/xsts/authorize", {
            "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbox["Token"]]},
            "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"})
        uhs = xsts["DisplayClaims"]["xui"][0]["uhs"]
        minecraft = self._auth_request("Minecraft 登录", "https://api.minecraftservices.com/authentication/login_with_xbox", {
            "identityToken": "XBL3.0 x={};{}".format(uhs, xsts["Token"])})
        headers = {"Authorization": "Bearer " + minecraft["access_token"]}
        entitlements = self._auth_request("Java 版授权检查", "https://api.minecraftservices.com/entitlements/mcstore", headers=headers)
        if not entitlements.get("items"):
            raise LauncherError("该微软账号没有可用的 Minecraft Java 版授权")
        profile = self._auth_request("Java 版玩家档案", "https://api.minecraftservices.com/minecraft/profile", headers=headers)
        account = {"id": profile["id"], "name": profile["name"], "type": "microsoft"}
        from .player_skins import PlayerSkins
        skins = profile.get("skins")
        for skin in (skins[:16] if isinstance(skins, list) else []):
            if isinstance(skin, dict) and skin.get("state") == "ACTIVE":
                try:
                    account["skin_url"] = PlayerSkins.texture_url(skin.get("url"))
                except (LauncherError, ValueError, TypeError):
                    pass  # A missing cosmetic texture must not prevent sign-in.
                break
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
