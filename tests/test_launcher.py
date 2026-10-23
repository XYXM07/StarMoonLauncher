import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from launcher.core import (Cancelled, LauncherError, Network, Store, Task, TaskManager,
                           atomic_json, extract_zip, mirror_url, protect, safe_path, unprotect)
from launcher.game import expand_arguments, maven_path, merge_profiles, rules_allow, shorten_classpath
from launcher.service import Application
from launcher.memory import automatic_memory, effective_memory, java_bits
from main import storage_paths


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Application(self.root)
        self.app.store.create_minecraft()

    def tearDown(self):
        self.temp.cleanup()

    def test_compact_default_migrates_old_standard_but_preserves_custom_size(self):
        directory = self.root / "size-migration"
        atomic_json(directory / "config.json", {"launcher_width": 1100, "launcher_height": 720})
        store = Store(directory)
        self.assertEqual((store.config()["launcher_width"], store.config()["launcher_height"]), (900,600))
        store.save({"launcher_width":1100,"launcher_height":720})
        again = Store(directory)
        self.assertEqual((again.config()["launcher_width"], again.config()["launcher_height"]), (1100,720))
        other = self.root / "custom-size"
        atomic_json(other / "config.json", {"launcher_width":980,"launcher_height":660})
        custom = Store(other)
        self.assertEqual((custom.config()["launcher_width"], custom.config()["launcher_height"]), (980,660))
        self.assertFalse(custom.minecraft_root.exists())

    def test_wallpaper_modes_migrate_custom_preserve_clear_and_restore_default(self):
        old = self.root/"wallpaper-migration"
        atomic_json(old/"config.json",{"background_path":"custom-picture.jpg"})
        self.assertEqual(Store(old).config()["background_mode"],"custom")
        self.assertEqual(self.app.store.config()["background_mode"],"default")
        self.app.config_update({"background_path":"chosen.jpg"})
        self.assertEqual(self.app.store.config()["background_mode"],"custom")
        self.app.config_update({"background_mode":"none","background_path":""})
        self.assertEqual(Store(self.root).config()["background_mode"],"none")
        self.app.config_update({"background_mode":"default","background_path":""})
        self.assertEqual(Store(self.root).config()["background_mode"],"default")
        with self.assertRaises(LauncherError): self.app.config_update({"background_mode":"unknown"})

    def test_preset_wallpaper_stable_id_persists_and_invalid_selection_is_rejected(self):
        from launcher.appearance import BACKGROUND_PRESETS, preset_background, wallpaper_path
        for preset,_,_ in BACKGROUND_PRESETS:
            self.assertTrue(preset_background(preset).is_file())
        self.app.config_update({"background_mode":"preset","background_preset":"forest-reflection","background_path":""})
        config = Store(self.root).config()
        self.assertEqual(config["background_preset"],"forest-reflection")
        self.assertEqual(config["background_path"],"")
        self.assertEqual(wallpaper_path(config),str(preset_background("forest-reflection")))
        for invalid in ("../background.jpg","unknown"):
            with self.assertRaises(LauncherError):
                self.app.config_update({"background_preset":invalid})
        self.assertEqual(self.app.store.config()["background_preset"],"forest-reflection")

    def test_offline_uuid_matches_java(self):
        account = self.app.accounts.offline("Steve")
        self.assertEqual(account["id"], "5627dd98e6be3c21b8a8e92344183641")
        self.assertEqual(self.app.accounts.for_launch(account["id"])["access_token"], "0")
        with self.assertRaises(LauncherError):
            self.app.accounts.offline("../invalid")

    def test_java_mismatch_auto_recovers_and_manual_mode_explains_requirement(self):
        wrong = self.root / "java24.exe"
        wrong.touch()
        self.app.config_update({"java_path": str(wrong), "java_auto": True})
        candidates = [{"path": str(wrong), "major": 24, "version": "24.0.2"},
                      {"path": "runtime/java17.exe", "major": 17, "version": "17.0.16"}]
        task = Task("Java selection", lambda t: None)
        with patch.object(self.app.game, "java_candidates", return_value=candidates):
            self.assertEqual(self.app.game.ensure_java(17, task), "runtime/java17.exe")
            self.assertEqual(self.app.game.ensure_java(24, task), str(wrong))
            self.app.config_update({"java_auto": False})
            with self.assertRaisesRegex(LauncherError, "Java 17.*Java 24"):
                self.app.game.ensure_java(17, task)

    def test_forge_bootstrap_ignores_actual_shared_parent_client_jar(self):
        instance = self.app.game.create("Forge", "1.20.1", "forge")
        instance["version"] = "1.20.1-forge-47"
        parent = {"id": "1.20.1", "libraries": [], "javaVersion": {"majorVersion": 17},
                  "arguments": {"jvm": ["-cp", "${classpath}"], "game": []}}
        child = {"id": instance["version"], "mainClass": "cpw.mods.bootstraplauncher.BootstrapLauncher",
                 "arguments": {"jvm": ["-DignoreList=bootstraplauncher,${version_name}.jar"]}}
        profile = merge_profiles(parent, child)
        account = self.app.accounts.for_launch(self.app.accounts.offline("ForgeTest")["id"])
        with patch.object(self.app.game, "profile", return_value=profile), patch("launcher.game.java_bits", return_value=64):
            command = self.app.game.command(instance, account, "java.exe")
        ignore = next(a for a in command if a.startswith("-DignoreList="))
        self.assertIn("1.20.1.jar", ignore.split("=", 1)[1].split(","))
        self.assertIn("1.20.1-forge-47.jar", ignore)
        self.assertIn(str(self.app.game.mc / "versions/1.20.1/1.20.1.jar"), command[command.index("-cp") + 1])
        profile["mainClass"] = "net.minecraft.client.main.Main"
        with patch.object(self.app.game, "profile", return_value=profile), patch("launcher.game.java_bits", return_value=64):
            vanilla = self.app.game.command(instance, account, "java.exe")
        self.assertNotIn("1.20.1.jar", next(a for a in vanilla if a.startswith("-DignoreList=")).split("=", 1)[1].split(","))

    def test_download_speed_aggregates_parallel_bytes_and_expires_when_idle(self):
        clock = [10.0]
        with patch("launcher.core.time.monotonic", side_effect=lambda: clock[0]):
            task = Task("Speed", lambda t: None)
            task.status = "running"
            threads = [threading.Thread(target=lambda: task.add_bytes(512*1024)) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            clock[0] = 11.0
            self.assertEqual(task.public()["speed"], 2*1024*1024)
            clock[0] = 12.1
            self.assertEqual(task.public()["speed"], 0)
            task.add_bytes(1024)
            self.assertGreater(task.public()["speed"], 0)
            task.status = "completed"
            self.assertEqual(task.public()["speed"], 0)
            task.status = "queued"
            self.assertEqual(task.public()["speed"], 0)
            self.assertEqual(task.public()["bytes"], 2*1024*1024+1024)

    def test_first_start_uses_sibling_minecraft_and_preserves_legacy_versions(self):
        directory = self.root / "portable"
        data, minecraft = storage_paths(directory)
        self.assertEqual(minecraft, directory / ".Minecraft")
        self.assertFalse(minecraft.exists())
        legacy = Application(data)
        legacy.store.create_minecraft()
        old = legacy.game.create("Existing", "1.21.1")
        world = legacy.store.instance_dir(old["id"]) / "game/saves/World/level.dat"
        world.parent.mkdir(parents=True)
        world.write_bytes(b"existing world")
        cached = legacy.game.mc / "libraries/test.jar"
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"immutable library")
        current = Application(data, minecraft_root=minecraft)
        self.assertEqual(current.game.mc, minecraft)
        self.assertFalse(minecraft.exists())
        current.route("POST", "/api/folders/create", {}, {})
        self.assertEqual((minecraft / "libraries/test.jar").read_bytes(), cached.read_bytes())
        self.assertEqual(world.read_bytes(), b"existing world")
        self.assertEqual(current.store.instance_dir(old["id"]), legacy.store.instance_dir(old["id"]))
        created = current.game.create("New", "1.21.1")
        self.assertEqual(current.store.instance_dir(created["id"]).parent, minecraft / "versions")
        self.assertEqual({i["name"] for i in current.store.instances()}, {"Existing", "New"})

    def test_automatic_memory_reserves_ram_and_honors_modes(self):
        self.assertEqual(automatic_memory(16384, 12000), 3072)
        self.assertGreater(automatic_memory(32768, 22000, 150), 3072)
        self.assertLessEqual(automatic_memory(8192, 4096, 300), 4096 - 1024)
        self.assertEqual(automatic_memory(2048, 900, 100), 512)
        self.assertLessEqual(automatic_memory(32768, 22000, 150, bits=32), 1280)
        config = {"memory_auto": True, "memory": 6144}
        available = {"total": 16384, "available": 12000}
        self.assertEqual(effective_memory(config, {"memory": 8192}, available), 8192)
        self.assertEqual(effective_memory(config, {"memory": 0}, available), 3072)
        config["memory_auto"] = False
        self.assertEqual(effective_memory(config, {"memory": 0}, available), 6144)
        self.assertEqual(effective_memory(config, {"memory_mode": "auto", "memory": 0}, available), 3072)

    def test_auto_heap_reaches_launch_command_and_java_32bit_is_detected(self):
        import struct
        executable = self.root / "java32.exe"
        pe = bytearray(70)
        pe[:2] = b"MZ"
        struct.pack_into("<I", pe, 60, 64)
        pe[64:68] = b"PE\0\0"
        struct.pack_into("<H", pe, 68, 0x14c)
        executable.write_bytes(pe)
        self.assertEqual(java_bits(executable), 32)
        instance = self.app.game.create("Auto", "1.21.1")
        atomic_json(self.app.game.profile_path("1.21.1"), {"id": "1.21.1", "mainClass": "Main", "libraries": [], "javaVersion": {"majorVersion": 21}, "arguments": {"game": []}})
        mods = self.app.store.instance_dir(instance["id"]) / "game/mods"
        mods.mkdir()
        for index in range(50):
            (mods / (str(index) + ".jar")).write_bytes(b"mod")
        account = self.app.accounts.for_launch(self.app.accounts.offline("Steve")["id"])
        with patch("launcher.memory.physical_memory", return_value={"total": 16384, "available": 12000}):
            command = self.app.game.command(instance, account, "java64.exe")
            self.assertIn("-Xmx4608M", command)
            self.assertIn("-Xmx1280M", self.app.game.command(instance, account, str(executable)))

    def test_existing_manual_memory_is_preserved_on_upgrade(self):
        atomic_json(self.root / "config.json", {"memory": 8192})
        store = Store(self.root)
        self.assertFalse(store.config()["memory_auto"])
        self.assertEqual(store.config()["memory"], 8192)

    def test_dismiss_finished_tasks_keeps_running_work(self):
        manager = TaskManager()
        finished = manager.submit("Finished", lambda t: None)["task_id"]
        deadline = time.time() + 3
        while not manager.tasks[finished].finished and time.time() < deadline:
            time.sleep(.01)
        gate = threading.Event()
        active = manager.submit("Active", lambda t: gate.wait(3))["task_id"]
        try:
            with self.assertRaises(LauncherError):
                manager.dismiss(active)
            manager.dismiss()
            self.assertIn(active, manager.tasks)
            self.assertNotIn(finished, manager.tasks)
        finally:
            gate.set()

    def test_game_record_dismiss_waits_for_log_monitor(self):
        from unittest.mock import MagicMock
        def process(code):
            return MagicMock(pid=123, poll=MagicMock(return_value=code))
        self.app.game.processes = {
            "active": {"process": process(None), "exit_code": None, "started": 0},
            "draining": {"process": process(0), "exit_code": None, "started": 0},
            "finished": {"process": process(0), "exit_code": 0, "started": 0}}
        with self.assertRaises(LauncherError):
            self.app.game.dismiss_process("active")
        self.app.game.dismiss_process()
        self.assertEqual(set(self.app.game.processes), {"active", "draining"})
        self.app.game.processes.clear()

    def test_world_folder_opens_only_an_existing_world_inside_the_instance(self):
        instance = self.app.game.create("Worlds", "1.21.1", "vanilla")
        world = self.app.store.instance_dir(instance["id"]) / "game/saves/My World"
        world.mkdir(parents=True)
        body = {"instance": instance["id"], "target": "world", "world": "My World"}
        with patch("launcher.service.os.startfile", create=True) as open_folder:
            result = self.app.route("POST", "/api/folder", {}, body)
            self.assertEqual(Path(result["path"]), world)
            for name in ("../outside", "missing", ""):
                with self.assertRaises(LauncherError):
                    self.app.route("POST", "/api/folder", {}, dict(body, world=name))

    def test_paths_and_zip_slip(self):
        for path in ("../evil", "D:/evil", "/evil", "nested/../../evil", "..\\evil"):
            with self.assertRaises(LauncherError):
                safe_path(self.root, path)
        archive = self.root / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../outside", "forbidden")
        with self.assertRaises(LauncherError):
            extract_zip(archive, self.root / "out")
        self.assertFalse((self.root / "outside").exists())

    def test_maven_and_profile_inheritance(self):
        self.assertEqual(maven_path("a.b:c:1:natives-windows"), "a/b/c/1/c-1-natives-windows.jar")
        parent = {"mainClass": "Base", "libraries": [{"name": "a:b:1"}, {"name": "c:d:1"}], "arguments": {"game": ["--base"], "jvm": ["-cp", "${classpath}"]}}
        child = {"mainClass": "Fabric", "libraries": [{"name": "a:b:2"}], "arguments": {"game": ["--fabric"]}}
        merged = merge_profiles(parent, child)
        self.assertEqual(merged["mainClass"], "Fabric")
        self.assertEqual({x["name"] for x in merged["libraries"]}, {"a:b:2", "c:d:1"})
        self.assertEqual(merged["arguments"]["game"], ["--base", "--fabric"])
        self.assertEqual(parent["libraries"][0]["name"], "a:b:1")
        conditional = {"libraries": [{"name": "a:b:1", "rules": [{"action": "allow", "os": {"name": "windows"}}]},
                                     {"name": "a:b:2", "rules": [{"action": "allow", "os": {"name": "osx"}}]}]}
        self.assertEqual(len(merge_profiles(conditional, {"mainClass": "Loader"})["libraries"]), 2)
        self.assertEqual(merge_profiles({"downloads": {"client": {"url": "client"}}}, {"downloads": {}})["downloads"]["client"]["url"], "client")
        with self.assertRaises(LauncherError):
            atomic_json(self.app.game.profile_path("cycle"), {"id": "cycle", "inheritsFrom": "cycle"})
            self.app.game.profile("cycle")

    def test_rules_features_and_arguments(self):
        self.assertFalse(rules_allow([{"action": "allow", "features": {"is_demo_user": True}}]))
        self.assertTrue(rules_allow([{"action": "allow", "features": {"has_custom_resolution": True}}], {"has_custom_resolution": True}))
        args = ["--name", "${name}", {"rules": [{"action": "allow", "features": {"is_demo_user": True}}], "value": "--demo"}]
        self.assertEqual(expand_arguments(args, {"name": "Steve"}), ["--name", "Steve"])

    def test_launch_command_paths_and_auth_are_separate_args(self):
        instance = self.app.game.create("Test", "1.21.1")
        profile = {"id": "1.21.1", "mainClass": "net.minecraft.client.main.Main", "type": "release",
                   "libraries": [], "arguments": {"jvm": ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"],
                   "game": ["--username", "${auth_player_name}", "--accessToken", "${auth_access_token}", "--gameDir", "${game_directory}"]}}
        atomic_json(self.app.game.profile_path("1.21.1"), profile)
        account = self.app.accounts.offline("Steve")
        command = self.app.game.command(instance, self.app.accounts.for_launch(account["id"]), "C:/Java Path/java.exe")
        self.assertEqual(command[0], "C:/Java Path/java.exe")
        self.assertEqual(command[command.index("--username") + 1], "Steve")
        self.assertEqual(command[command.index("--accessToken") + 1], "0")
        self.assertEqual(command[command.index("--gameDir") + 1], str(self.app.store.instance_dir(instance["id"]) / "game"))
        self.assertFalse(any("${" in arg for arg in command))

    def test_mirrors_and_unchanged_third_party_hosts(self):
        self.assertEqual(mirror_url("https://resources.download.minecraft.net/ab/abcd"), "https://bmclapi2.bangbang93.com/assets/ab/abcd")
        self.assertEqual(mirror_url("https://libraries.minecraft.net/a.jar"), "https://bmclapi2.bangbang93.com/maven/a.jar")
        self.assertEqual(mirror_url("https://api.minecraftservices.com/a"), "https://api.minecraftservices.com/a")

    def test_long_classpath_is_accepted_by_real_java_without_writing_tokens(self):
        import subprocess
        candidates = [j for j in self.app.game.java_candidates() if j["major"] >= 9]
        if not candidates:
            self.skipTest("Requires a local Java 9+ runtime")
        java = candidates[0]
        classpath = os.pathsep.join([str(self.root / "space path" / ("library-" + str(i) + ".jar")) for i in range(1000)])
        command = [java["path"], "-cp", classpath, "-version"]
        compact = shorten_classpath(command, self.root, java["major"])
        result = subprocess.run(compact, capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        token_command = [java["path"], "-cp", classpath, "Main", "--accessToken", "sensitive-token"]
        compact = shorten_classpath(token_command, self.root, java["major"])
        self.assertIn("sensitive-token", compact)
        self.assertNotIn("sensitive-token", (self.root / "classpath.args").read_text())

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows specific")
    def test_windows_dpapi_round_trip(self):
        secret = b"test-refresh-token-not-real"
        encrypted = protect(secret)
        self.assertNotIn(secret.decode(), encrypted)
        self.assertEqual(unprotect(encrypted), secret)

    def test_content_toggle_and_recycle(self):
        instance = self.app.game.create("Fabric Test", "1.21.1", "fabric")
        mod = self.app.store.instance_dir(instance["id"]) / "game" / "mods" / "test.jar"
        mod.parent.mkdir(parents=True)
        mod.write_bytes(b"test")
        self.app.content.toggle(instance["id"], "test.jar", "mod")
        self.assertFalse(mod.exists())
        self.assertTrue(mod.with_suffix(".jar.disabled").exists())
        self.app.content.toggle(instance["id"], "test.jar.disabled", "mod")
        self.assertTrue(mod.exists())
        self.app.content.toggle(instance["id"], "test.jar", "mod", True)
        self.assertFalse(mod.exists())
        self.assertEqual(len(list((mod.parent.parent.parent / "trash").glob("*"))), 1)

    def test_modpack_import_install_order_and_overrides(self):
        archive = self.root / "test.mrpack"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("modrinth.index.json", json.dumps({"formatVersion": 1, "game": "minecraft", "name": "Pack",
                        "dependencies": {"minecraft": "1.21.1", "fabric-loader": "0.16.0"}, "files": []}))
            zf.writestr("overrides/config/test.json", "{}")
            zf.writestr("client-overrides/config/test.json", '{"client":true}')
            zf.writestr("server-overrides/config/server.txt", "do not install")
        with patch.object(self.app.game, "install") as install:
            result = self.app.content.import_pack(archive, Task("test", None))
        install.assert_called_once()
        instance = self.app.store.instance(result["instance_id"])
        self.assertTrue(instance["ready"])
        self.assertEqual(instance["loader"], "fabric")
        directory = self.app.store.instance_dir(instance["id"]) / "game"
        self.assertEqual(json.loads((directory / "config/test.json").read_text()), {"client": True})
        self.assertFalse((directory / "config/server.txt").exists())

    def test_task_cancellation_while_queued(self):
        manager = TaskManager()
        gate = threading.Event()
        manager.submit("blocking", lambda t: gate.wait(3))
        called = threading.Event()
        second = manager.submit("queued", lambda t: called.set())
        manager.cancel_task(second["task_id"])
        gate.set()
        for _ in range(50):
            if manager.tasks[second["task_id"]].finished:
                break
            time.sleep(.02)
        self.assertFalse(called.is_set())
        self.assertEqual(manager.tasks[second["task_id"]].status, "cancelled")

    def test_failed_pack_cannot_be_marked_ready_by_base_game_repair(self):
        instance = self.app.game.create("Incomplete Pack", "1.21.1", "fabric")
        self.app.store.update_instance(instance["id"], {"pack_install_pending": True, "loader_installed": True})
        profile = {"id": "1.21.1", "mainClass": "Main", "libraries": [], "javaVersion": {"majorVersion": 21}}
        atomic_json(self.app.game.profile_path("1.21.1"), profile)
        with patch.object(self.app.game, "ensure_base"), patch.object(self.app.game, "install_files", return_value=profile), patch.object(self.app.game, "ensure_java", return_value="java.exe"):
            self.app.game.install(instance["id"], Task("repair", None))
        self.assertFalse(self.app.store.instance(instance["id"])["ready"])

    def test_download_detects_corruption_and_cleans_partial_files(self):
        import io
        payload = b"downloaded test payload"
        class Response(io.BytesIO):
            headers = {"Content-Length": str(len(payload))}
        target = self.root / "download.jar"
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: Response(payload)):
            self.app.net.download("https://example.com/test.jar", target, sha1=hashlib.sha1(payload).hexdigest(), size=len(payload))
        self.assertEqual(target.read_bytes(), payload)
        target.write_bytes(b"corrupted")
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: Response(payload)), patch("time.sleep"):
            with self.assertRaises(LauncherError):
                self.app.net.download("https://example.com/test.jar", target, sha1="0" * 40)
        self.assertEqual(list(self.root.glob("*.part-*")), [])

    def test_mirror_failure_falls_back_to_official(self):
        self.app.store.save({"source": "bmclapi"})
        with patch("launcher.core.request_json", side_effect=[OSError("mirror down"), {"ok": True}]) as fetch:
            self.assertEqual(self.app.net.json("https://piston-meta.mojang.com/test.json"), {"ok": True})
        self.assertIn("bmclapi2.bangbang93.com", fetch.call_args_list[0][0][0])
        self.assertIn("piston-meta.mojang.com", fetch.call_args_list[1][0][0])

    @unittest.skipUnless(os.name == "nt", "Uses Windows credential encryption")
    def test_microsoft_chain_and_refresh_keep_tokens_out_of_public_config(self):
        def responses(name="Player"):
            return [
                {"Token": "xbox-token"},
                {"Token": "xsts-token", "DisplayClaims": {"xui": [{"uhs": "userhash", "xid": "xuid"}]}},
                {"access_token": "minecraft-secret-token", "expires_in": 3600},
                {"items": [{"name": "game_minecraft"}]},
                {"id": "a" * 32, "name": name},
            ]
        token = {"access_token": "microsoft-secret-token", "refresh_token": "refresh-secret-token"}
        with patch("launcher.accounts.request_json", side_effect=responses()) as request:
            account = self.app.accounts._exchange(token, "client-id", {"auth_backend": "wam", "broker_home_account_id": "home-id"})
            identity = request.call_args_list[2][0][1]["identityToken"]
            self.assertEqual(identity, "XBL3.0 x=userhash;xsts-token")
        self.app.accounts._save_account(account)
        self.assertNotIn("secret-token", json.dumps(self.app.store.public_config()))
        self.assertNotIn("secret-token", self.app.accounts.path.read_text())
        credentials = self.app.accounts._credentials(account["id"])
        self.assertNotIn("refresh_token", credentials)
        credentials["expires"] = 0
        self.app.accounts._credentials(account["id"], credentials)
        with patch.object(self.app.accounts.broker, "silent", return_value={"access_token": "refreshed-ms-token"}) as silent, patch("launcher.accounts.request_json", side_effect=responses("Renamed")) as refresh_request:
            launch = self.app.accounts.for_launch(account["id"])
        silent.assert_called_once_with("client-id", "home-id")
        self.assertEqual(refresh_request.call_args_list[0][0][0], "https://user.auth.xboxlive.com/user/authenticate")
        self.assertEqual(launch["name"], "Renamed")
        self.assertEqual(launch["xuid"], "xuid")

if __name__ == "__main__":
    unittest.main()
