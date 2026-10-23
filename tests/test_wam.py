"""Broker integration contracts; never opens an actual account picker in tests."""
import base64
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".build-env"))
from launcher.accounts import Accounts
from launcher.core import Cancelled, LauncherError, Store
from launcher.wam import AUTHORITY, SCOPES, WamBroker

CLIENT_ID = "11111111-2222-3333-4444-555555555555"


class WamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.broker = WamBroker(self.root)
        self.client = MagicMock()
        self.cache = MagicMock()
        self.cache.has_state_changed = False
        self.broker.clients[CLIENT_ID] = (self.client, self.cache)

    def tearDown(self):
        self.temp.cleanup()

    def test_native_broker_options_and_encrypted_cache_roundtrip(self):
        msal = MagicMock()
        cache = msal.SerializableTokenCache.return_value
        cache.serialize.return_value = "private serialized cache"
        cache.has_state_changed = True
        broker = WamBroker(self.root)
        with patch.dict(sys.modules, {"msal": msal, "pymsalruntime": MagicMock()}), patch("launcher.wam.os.name", "nt"), patch("launcher.wam.protect", return_value="encrypted") as encrypt, patch("launcher.wam.unprotect", return_value=b"restored cache"):
            broker._client(CLIENT_ID)
            options = msal.PublicClientApplication.call_args.kwargs
            self.assertTrue(options["enable_broker_on_windows"])
            self.assertEqual(options["authority"], AUTHORITY)
            self.assertFalse(options["enable_pii_log"])
            broker._save(CLIENT_ID, cache)
            encrypt.assert_called_once_with(b"private serialized cache")
            self.assertNotIn("private", broker.cache_path.read_text())
            WamBroker(self.root)._client(CLIENT_ID)
            cache.deserialize.assert_called_once_with("restored cache")

    def test_interactive_binds_hwnd_and_matches_chosen_broker_account(self):
        import msal
        # Exercise the real MSAL broker response -> cache -> public result path.
        # MSA's broker account ID can differ from the pairwise ID-token subject.
        app = object.__new__(msal.PublicClientApplication)
        app.client_id = CLIENT_ID
        app.authority = MagicMock(instance="login.microsoftonline.com", token_endpoint=AUTHORITY + "/oauth2/v2.0/token")
        app.token_cache = msal.SerializableTokenCache()
        client_info = base64.urlsafe_b64encode(b'{"uid":"second","utid":"realm"}').decode().rstrip("=")
        raw = {"access_token": "secret", "_account_id": "broker-second", "client_info": client_info, "id_token_claims": {"sub": "different-sub", "preferred_username": "example@example.invalid"}}
        public_result = app._process_broker_response(raw, SCOPES, {})
        self.assertNotIn("_account_id", public_result)
        actual_accounts = list(app.token_cache.search(msal.TokenCache.CredentialType.ACCOUNT))
        self.assertEqual(actual_accounts[0]["local_account_id"], "broker-second")
        self.client.acquire_token_interactive.return_value = public_result
        self.client.get_accounts.return_value = [{"local_account_id": "broker-first", "home_account_id": "first.realm"}] + actual_accounts
        result, home_id = self.broker.interactive(CLIENT_ID, 1234)
        self.assertEqual(home_id, "second.realm")
        args, options = self.client.acquire_token_interactive.call_args
        self.assertEqual(args, (SCOPES,))
        self.assertEqual(options["parent_window_handle"], 1234)
        self.assertEqual(options["prompt"], "select_account")
        options["on_before_launching_ui"](ui="broker")

    def test_browser_fallback_is_stopped_before_launch(self):
        with self.assertRaises(LauncherError):
            WamBroker.require_broker(ui="browser")

    def test_ambiguous_account_is_not_silently_selected(self):
        self.client.acquire_token_interactive.return_value = {"access_token": "secret"}
        self.client.get_accounts.return_value = [{"home_account_id": "one"}, {"home_account_id": "two"}]
        with self.assertRaises(LauncherError):
            self.broker.interactive(CLIENT_ID, 1234)

    def test_silent_refresh_uses_exact_saved_account(self):
        account = {"home_account_id": "saved"}
        self.client.get_accounts.return_value = [{"home_account_id": "other"}, account]
        self.client.acquire_token_silent.return_value = {"access_token": "renewed"}
        self.assertEqual(self.broker.silent(CLIENT_ID, "saved")["access_token"], "renewed")
        self.client.acquire_token_silent.assert_called_once_with(SCOPES, account=account)
        with self.assertRaises(LauncherError):
            self.broker.silent(CLIENT_ID, "missing")

    def test_native_cancel_and_invalid_hwnd(self):
        with self.assertRaises(Cancelled):
            WamBroker._check({"error": "broker_error", "error_description": "Status: Response_Status.Status_UserCanceled"})
        with self.assertRaises(LauncherError):
            self.broker.interactive(CLIENT_ID, 0)

    def test_account_session_exposes_no_tokens_and_keeps_wam_metadata(self):
        accounts = Accounts(Store(self.root))
        accounts.store.save({"client_id": CLIENT_ID})
        profile = {"id": "a" * 32, "name": "WamPlayer", "type": "microsoft"}
        token = {"access_token": "private-token"}
        with patch.object(accounts.broker, "interactive", return_value=(token, "home-id")) as interactive, patch.object(accounts, "_exchange", return_value=profile) as exchange:
            session = accounts.begin(1234)
            deadline = time.time() + 3
            while accounts.status(session["id"])["status"] == "pending" and time.time() < deadline:
                time.sleep(.01)
            status = accounts.status(session["id"])
            self.assertEqual(status["status"], "completed")
            self.assertNotIn("private-token", json.dumps(status))
            self.assertEqual(session["method"], "wam")
            interactive.assert_called_once_with(CLIENT_ID, 1234)
            exchange.assert_called_once_with(token, CLIENT_ID, {"auth_backend": "wam", "broker_home_account_id": "home-id"})

    def test_cancelled_session_does_not_add_account(self):
        accounts = Accounts(Store(self.root))
        accounts.store.save({"client_id": CLIENT_ID})
        gate = threading.Event()
        def interactive(*_):
            gate.wait(3)
            return {"access_token": "private"}, "home-id"
        with patch.object(accounts.broker, "interactive", side_effect=interactive), patch.object(accounts, "_exchange") as exchange:
            session = accounts.begin(1234)
            with self.assertRaises(LauncherError):
                accounts.begin(1234)
            accounts.status(session["id"], cancel=True)
            gate.set()
            deadline = time.time() + 3
            while accounts.status(session["id"])["status"] == "pending" and time.time() < deadline:
                time.sleep(.01)
            self.assertEqual(accounts.status(session["id"])["status"], "cancelled")
            self.assertEqual(accounts.store.config()["accounts"], [])
            exchange.assert_not_called()


if __name__ == "__main__":
    unittest.main()
