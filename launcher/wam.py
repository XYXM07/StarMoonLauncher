"""Windows Web Account Manager via Microsoft's supported MSAL broker."""
import base64
import json
import os
import threading
from pathlib import Path

from .core import Cancelled, LauncherError, atomic_json, protect, read_json, unprotect
from . import __version__

AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["XboxLive.signin"]


class WamBroker:
    def __init__(self, root):
        self.root = Path(root)
        self.cache_path = self.root / "wam-cache.json"
        self.lock = threading.RLock()
        self.clients = {}

    @staticmethod
    def require_broker(ui=None, **_):
        # MSAL documents a browser fallback. Stop it before it opens any browser.
        if ui != "broker":
            raise LauncherError("Windows 账户管理器不可用")

    def _client(self, client_id):
        if os.name != "nt":
            raise LauncherError("WAM 需要 Windows 10 或更高版本")
        if client_id not in self.clients:
            try:
                import msal
                import pymsalruntime  # Ensure the bundled native broker can load.
            except (ImportError, OSError, RuntimeError) as exc:
                raise LauncherError("无法加载 Windows 登录组件") from exc
            cache = msal.SerializableTokenCache()
            saved = read_json(self.cache_path, {})
            if saved.get(client_id):
                try:
                    cache.deserialize(unprotect(saved[client_id]).decode("utf-8"))
                except Exception:
                    # A cache moved from another Windows user cannot be decrypted.
                    cache = msal.SerializableTokenCache()
            client = msal.PublicClientApplication(
                client_id, authority=AUTHORITY, token_cache=cache,
                enable_broker_on_windows=True, enable_pii_log=False,
                app_name="StarMoon", app_version=__version__, timeout=25)
            self.clients[client_id] = (client, cache)
        return self.clients[client_id]

    def _save(self, client_id, cache):
        if cache.has_state_changed:
            saved = read_json(self.cache_path, {})
            saved[client_id] = protect(cache.serialize().encode("utf-8"))
            atomic_json(self.cache_path, saved)

    @staticmethod
    def _check(result):
        if not result or not result.get("access_token"):
            error = (result or {}).get("error", "")
            description = (result or {}).get("error_description", "").lower()
            if error in ("user_cancelled", "authentication_canceled") or any(s in description for s in ("user canceled", "user cancelled", "status_usercanceled")):
                raise Cancelled("已取消登录")
            raise LauncherError("Windows 登录失败，请检查网络或联系启动器发布者" + ("（" + error + "）" if error else ""))
        return result

    def interactive(self, client_id, parent_window_handle):
        if not isinstance(parent_window_handle, int) or parent_window_handle <= 0:
            raise LauncherError("登录窗口句柄无效")
        with self.lock:
            client, cache = self._client(client_id)
            result = client.acquire_token_interactive(
                SCOPES, parent_window_handle=parent_window_handle,
                prompt="select_account", on_before_launching_ui=self.require_broker)
            self._check(result)
            self._save(client_id, cache)
            # MSAL removes broker-private _account_id from public results. Match
            # the client_info home ID used by its token cache instead.
            home_id = None
            info = result.get("client_info", "")
            if info:
                try:
                    info = json.loads(base64.urlsafe_b64decode(info + "=" * (-len(info) % 4)))
                    if info.get("uid") and info.get("utid"):
                        home_id = "{}.{}".format(info["uid"], info["utid"])
                except (TypeError, ValueError):
                    pass
            claims = result.get("id_token_claims", {})
            local_id = claims.get("oid") or claims.get("sub")
            accounts = client.get_accounts()
            selected = next((a for a in accounts if (
                a.get("home_account_id") == home_id if home_id
                else local_id and a.get("local_account_id") == local_id)), None)
            if not selected or not selected.get("home_account_id"):
                raise LauncherError("无法保存所选 Windows 账户，请重新登录")
            return result, selected["home_account_id"]

    def silent(self, client_id, home_account_id):
        with self.lock:
            client, cache = self._client(client_id)
            account = next((a for a in client.get_accounts() if a.get("home_account_id") == home_account_id), None)
            if not account:
                raise LauncherError("请使用 Windows 账户重新登录")
            # Broker accounts have no app-managed Microsoft refresh token.
            result = client.acquire_token_silent(SCOPES, account=account)
            self._check(result)
            self._save(client_id, cache)
            return result
