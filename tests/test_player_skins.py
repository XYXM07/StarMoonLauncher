import base64
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/".build-env"))
from PySide6.QtGui import QColor, QImage
from launcher.core import LauncherError
from launcher.service import Application
from launcher.player_skins import OfficialRedirectsOnly, PlayerSkins


class PlayerSkinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Application(self.root)
        self.uuid = "123456781234123412341234567890ab"
        self.texture = "http://textures.minecraft.net/texture/"+"a"*64
        self.png = self.root/"source.png"
        image = QImage(64,64,QImage.Format_ARGB32)
        image.fill(QColor("#308aff")); image.setText("comment","discard metadata")
        image.save(str(self.png))
        self.profile = {"id":self.uuid,"name":"FixturePlayer","properties":[]}
        self.set_texture()
        self.calls = []

    def tearDown(self): self.temp.cleanup()

    def set_texture(self,model="slim",url=None):
        data = {"textures":{"SKIN":{"url":url or self.texture,"metadata":{"model":model}}}}
        self.profile["properties"] = [{"name":"textures","value":base64.b64encode(json.dumps(data).encode()).decode()}]

    def get(self,url,limit):
        self.calls.append(url)
        if "users/profiles" in url or "/lookup/name/" in url:
            return json.dumps({"id":self.uuid,"name":"FixturePlayer"}).encode()
        if "sessionserver" in url: return json.dumps(self.profile).encode()
        return self.png.read_bytes()

    def test_name_download_normalizes_png_detects_slim_and_does_not_change_accounts(self):
        before = self.app.store.config()["accounts"]
        with patch.object(self.app.player_skins,"get",side_effect=self.get):
            result = self.app.route("POST","/api/skins/player",{},{"player":"FixturePlayer"})
        self.assertEqual(result["model"],"slim")
        self.assertEqual(result["uuid"],self.uuid)
        self.assertEqual(self.calls[-1],self.texture.replace("http:","https:"))
        self.assertEqual(len(self.calls),3)
        self.assertEqual(QImage(result["path"]).textKeys(),[])
        self.assertEqual(self.app.store.config()["accounts"],before)
        self.assertIn("player-skins",result["path"])

    def test_uuid_skips_name_lookup_and_legacy_skin_is_classic(self):
        image = QImage(64,32,QImage.Format_ARGB32)
        image.fill(QColor("red")); image.save(str(self.png))
        with patch.object(self.app.player_skins,"get",side_effect=self.get):
            result = self.app.player_skins.download("12345678-1234-1234-1234-1234567890ab")
        self.assertEqual(result["model"],"default")
        self.assertEqual(len(self.calls),2)

    def test_official_name_lookup_has_official_fallback(self):
        def get(url,limit):
            if "api.mojang.com" in url: raise LauncherError("fixture unavailable")
            return self.get(url,limit)
        with patch.object(self.app.player_skins,"get",side_effect=get):
            result = self.app.player_skins.download("FixturePlayer")
        self.assertEqual(result["uuid"],self.uuid)
        self.assertIn("api.minecraftservices.com",self.calls[0])

    def test_invalid_input_untrusted_texture_and_bad_profiles_are_rejected(self):
        with patch.object(self.app.player_skins,"get",side_effect=self.get) as get:
            for invalid in ("../player","https://example.com","a"*50,"含中文"):
                with self.assertRaises(LauncherError): self.app.player_skins.download(invalid)
            get.assert_not_called()
            for url in ("https://example.com/texture/"+"a"*64,"https://textures.minecraft.net.evil.com/texture/"+"a"*64,
                        "http://127.0.0.1/private","https://textures.minecraft.net/texture/../../secret",
                        "https://textures.minecraft.net/texture/"+"a"*64+"?secret=x"):
                self.set_texture(url=url)
                with self.assertRaises(LauncherError): self.app.player_skins.download("FixturePlayer")
            self.profile["properties"] = []
            with self.assertRaisesRegex(LauncherError,"公开"): self.app.player_skins.download("FixturePlayer")
            self.set_texture(); self.profile["id"] = "f"*32
            with self.assertRaises(LauncherError): self.app.player_skins.download("FixturePlayer")

    def test_export_is_normal_png_cache_only_and_can_apply_to_offline_account(self):
        with patch.object(self.app.player_skins,"get",side_effect=self.get):
            result = self.app.player_skins.download("FixturePlayer")
        exported = self.app.route("POST","/api/skins/export",{},{"path":result["path"],"destination":str(self.root/"downloaded")})
        self.assertEqual(Path(exported["path"]).suffix,".png")
        self.assertEqual(QImage(exported["path"]).size(),QImage(64,64,QImage.Format_ARGB32).size())
        with self.assertRaises(LauncherError): self.app.player_skins.export(self.png,self.root/"forbidden.png")
        account = self.app.accounts.offline("OfflineFixture")
        applied = self.app.skins.save(account["id"],result["path"],result["model"])
        self.assertEqual(applied["skin_model"],"slim")
        self.assertNotEqual(str(self.app.skins.path(applied)),result["path"])

    def test_image_size_format_network_limit_errors_and_redirect_are_handled(self):
        for payload in (b"not PNG",b"x"*(1024*1024+1)):
            with self.assertRaises(LauncherError): PlayerSkins.clean_png(payload)
        image = QImage(128,128,QImage.Format_ARGB32); image.fill(0); image.save(str(self.png))
        with self.assertRaises(LauncherError): PlayerSkins.clean_png(self.png.read_bytes())
        with self.assertRaises(LauncherError): self.app.player_skins.get("https://example.com/skin",1024)
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b"x"*11
        opener = MagicMock(); opener.open.return_value = response
        with patch("launcher.player_skins.urllib.request.build_opener",return_value=opener):
            with self.assertRaises(LauncherError): self.app.player_skins.get("https://api.mojang.com/a",10)
            for code in (404,429,500):
                opener.open.side_effect = urllib.error.HTTPError("https://api.mojang.com/a",code,"fixture",{},None)
                with self.assertRaises(LauncherError): self.app.player_skins.get("https://api.mojang.com/a",10)
        with self.assertRaises(LauncherError): OfficialRedirectsOnly().redirect_request(None,None,302,"",{},"https://example.com")

    def test_signed_in_account_uses_active_texture_and_caches_it(self):
        account = {"id": self.uuid, "type": "microsoft", "skin_url": self.texture, "name": "FixturePlayer"}
        with patch.object(self.app.player_skins, "get", side_effect=self.get):
            first = self.app.player_skins.account_skin(account)
            second = self.app.player_skins.account_skin(account)
        self.assertEqual(first, second)
        self.assertEqual(self.calls, [self.texture.replace("http:", "https:")])
        self.assertEqual(QImage(first).textKeys(), [])

    def test_signed_in_account_without_texture_uses_public_uuid_profile_and_stale_cache_offline(self):
        account = {"id": self.uuid, "type": "microsoft", "name": "FixturePlayer"}
        with patch.object(self.app.player_skins, "get", side_effect=self.get):
            path = self.app.player_skins.account_skin(account)
        self.assertIn("sessionserver", self.calls[0])
        receipt = self.app.player_skins.cache / self.uuid / "avatar.json"
        data = json.loads(receipt.read_text("utf-8")); data["time"] = 0
        receipt.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(self.app.player_skins, "get", side_effect=LauncherError("offline")):
            self.assertEqual(self.app.player_skins.account_skin(account), path)

    def test_login_profile_texture_is_cosmetic_and_does_not_expose_tokens(self):
        for skins in ([{"state": "ACTIVE", "url": self.texture}], [{"state": "ACTIVE", "url": "https://evil.invalid/skin.png"}],
                      [{"state": "ACTIVE", "url": "https://textures.minecraft.net:invalid/texture/a"}], None):
            responses = [{"Token": "xbox"}, {"Token": "xsts", "DisplayClaims": {"xui": [{"uhs": "hash"}]}},
                         {"access_token": "PRIVATE_TOKEN"}, {"items": ["java"]}, {"id": self.uuid, "name": "FixturePlayer", "skins": skins}]
            with patch.object(self.app.accounts, "_auth_request", side_effect=responses), patch.object(self.app.accounts, "_credentials"):
                account = self.app.accounts._exchange({"access_token": "PRIVATE_TOKEN"}, "client")
            self.assertNotIn("PRIVATE_TOKEN", json.dumps(account))
            self.assertEqual(bool(account.get("skin_url")), skins == [{"state": "ACTIVE", "url": self.texture}])
