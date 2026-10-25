"""Read mod artwork as bounded data, with cache and provider-cover fallbacks."""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".build-env"))
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage
from launcher.core import LauncherError
from launcher.service import Application


def png(color="blue"):
    image = QImage(128, 128, QImage.Format_ARGB32)
    image.fill(QColor(color)); image.setText("comment", "strip metadata")
    buffer = QBuffer(); buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


class ModPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Application(self.root)
        self.app.store.create_minecraft()
        self.instance = self.app.game.create("Fixture", "1.21.1", "fabric", "0.16.0")
        self.folder = self.app.store.game_dir(self.instance["id"]) / "mods"
        self.folder.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def jar(self, metadata_name, metadata, filename="example.jar", images=None):
        path = self.folder / filename
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(metadata_name, metadata if isinstance(metadata, str) else json.dumps(metadata))
            for name, payload in (images or {"logo.png": png()}).items():
                archive.writestr(name, payload)
        return path

    def test_fabric_icon_map_and_description_are_loaded_without_network(self):
        self.jar("fabric.mod.json", {"id": "example", "name": "Example Mod", "description": "A fixture description",
                                    "icon": {"16": "small.png", "128": "logo.png"}})
        with patch.object(self.app.net, "json") as network:
            item = self.app.content.list_local(self.instance["id"])[0]
            network.assert_not_called()
        self.assertEqual(item["mod_name"], "Example Mod")
        self.assertEqual(item["description"], "A fixture description")
        self.assertFalse(QImage(item["image_path"]).isNull())
        self.assertEqual(QImage(item["image_path"]).textKeys(), [])

    def test_quilt_embedded_logo(self):
        self.jar("quilt.mod.json", {"quilt_loader": {"metadata": {"name": "Quilt Example", "icon": "logo.png"}}})
        item = self.app.content.list_local(self.instance["id"])[0]
        self.assertEqual(item["mod_name"], "Quilt Example")
        self.assertTrue(Path(item["image_path"]).is_file())

    def test_forge_and_neoforge_logo_file(self):
        for name in ("META-INF/mods.toml", "META-INF/neoforge.mods.toml"):
            path = self.jar(name, '[[mods]]\nmodId="example"\ndisplayName="Forge Example"\nlogoFile="logo.png"\ndescription="Fixture"')
            item = self.app.content.list_local(self.instance["id"])[0]
            self.assertEqual(item["mod_name"], "Forge Example")
            self.assertTrue(item["image_path"])
            path.unlink()

    def test_legacy_logo_and_disabled_mod_keep_same_cached_picture(self):
        path = self.jar("mcmod.info", [{"modid": "example", "name": "Legacy Example", "logoFile": "logo.png"}])
        first = self.app.content.list_local(self.instance["id"])[0]
        path.rename(path.with_name(path.name + ".disabled"))
        second = self.app.content.list_local(self.instance["id"])[0]
        self.assertFalse(second["enabled"])
        self.assertEqual(first["image_path"], second["image_path"])

    def test_invalid_mods_and_unsafe_icon_paths_do_not_break_management(self):
        self.jar("fabric.mod.json", {"icon": "../../outside.png"}, images={"../../outside.png": png()})
        (self.folder / "broken.jar").write_bytes(b"not a jar")
        items = self.app.content.list_local(self.instance["id"])
        self.assertEqual(len(items), 2)
        self.assertTrue(all(not item["image_path"] for item in items))
        self.assertFalse((self.root / "outside.png").exists())

    def test_unknown_metadata_can_use_root_logo_and_bad_picture_uses_placeholder(self):
        path = self.jar("unknown.json", {}, images={"logo.png": png("red")})
        self.assertTrue(self.app.content.previews.local(path)["image_path"])
        path.unlink()
        path = self.jar("unknown.json", {}, images={"logo.png": b"not image"})
        self.assertFalse(self.app.content.previews.local(path)["image_path"])

    def test_provider_cover_metadata_is_cached_and_missing_key_is_quiet(self):
        with patch.object(self.app.net, "json", return_value={"icon_url": "https://cdn.modrinth.com/icon.png"}) as get:
            for _ in range(2):
                self.assertEqual(self.app.content.previews.project_image("modrinth", "example"), "https://cdn.modrinth.com/icon.png")
            get.assert_called_once()
        self.assertEqual(self.app.content.previews.project_image("curseforge", "1"), "")
        self.assertEqual(self.app.content.previews.project_image("github", "owner/repo"), "")


if __name__ == "__main__":
    unittest.main()
