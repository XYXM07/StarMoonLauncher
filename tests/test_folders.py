import hashlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from launcher.core import LauncherError, Task, atomic_json
from launcher.folders import add_folder
from launcher.service import Application


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Application(self.root / "launcher")
        self.external = self.root / "original/.minecraft"
        self.base = self.external / "versions/1.20.1"
        self.base.mkdir(parents=True)
        atomic_json(self.base / "1.20.1.json", {"id": "1.20.1", "mainClass": "net.minecraft.client.main.Main", "libraries": [],
                    "javaVersion": {"majorVersion": 17}, "arguments": {"jvm": ["-cp", "${classpath}"], "game": ["--gameDir", "${game_directory}", "--assetsDir", "${assets_root}"]}})
        (self.base / "1.20.1.jar").write_bytes(b"original game")

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        return {p.relative_to(self.external).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.external.rglob("*") if p.is_file()}

    def test_add_and_restart_reference_original_files_without_copying(self):
        (self.external / "saves/World").mkdir(parents=True)
        (self.external / "saves/World/level.dat").write_bytes(b"world")
        before = self.snapshot()
        result = add_folder(self.app.game, self.external)
        self.assertEqual(len(result["instances"]), 1)
        instance = self.app.store.instance(result["instances"][0])
        self.assertEqual(self.app.store.game_dir(instance["id"]), self.external)
        self.assertFalse((self.app.game.mc / "versions/1.20.1").exists())
        self.assertFalse((self.app.store.instance_dir(instance["id"]) / "game").exists())
        self.app.store.update_instance(instance["id"], {"name": "Kept Name", "favorite": True})
        restarted = Application(self.app.store.root)
        self.assertEqual(restarted.store.instance(instance["id"])["name"], "Kept Name")
        self.assertEqual(restarted.state()["minecraft_path"], str(self.external))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(add_folder(restarted.game, self.external)["instances"], result["instances"])

    def test_unicode_isolated_version_uses_original_mods_assets_and_client(self):
        name = "中文 Fabric 整合包"
        version = self.external / "versions" / name
        (version / "mods").mkdir(parents=True)
        (version / "mods/existing.jar").write_bytes(b"original mod")
        atomic_json(version / (name + ".json"), {"id": name, "inheritsFrom": "1.20.1", "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient"})
        result = add_folder(self.app.game, self.external)
        instance = next(self.app.store.instance(i) for i in result["instances"] if self.app.store.instance(i)["version"] == name)
        account = self.app.accounts.for_launch(self.app.accounts.offline("FolderTest")["id"])
        with patch("launcher.game.java_bits", return_value=64):
            command = self.app.game.command(instance, account, "java.exe")
        self.assertEqual(command[command.index("--gameDir") + 1], str(version))
        self.assertEqual(command[command.index("--assetsDir") + 1], str(self.external / "assets"))
        self.assertIn(str(self.base / "1.20.1.jar"), command[command.index("-cp") + 1])
        self.assertEqual(self.app.content.list_local(instance["id"])[0]["filename"], "existing.jar")
        self.app.content.toggle(instance["id"], "existing.jar", "mod")
        self.assertTrue((version / "mods/existing.jar.disabled").is_file())
        self.assertFalse((self.app.game.mc / "versions" / name).exists())

    def test_pcl_explicit_isolation_overrides_directory_heuristics(self):
        (self.base / "PCL").mkdir()
        (self.base / "mods").mkdir()
        (self.base / "PCL/Setup.ini").write_text("VersionArgumentIndieV2:False\n", encoding="utf-8")
        identifier = add_folder(self.app.game, self.external)["instances"][0]
        self.assertEqual(self.app.store.game_dir(identifier), self.external)
        (self.base / "PCL/Setup.ini").write_text("VersionArgumentIndie:1\n", encoding="utf-8")
        add_folder(self.app.game, self.external)
        self.assertEqual(self.app.store.game_dir(identifier), self.base)

    def test_remove_reference_preserves_original_and_refresh_does_not_restore_it(self):
        identifier = add_folder(self.app.game, self.external)["instances"][0]
        before = self.snapshot()
        result = self.app.route("POST", "/api/instances/delete", {}, {"id": identifier})
        task = self.app.tasks.tasks[result["task_id"]]
        deadline = time.time() + 3
        while not task.finished and time.time() < deadline:
            time.sleep(.01)
        self.assertEqual(task.status, "completed", task.error)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(add_folder(self.app.game, self.external)["instances"], [])
        self.assertNotIn(identifier, [i["id"] for i in self.app.store.instances()])

    def test_launch_working_directory_is_original_and_process_registry_is_shared(self):
        identifier = add_folder(self.app.game, self.external)["instances"][0]
        account = self.app.accounts.offline("FolderTest")
        process = Mock(pid=4242, stdout=io.BytesIO(b"game output\n"))
        process.poll.return_value = 0
        process.wait.return_value = 0
        with patch.object(self.app.game, "install_files"), patch.object(self.app.game, "ensure_java", return_value="java.exe"), patch("launcher.game.java_bits", return_value=64), patch("launcher.game.subprocess.Popen", return_value=process) as start:
            self.app.game.launch(identifier, account["id"], Task("Launch", None))
        self.assertEqual(start.call_args.kwargs["cwd"], str(self.external))
        deadline = time.time() + 3
        while not self.app.game.process_status()[0]["finalized"] and time.time() < deadline:
            time.sleep(.01)
        self.assertEqual(self.app.game.process_status()[0]["instance_id"], identifier)
        self.assertTrue(self.app.game.process_status()[0]["finalized"])

    def test_same_version_in_different_roots_is_distinct_and_bad_parent_is_skipped(self):
        first = add_folder(self.app.game, self.external)["instances"][0]
        another = self.root / "second/.minecraft"
        path = another / "versions/1.20.1/1.20.1.json"
        atomic_json(path, json.loads((self.base / "1.20.1.json").read_text(encoding="utf-8")))
        path.with_suffix(".jar").write_bytes(b"second game")
        second = add_folder(self.app.game, another)["instances"][0]
        self.assertNotEqual(first, second)
        atomic_json(self.external / "versions/broken/broken.json", {"id": "broken", "inheritsFrom": "../escape"})
        result = add_folder(self.app.game, self.external)
        self.assertEqual(result["instances"], [first])
        self.assertEqual(len(result["skipped"]), 1)
        with self.assertRaises(LauncherError):
            add_folder(self.app.game, self.root)
