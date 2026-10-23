import base64
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/".build-env"))
from PySide6.QtGui import QColor, QImage
from launcher.core import LauncherError
from launcher.service import Application


class SkinTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.app=Application(self.root)
        self.account=self.app.accounts.offline("SkinTester")
        self.png=self.root/"source.png"
        image=QImage(64,64,QImage.Format_ARGB32)
        image.fill(QColor("#308aff"))
        image.setText("description","source metadata")
        image.save(str(self.png))
        self.sessions=[]

    def tearDown(self):
        for session in self.sessions: session.close()
        self.temp.cleanup()

    def test_import_is_owned_sanitized_and_survives_relogin(self):
        other=self.app.accounts.offline("OtherPlayer")
        updated=self.app.skins.save(self.account["id"],self.png,"slim")
        self.assertEqual(self.app.store.config()["selected_account"],other["id"])
        saved=self.app.skins.path(updated)
        self.assertNotEqual(saved,self.png)
        self.assertEqual(QImage(str(saved)).textKeys(),[])
        self.png.unlink()
        self.assertTrue(saved.is_file())
        again=self.app.accounts.offline("SkinTester")
        self.assertEqual(again["id"],self.account["id"])
        self.assertEqual(again["skin_path"],updated["skin_path"])
        self.assertEqual(again["skin_model"],"slim")
        self.app.skins.reset(again["id"])
        self.assertNotIn("skin_path",self.app.accounts.for_launch(again["id"]))

    def test_invalid_images_and_non_offline_accounts_are_rejected(self):
        image=QImage(128,128,QImage.Format_RGB32)
        image.fill(0)
        image.save(str(self.png))
        with self.assertRaises(LauncherError): self.app.skins.save(self.account["id"],self.png)
        self.app.store.save({"accounts":[{"id":"f"*32,"name":"Online","type":"microsoft"}]})
        with self.assertRaises(LauncherError): self.app.skins.save("f"*32,self.png)
        self.assertIsNone(self.app.skins.prepare({"type":"microsoft","skin_path":"ignored"}))

    def test_local_profile_is_signed_and_texture_and_model_are_served(self):
        self.app.skins.save(self.account["id"],self.png,"slim")
        account=self.app.accounts.for_launch(self.account["id"])
        session=self.app.skins.prepare(account)
        self.sessions.append(session)
        with urllib.request.urlopen(session.url+"sessionserver/session/minecraft/profile/"+account["id"]+"?unsigned=false") as response:
            profile=json.load(response)
        prop=profile["properties"][0]
        texture=json.loads(base64.b64decode(prop["value"]))
        self.assertEqual(texture["profileId"],account["id"])
        self.assertEqual(texture["textures"]["SKIN"]["metadata"]["model"],"slim")
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        key=serialization.load_pem_public_key(session.metadata["signaturePublickey"].encode())
        key.verify(base64.b64decode(prop["signature"]),prop["value"].encode(),padding.PKCS1v15(),hashes.SHA1())
        with urllib.request.urlopen(texture["textures"]["SKIN"]["url"]) as response:
            self.assertEqual(response.headers["Content-Type"],"image/png")
            self.assertEqual(response.read(),session.png)
        request=urllib.request.Request(session.url,headers={"Origin":"https://example.com"})
        with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(request)
        self.assertEqual(error.exception.code,403)
        request=urllib.request.Request(session.url+"sessionserver/session/minecraft/join",data=b"{}")
        with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(request)
        self.assertEqual(error.exception.code,403)
        self.assertTrue(any(arg.startswith("-javaagent:") for arg in session.arguments))
