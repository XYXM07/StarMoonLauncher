"""Targeted pack export, game title, screenshot and world-content backend checks."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.parse
from unittest.mock import patch
import zipfile

from launcher.core import Cancelled, LauncherError, Task
from launcher.game_title import render_title
from launcher.service import Application
from launcher.screenshots import listing, resolve


def zip_bytes(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return stream.getvalue()


class InstanceExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.app = Application(self.root)
        self.app.store.create_minecraft()
        self.instance = self.app.game.create("Export fixture", "1.20.1")
        self.id = self.instance["id"]
        self.game = self.app.store.game_dir(self.id)
        self.world = self.game / "saves/Existing"
        self.world.mkdir(parents=True)
        (self.world / "level.dat").write_bytes(b"keep-world")

    def tearDown(self):
        self.temp.cleanup()

    def task(self):
        return Task("Fixture", None)

    def test_export_selected_groups_formats_and_custom_destination_roundtrip(self):
        for name in ("mods/a.jar", "mods/b.jar.disabled", "config/a.cfg", "screenshots/a.png", "options.txt"):
            path = self.game / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        self.app.store.update_instance(self.id, {"window_title": "{name} - {player}"})
        for format_name, manifest in (("starmoon", "blockcraft.json"), ("modrinth", "modrinth.index.json"), ("curseforge", "manifest.json")):
            result = self.app.content.export_pack(self.id, self.task(), {"format": format_name, "include": ["mods", "config"],
                "include_disabled": False, "destination": str(self.root / "chosen"), "name": "Selected pack", "version": "2.0", "author": "Fixture"})
            path = Path(result["path"])
            self.assertEqual(path.parent, self.root / "chosen")
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(set(archive.namelist()), {manifest, "overrides/mods/a.jar", "overrides/config/a.cfg"})
                metadata = json.loads(archive.read(manifest))
                self.assertEqual(metadata["name"], "Selected pack")
            with patch.object(self.app.game, "install", return_value={}):
                imported = self.app.content.import_pack(path, self.task())
            folder = self.app.store.game_dir(imported["instance_id"])
            self.assertEqual((folder / "mods/a.jar").read_bytes(), b"fixture")
            self.assertFalse((folder / "mods/b.jar.disabled").exists())
            if format_name == "starmoon":
                self.assertEqual(self.app.store.instance(imported["instance_id"])["window_title"], "{name} - {player}")

    def test_export_defaults_exclude_worlds_options_and_screenshots_and_reject_unknown_groups(self):
        result = self.app.content.export_pack(self.id, self.task())
        with zipfile.ZipFile(result["path"]) as archive:
            self.assertFalse(any("saves/" in name for name in archive.namelist()))
        with self.assertRaises(LauncherError):
            self.app.content.export_pack(self.id, self.task(), {"include": ["../credentials"]})
        with self.assertRaises(LauncherError):
            self.app.content.export_pack(self.id, self.task(), {"format": "unknown"})

    def test_cancel_export_removes_its_incomplete_archive(self):
        (self.game / "config").mkdir()
        (self.game / "config/a.cfg").write_bytes(b"config")
        task = self.task()
        original = task.update
        def update(message, progress=None):
            original(message, progress)
            if message.startswith("导出文件"):
                task.cancel.set()
        with patch.object(task, "update", side_effect=update), self.assertRaises(Cancelled):
            self.app.content.export_pack(self.id, task)
        self.assertEqual(list((self.root / "exports").iterdir()), [])
        self.assertEqual((self.world / "level.dat").read_bytes(), b"keep-world")

    def test_window_title_is_saved_validated_and_expands_known_placeholders(self):
        self.app.route("POST", "/api/instances/update", {}, {"id": self.id, "window_title": "{name} · {version} · {player}"})
        title = self.app.store.instance(self.id)["window_title"]
        self.assertEqual(render_title(title, self.instance, {"name": "FixturePlayer"}), "Export fixture · 1.20.1 · FixturePlayer")
        self.assertEqual(render_title("", self.instance, {}), "")
        with self.assertRaises(LauncherError):
            self.app.route("POST", "/api/instances/update", {}, {"id": self.id, "window_title": "x\ninvalid"})

    def test_screenshots_are_sorted_and_paths_are_restricted(self):
        root = self.game / "screenshots"
        root.mkdir()
        (root / "image.png").write_bytes(b"fixture")
        (root / "ignore.txt").write_bytes(b"ignore")
        self.assertEqual([item["name"] for item in listing(self.app.store, self.id)], ["image.png"])
        self.assertEqual(resolve(self.app.store, self.id, "image.png"), root / "image.png")
        for name in ("../options.txt", "ignore.txt", "missing.png"):
            with self.assertRaises(LauncherError):
                resolve(self.app.store, self.id, name)

    def mr_install(self, payload, world="Existing", filename="datapack.zip", loaders=None):
        data = {"id": "file", "project_id": "project", "game_versions": ["1.20.1"], "loaders": loaders or ["datapack"],
                "files": [{"filename": filename, "url": "https://cdn.modrinth.com/fixture.zip", "hashes": {}, "size": len(payload)}]}
        def download(url, target, **kwargs):
            Path(target).write_bytes(payload)
        with patch.object(self.app.net, "json", return_value=data), patch.object(self.app.net, "download", side_effect=download):
            return self.app.content.install_project("project", "file", self.id, "datapack", "modrinth", self.task(), world=world)

    def test_datapack_is_installed_only_in_chosen_world_and_normalizes_wrapper(self):
        data = zip_bytes({"Wrapper/pack.mcmeta": '{"pack":{"pack_format":15,"description":"Fixture"}}', "Wrapper/data/test/function/a.mcfunction": "say fixture"})
        result = self.mr_install(data)
        path = Path(result["path"])
        self.assertEqual(path.parent, self.world / "datapacks")
        with zipfile.ZipFile(path) as archive:
            self.assertIn("pack.mcmeta", archive.namelist())
            self.assertIn("data/test/function/a.mcfunction", archive.namelist())
        self.assertEqual((self.world / "level.dat").read_bytes(), b"keep-world")

    def test_datapack_rejects_missing_world_wrong_type_and_traversal(self):
        valid = zip_bytes({"pack.mcmeta": "{}", "data/test/a.json": "{}"})
        with self.assertRaises(LauncherError):
            self.mr_install(valid, world="../Outside")
        with self.assertRaises(LauncherError):
            self.mr_install(valid, loaders=["fabric"])
        with self.assertRaises(LauncherError):
            self.mr_install(zip_bytes({"../outside": "bad", "pack.mcmeta": "{}", "data/a.json": "{}"}))
        self.assertFalse((self.root / "outside").exists())

    def test_datapack_windows_archive_paths_are_normalized(self):
        result=self.mr_install(zip_bytes({"Wrapper\\pack.mcmeta":'{"pack":{"pack_format":15}}',"Wrapper\\data\\fixture\\a.json":"{}"}))
        with zipfile.ZipFile(result["path"]) as archive:
            self.assertIn("pack.mcmeta",archive.namelist())
            self.assertIn("data/fixture/a.json",archive.namelist())

    def test_map_import_does_not_replace_existing_save(self):
        payload = zip_bytes({"Map/level.dat": b"new-world", "Map/region/r.0.0.mca": b"region"})
        project = {"data": {"classId": 17}}
        file = {"data": {"modId": 1, "fileName": "Existing.zip", "gameVersions": ["1.20.1"]}}
        def download(file, target, task):
            Path(target).write_bytes(payload)
        with patch.object(self.app.content, "curse", side_effect=[project, file]), patch.object(self.app.content, "_cf_download", side_effect=download):
            result = self.app.content.install_project("1", "2", self.id, "map", "curseforge", self.task())
        path = Path(result["path"])
        self.assertNotEqual(path, self.world)
        self.assertEqual((path / "level.dat").read_bytes(), b"new-world")
        self.assertEqual((self.world / "level.dat").read_bytes(), b"keep-world")

    def test_download_filters_use_datapack_versions_and_map_class_without_modloader(self):
        with patch.object(self.app.net, "json", return_value={"hits":[],"total_hits":0}) as get:
            self.app.content.search("fixture", "datapack", provider="modrinth")
            params=urllib.parse.parse_qs(urllib.parse.urlsplit(get.call_args.args[0]).query)
            self.assertEqual(json.loads(params["facets"][0]), [["categories:datapack"]])
            self.app.content.versions("fixture", self.id, "datapack", "modrinth")
            params=urllib.parse.parse_qs(urllib.parse.urlsplit(get.call_args.args[0]).query)
            self.assertEqual(json.loads(params["loaders"][0]), ["datapack"])
        with patch.object(self.app.content, "curse", return_value={"data":[],"pagination":{"totalCount":0}}) as get:
            self.app.content.search("fixture", "map", loader="forge", provider="curseforge")
            params=urllib.parse.parse_qs(urllib.parse.urlsplit(get.call_args.args[0]).query)
            self.assertEqual(params["classId"],["17"])
            self.assertNotIn("modLoaderType",params)

    def test_bad_map_does_not_create_a_save_or_leave_partial_files(self):
        payload=zip_bytes({"readme.txt":"not a world"})
        def download(file,target,task): Path(target).write_bytes(payload)
        with patch.object(self.app.content,"curse",side_effect=[{"data":{"classId":17}},
             {"data":{"modId":1,"fileName":"bad.zip","gameVersions":["1.20.1"]}}]), \
             patch.object(self.app.content,"_cf_download",side_effect=download), self.assertRaises(LauncherError):
            self.app.content.install_project("1","2",self.id,"map","curseforge",self.task())
        self.assertEqual([path.name for path in (self.game/"saves").iterdir()],["Existing"])
        self.assertEqual(list((self.root/"cache/world-downloads").iterdir()),[])


if __name__ == "__main__":
    unittest.main()
