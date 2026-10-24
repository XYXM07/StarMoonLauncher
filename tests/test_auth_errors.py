"""Authentication diagnostics use synthetic tokens and responses only."""
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from launcher.accounts import Accounts
from launcher.core import LauncherError, Store


class AuthErrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.accounts = Accounts(Store(self.root))

    def tearDown(self):
        self.temp.cleanup()

    def responses(self):
        return [{"Token": "xbox-secret"},
                {"Token": "xsts-secret", "DisplayClaims": {"xui": [{"uhs": "hash"}]}},
                {"access_token": "minecraft-secret"}, {"items": [{"name": "game_minecraft"}]},
                {"id": "a" * 32, "name": "Player"}]

    def failure(self, index, status, body):
        responses = self.responses()[:index]
        error = urllib.error.HTTPError("https://example.invalid", status, "failure", {}, io.BytesIO(body))
        with patch("launcher.accounts.request_json", side_effect=responses + [error]), patch.object(self.accounts, "_credentials") as save:
            with self.assertRaises(LauncherError) as raised:
                self.accounts._exchange({"access_token": "ms-secret"}, "client")
            save.assert_not_called()
        return str(raised.exception)

    def test_each_http_failure_keeps_its_actual_stage_and_never_saves_credentials(self):
        stages = ["Xbox Live 登录", "Xbox XSTS 授权", "Minecraft 登录", "Java 版授权检查", "Java 版玩家档案"]
        for index, stage in enumerate(stages):
            with self.subTest(stage=stage):
                message = self.failure(index, 401, b'{"XErr":2148916238,"Token":"response-secret"}')
                self.assertIn(stage, message)
                self.assertIn("HTTP 401", message)
                self.assertIn("XErr=2148916238", message)
                record = json.loads((self.root / "auth-last-error.json").read_text(encoding="utf-8"))
                self.assertEqual(record["stage"], stage)
                self.assertEqual(record["http_status"], 401)
                self.assertIn("https://", record["endpoint"])
                self.assertNotIn("secret", message + json.dumps(record))

    def test_invalid_app_registration_is_identified_without_logging_raw_response(self):
        message = self.failure(2, 403, b'{"error":"ForbiddenOperationException","errorMessage":"Invalid app registration, see https://aka.ms/AppRegInfo","access_token":"hidden-secret","email":"private@example.invalid"}')
        self.assertIn("Minecraft 登录", message)
        self.assertIn("Invalid app registration", message)
        self.assertIn("审批", message)
        logged = (self.root / "auth-last-error.json").read_text(encoding="utf-8")
        self.assertNotIn("hidden-secret", message + logged)
        self.assertNotIn("private@example.invalid", message + logged)

    def test_generic_forbidden_does_not_claim_app_registration_was_rejected(self):
        message = self.failure(1, 403, b'{"error":"Forbidden","message":"unknown"}')
        self.assertIn("Xbox XSTS", message)
        self.assertIn("Forbidden", message)
        self.assertNotIn("审批", message)

    def test_missing_java_profile_is_separate_from_application_approval(self):
        message = self.failure(4, 404, b'{"error":"NotFound"}')
        self.assertIn("Java 版玩家档案", message)
        self.assertIn("创建玩家名", message)
        self.assertNotIn("审批", message)

    def test_unparseable_and_oversized_error_bodies_are_bounded_and_not_echoed(self):
        for body in (b'<html>private-secret</html>', b'{"private":"' + b'x' * 10000 + b'"}', b'{"error":{"access_token":"private-secret"}}'):
            with self.subTest(size=len(body)):
                message = self.failure(0, 503, body)
                self.assertIn("HTTP 503", message)
                self.assertLess(len(message), 500)
                self.assertNotIn("private", message + (self.root / "auth-last-error.json").read_text(encoding="utf-8"))

    def test_logging_failure_does_not_hide_the_authentication_error(self):
        with patch("launcher.accounts.atomic_json", side_effect=PermissionError("cannot write")):
            message = self.failure(2, 403, b'{"errorMessage":"Invalid app registration"}')
        self.assertIn("HTTP 403", message)
        self.assertIn("Invalid app registration", message)

    def test_successful_exchange_still_preserves_all_five_authentication_requests(self):
        with patch("launcher.accounts.request_json", side_effect=self.responses()) as requests, patch.object(self.accounts, "_credentials") as save:
            account = self.accounts._exchange({"access_token": "ms-secret"}, "client")
        self.assertEqual(requests.call_count, 5)
        self.assertEqual(account["name"], "Player")
        save.assert_called_once()
        self.assertFalse((self.root / "auth-last-error.json").exists())


if __name__ == "__main__":
    unittest.main()
