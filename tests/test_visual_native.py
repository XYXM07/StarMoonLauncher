"""Focused avatar and installed-mod artwork checks with synthetic images only."""
import io
import json
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import test_native as native
from test_mod_previews import png
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from launcher.core import atomic_json
from launcher.images import ProjectPreview
from launcher.skin_ui import AccountAvatar
from launcher.widgets import Button


class VisualNativeTests(unittest.TestCase):
    def setUp(self):
        native.NativeTests.setUp(self)
        self.window.timer.start(30)

    def tearDown(self):
        native.NativeTests.tearDown(self)

    def skin(self, name, color, overlay=False):
        image = QImage(64, 64, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        for x in range(8, 16):
            for y in range(8, 16):
                image.setPixelColor(x, y, QColor(color))
        if overlay:
            image.setPixelColor(40, 8, QColor("lime"))
        path = self.root / (name + ".png")
        image.save(str(path))
        return path

    def test_microsoft_account_avatar_uses_skin_face_overlay_and_account_card(self):
        path = self.skin("official", "red", True)
        account = {"id": "123456781234123412341234567890ab", "name": "FixturePlayer", "type": "microsoft"}
        with patch.object(self.service.player_skins, "account_skin", return_value=str(path)) as get:
            self.service.accounts._save_account(account)
            self.window.data = self.service.state()
            self.window.select_account(account["id"])
            native.wait_for(lambda: self.window.avatar_label.pixmap().toImage().pixelColor(28, 28) == QColor("red"))
            self.assertEqual(self.window.avatar_label.pixmap().toImage().pixelColor(0, 0), QColor("lime"))
            self.window.navigate("accounts", animated=False)
            avatar = self.window.page.findChild(AccountAvatar)
            native.wait_for(lambda: avatar.pixmap().toImage().pixelColor(22, 22) == QColor("red"))
            get.assert_called_once()

    def test_late_account_image_cannot_replace_newer_selection(self):
        red, blue = self.skin("red", "red"), self.skin("blue", "blue")
        gate = threading.Event()
        def fetch(account):
            if account["id"] == "a" * 32:
                gate.wait(3)
                return str(red)
            return str(blue)
        avatar = self.window.avatar_label
        with patch.object(self.service.player_skins, "account_skin", side_effect=fetch):
            self.window.bind_account_avatar(avatar, {"id": "a" * 32, "type": "microsoft"})
            self.window.bind_account_avatar(avatar, {"id": "b" * 32, "type": "microsoft"})
            native.wait_for(lambda: avatar.pixmap().toImage().pixelColor(28, 28) == QColor("blue"))
            gate.set()
            native.wait_for(lambda: not self.window.images.jobs)
            self.assertEqual(avatar.pixmap().toImage().pixelColor(28, 28), QColor("blue"))

    def setup_mod(self, embedded=True):
        instance = self.service.game.create("Mods", "1.21.1", "fabric", "0.16.0")
        folder = self.service.store.game_dir(instance["id"]) / "mods"
        folder.mkdir()
        path = folder / "example.jar"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("fabric.mod.json", json.dumps({"id": "example", "name": "Example Mod", "description": "A preview fixture", "icon": "logo.png"}))
            if embedded:
                archive.writestr("logo.png", png("#386fff"))
        self.window.refresh()
        return instance, path

    def open_mods(self, instance):
        self.window.open_instance(instance["id"])
        self.window.set_instance_category("mods")
        native.wait_for(lambda: bool(self.window.instance_panel.findChildren(ProjectPreview, "local-mod-preview")))
        return self.window.instance_panel.findChild(ProjectPreview, "local-mod-preview")

    def test_embedded_mod_picture_and_description_are_visible_when_disabled_too(self):
        instance, path = self.setup_mod()
        preview = self.open_mods(instance)
        native.wait_for(lambda: preview.property("loaded"))
        self.assertEqual(preview.image.pixelColor(20, 20), QColor("#386fff"))
        self.service.content.toggle(instance["id"], path.name, "mod")
        self.window.instance_panel.refresh_content()
        native.wait_for(lambda: any(item["filename"].endswith(".disabled") for item in self.window.instance_panel.local_items))
        native.wait_for(lambda: any(p.isVisible() for p in self.window.instance_panel.findChildren(ProjectPreview, "local-mod-preview")))
        preview = next(p for p in self.window.instance_panel.findChildren(ProjectPreview, "local-mod-preview") if p.isVisible())
        native.wait_for(lambda: preview.property("loaded"))
        self.assertTrue(any(button.text() == "启用" for button in self.window.instance_panel.findChildren(Button)))

    def test_mod_without_embedded_art_uses_provider_cover_asynchronously(self):
        instance, path = self.setup_mod(False)
        atomic_json(self.service.store.instance_dir(instance["id"]) / "content.json", [{"filename": path.name, "provider": "modrinth", "project_id": "example", "kind": "mod"}])
        url = "https://cdn.modrinth.com/fixture/mod.png"
        class Response(io.BytesIO):
            def geturl(self):
                return url
        with patch.object(self.service.content.previews, "project_image", return_value=url), \
             patch("launcher.images.urllib.request.urlopen", side_effect=lambda *a, **k: Response(png("orange"))):
            preview = self.open_mods(instance)
            native.wait_for(lambda: preview.property("loaded"))
            self.assertEqual(preview.image.pixelColor(20, 20), QColor("orange"))

    def test_missing_mod_art_keeps_placeholder_and_management_buttons(self):
        instance, _ = self.setup_mod(False)
        preview = self.open_mods(instance)
        self.assertFalse(preview.property("loaded"))
        self.assertTrue(any(button.text() == "禁用" and button.isEnabled() for button in self.window.instance_panel.findChildren(Button)))


if __name__ == "__main__":
    unittest.main()
