"""Failed pack sessions own fresh directories, never existing games or source archives."""
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from launcher.core import Cancelled, LauncherError, Task, read_json
from launcher.pack_session import pack_session
from launcher.service import Application


class PackCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.app = Application(self.root / "data")
        self.app.store.create_minecraft()
        self.old = self.app.game.create("Existing", "1.21.1")
        self.world = self.app.store.game_dir(self.old["id"]) / "saves/World/level.dat"
        self.world.parent.mkdir(parents=True)
        self.world.write_bytes(b"keep-world")
        self.task = Task("Import pack", None)
        self.archive = self.root / "original.mrpack"

    def tearDown(self):
        self.temp.cleanup()

    def write_pack(self, file=False, traversal=False):
        index = {"formatVersion": 1, "game": "minecraft", "name": "New Pack", "dependencies": {"minecraft": "1.21.1"}, "files": []}
        if file:
            index["files"] = [{"path": "mods/test.jar", "downloads": ["https://original.invalid/test.jar"],
                               "hashes": {"sha1": hashlib.sha1(b"fixture").hexdigest()}, "fileSize": 7}]
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("modrinth.index.json", json.dumps(index))
            archive.writestr("overrides/config/settings.txt", "fixture")
            if traversal:
                archive.writestr("overrides/../../escape.txt", "unsafe")

    def assert_cleaned(self):
        self.assertEqual([item["id"] for item in self.app.store.instances()], [self.old["id"]])
        self.assertEqual(self.app.store.config()["selected_instance"], self.old["id"])
        self.assertEqual(self.world.read_bytes(), b"keep-world")
        self.assertEqual(list(self.app.store.instances_root.iterdir()), [self.app.store.instance_dir(self.old["id"])])
        stages = self.app.store.root / "cache/pack-sessions"
        self.assertFalse(stages.exists() and any(stages.iterdir()))
        self.assertTrue(self.task.pack_install["cleaned"])

    def test_failed_base_install_removes_new_instance_and_preserves_source_and_old_world(self):
        self.write_pack()
        original = self.archive.read_bytes()
        def fail(identifier, task):
            (self.app.store.game_dir(identifier) / "partial.tmp").write_bytes(b"partial")
            raise LauncherError("base install failed")
        with patch.object(self.app.game, "install", side_effect=fail):
            with self.assertRaises(LauncherError):
                self.app.content.import_file(self.archive, self.task)
        self.assert_cleaned()
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertEqual(self.task.pack_install["status"], "failed")

    def test_state_refresh_tolerates_instance_removed_after_directory_scan(self):
        removed = self.app.game.create("Being cleaned", "1.21.1")
        metadata = self.app.store.instance_dir(removed["id"]) / "instance.json"
        def read_during_cleanup(path, default=None):
            if Path(path) == metadata and metadata.exists():
                metadata.unlink()
            return read_json(path, default)
        with patch("launcher.core.read_json", side_effect=read_during_cleanup):
            state = self.app.state()
        self.assertEqual([item["id"] for item in state["instances"]], [self.old["id"]])
        self.assertEqual(self.world.read_bytes(), b"keep-world")

    def test_download_failure_and_cancellation_clean_the_new_instance(self):
        self.write_pack(file=True)
        self.app.config_update({"ai_pack_download_fallback": False})
        for error in (LauncherError("404"), Cancelled("cancelled")):
            task = Task("Pack", None)
            with patch.object(self.app.game, "install"), patch.object(self.app.net, "download", side_effect=error):
                with self.assertRaises(LauncherError):
                    self.app.content.import_file(self.archive, task)
            self.task = task
            self.assert_cleaned()

    def test_override_traversal_failure_removes_already_extracted_files(self):
        self.write_pack(traversal=True)
        with patch.object(self.app.game, "install"):
            with self.assertRaises(LauncherError):
                self.app.content.import_file(self.archive, self.task)
        self.assert_cleaned()
        self.assertFalse((self.app.store.instances_root / "escape.txt").exists())

    def test_success_keeps_instance_and_cleans_archive_and_owner_marker(self):
        self.write_pack()
        with patch.object(self.app.game, "install"):
            result = self.app.content.import_file(self.archive, self.task)
        directory = self.app.store.instance_dir(result["instance_id"])
        self.assertTrue((directory / "game/config/settings.txt").is_file())
        self.assertFalse((directory / ".pack-install-owner").exists())
        self.assertTrue(self.app.store.instance(result["instance_id"])["ready"])
        self.assertFalse(any((self.app.store.root / "cache/pack-sessions").iterdir()))
        self.assertTrue(self.archive.exists())

    def test_failure_during_metadata_creation_is_also_owned_and_cleaned(self):
        self.write_pack()
        original = __import__("launcher.game", fromlist=["atomic_json"]).atomic_json
        def fail(path, data):
            if Path(path).name == "instance.json":
                raise OSError("cannot write metadata")
            return original(path, data)
        with patch("launcher.game.atomic_json", side_effect=fail):
            with self.assertRaises(OSError):
                self.app.content.import_file(self.archive, self.task)
        self.assert_cleaned()

    def test_existing_directory_cannot_be_registered_as_a_new_pack(self):
        self.write_pack()
        with patch("launcher.game.uuid.uuid4") as generated:
            generated.return_value.hex = self.old["id"].split("-", 1)[1] + "0" * 22
            with self.assertRaises(FileExistsError):
                self.app.content.import_file(self.archive, self.task)
        self.assert_cleaned()

    def test_cleanup_errors_are_reported_instead_of_claiming_success(self):
        self.write_pack()
        original = __import__("shutil").rmtree
        def remove(path, *args, **kwargs):
            if Path(path).parent == self.app.store.instances_root:
                raise PermissionError("file is in use")
            return original(path, *args, **kwargs)
        with patch.object(self.app.game, "install", side_effect=LauncherError("failed")), patch("launcher.pack_session.shutil.rmtree", side_effect=remove):
            with self.assertRaises(LauncherError):
                self.app.content.import_file(self.archive, self.task)
        self.assertFalse(self.task.pack_install["cleaned"])
        self.assertTrue(self.task.pack_install["cleanup_errors"])
        self.assertEqual(self.world.read_bytes(), b"keep-world")

    def test_changed_ownership_marker_prevents_deletion(self):
        with self.assertRaises(LauncherError):
            with pack_session(self.app.content, self.task) as session:
                created = session.create("New", "1.21.1")
                directory = self.app.store.instance_dir(created["id"])
                (directory / ".pack-install-owner").write_text("different owner")
                raise LauncherError("failed")
        self.assertTrue(directory.exists())
        self.assertFalse(self.task.pack_install["cleaned"])
        self.assertEqual(self.world.read_bytes(), b"keep-world")

    def test_corrupt_archive_reports_failure_without_touching_existing_game(self):
        self.archive.write_bytes(b"not zip")
        with self.assertRaisesRegex(LauncherError, "ZIP"):
            self.app.content.import_file(self.archive, self.task)
        self.assert_cleaned()

    def test_nested_windows_junction_is_removed_without_deleting_its_target(self):
        import _winapi
        if not hasattr(_winapi, "CreateJunction"):
            self.skipTest("Windows junction fixture unavailable")
        outside = self.root / "preserved-outside"
        outside.mkdir()
        kept = outside / "keep.dat"
        kept.write_bytes(b"keep")
        with self.assertRaises(LauncherError):
            with pack_session(self.app.content, self.task) as session:
                created = session.create("New", "1.21.1")
                _winapi.CreateJunction(str(outside), str(self.app.store.game_dir(created["id"]) / "linked"))
                raise LauncherError("failed")
        self.assert_cleaned()
        self.assertEqual(kept.read_bytes(), b"keep")

    def test_cleanup_refuses_an_outside_absolute_target(self):
        outside = self.root / "keep-outside"
        outside.mkdir()
        with pack_session(self.app.content, self.task) as session:
            with self.assertRaises(LauncherError):
                session._remove(outside, self.app.store.instances_root)
        self.assertTrue(outside.is_dir())

    def test_temporary_windows_file_lock_is_retried(self):
        self.write_pack()
        original = __import__("shutil").rmtree
        blocked = []
        def remove(path, *args, **kwargs):
            if not blocked:
                blocked.append(str(path))
                raise PermissionError("temporary sharing violation")
            return original(path, *args, **kwargs)
        with patch.object(self.app.game, "install", side_effect=LauncherError("failed")), patch("launcher.pack_session.shutil.rmtree", side_effect=remove):
            with self.assertRaises(LauncherError):
                self.app.content.import_file(self.archive, self.task)
        self.assertTrue(blocked)
        self.assert_cleaned()


if __name__ == "__main__":
    unittest.main()
