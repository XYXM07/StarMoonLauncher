"""Real loader metadata shapes, patch profiles, Java versions and integrity checks."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from launcher.core import LauncherError, Task, atomic_json
from launcher.game import maven_path
from launcher.service import Application


class ExtraLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.app = Application(self.root)
        self.app.store.create_minecraft()

    def tearDown(self):
        self.temp.cleanup()

    def test_catalog_rejects_incompatible_game_versions_and_parses_liteloader_builds(self):
        with patch.object(self.app.net, "json") as get:
            self.assertEqual(self.app.game.loader_versions("1.20.1", "cleanroom"), [])
            self.assertEqual(self.app.game.loader_versions("1.20.1", "liteloader"), [])
            get.assert_not_called()
        entry = {"version": "1.12.2-SNAPSHOT", "build": "build-4", "md5": "a"*32, "libraries": [{"name":"net.minecraft:launchwrapper:1.12"}]}
        data = {"versions":{"1.12.2":{"snapshots":{"libraries":[], "com.mumfrey:liteloader":{"latest":entry, "a"*32:entry}}}}}
        with patch.object(self.app.net, "json", return_value=data):
            result = self.app.game.loader_versions("1.12.2", "liteloader")
        self.assertEqual([item["version"] for item in result], ["build-4"])

    def test_cleanroom_uses_official_full_profile_embedded_libraries_and_java25(self):
        instance = self.app.game.create("Clean", "1.12.2", "cleanroom", "0.7.12-alpha")
        payload = b"fixture-universal"
        library = "com.cleanroommc:cleanroom:0.7.12-alpha:universal"
        path = maven_path(library)
        source = self.root / "cleanroom-installer.jar"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("install_profile.json", json.dumps({"minecraft":"1.12.2", "processors":[], "java":{"minimum":25}}))
            archive.writestr("version.json", json.dumps({"id":"Cleanroom", "mainClass":"top.outlands.foundation.boot.Foundation",
                "javaVersion":{"majorVersion":25}, "libraries":[{"name":library,"downloads":{"artifact":{"path":path,"url":"","sha1":hashlib.sha1(payload).hexdigest()}}}]}))
            archive.writestr("maven/"+path, payload)
        versions = [{"version":"0.7.12-alpha", "stable":False, "url":"https://github.com/CleanroomMC/Cleanroom/releases/download/fixture/installer.jar"}]
        def download(url, target, **kwargs):
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(source), str(target))
        with patch("launcher.extra_loaders.versions", return_value=versions), patch.object(self.app.net,"download",side_effect=download):
            profile_id = self.app.game.install_loader(instance, Task("Cleanroom",None))
            library_path=self.app.game.mc/'libraries'/path
            os.utime(str(library_path),(1000000000,1000000000))
            previous_mtime=library_path.stat().st_mtime_ns
            self.app.game.install_loader(instance, Task("Reuse Cleanroom",None))
            self.assertEqual(library_path.stat().st_mtime_ns,previous_mtime)
        profile = self.app.game.profile(profile_id)
        self.assertEqual(profile["javaVersion"]["majorVersion"],25)
        self.assertNotIn("inheritsFrom",profile)
        self.assertEqual(profile["jar"],"1.12.2")
        self.assertEqual((self.app.game.mc/'libraries'/path).read_bytes(),payload)

    def test_optifine_patcher_uses_vanilla_client_and_embedded_launch_wrapper(self):
        instance = self.app.game.create("OptiFine", "1.20.1", "optifine", "HD_U_I6")
        atomic_json(self.app.game.profile_path("1.20.1"), {"id":"1.20.1","mainClass":"net.minecraft.client.main.Main",
            "javaVersion":{"majorVersion":17}, "downloads":{"client":{"url":"https://example.invalid/client.jar"}},
            "arguments":{"game":["--username","${auth_player_name}"],"jvm":[]}, "libraries":[]})
        source = self.root / "optifine.jar"
        with zipfile.ZipFile(source,"w") as archive:
            archive.writestr("optifine/Patcher.class",b"fixture")
            archive.writestr("launchwrapper-of.txt","2.3")
            archive.writestr("launchwrapper-of-2.3.jar",b"wrapper")
        def download(url,target,**kw):
            Path(target).parent.mkdir(parents=True,exist_ok=True)
            if url.endswith("client.jar"):
                Path(target).write_bytes(b"vanilla")
            else:
                shutil.copyfile(str(source),str(target))
        def patcher(game, item, command, task):
            self.assertEqual(command[3],"optifine.Patcher")
            self.assertEqual(Path(command[4]).read_bytes(),b"vanilla")
            with zipfile.ZipFile(command[-1],"w") as output:
                output.writestr("optifine/OptiFineTweaker.class",b"fixture")
        with patch("launcher.extra_loaders.versions",return_value=[{"version":"HD_U_I6","stable":True,"url":"https://example.invalid/optifine.jar"}]), \
             patch.object(self.app.net,"download",side_effect=download), patch.object(self.app.game,"ensure_java",return_value="java.exe"), \
             patch("launcher.extra_loaders.run_patcher",side_effect=patcher):
            profile_id = self.app.game.install_loader(instance,Task("OptiFine",None))
        profile = self.app.game.profile(profile_id)
        self.assertEqual(profile["mainClass"],"net.minecraft.launchwrapper.Launch")
        self.assertEqual(profile["arguments"]["game"][-2:],["--tweakClass","optifine.OptiFineTweaker"])
        self.assertIn("--username",profile["arguments"]["game"])
        self.assertTrue(any(lib["name"]=="optifine:launchwrapper-of:2.3" for lib in profile["libraries"]))

    def test_liteloader_keeps_vanilla_arguments_and_checks_md5(self):
        instance=self.app.game.create("Lite", "1.12.2", "liteloader", "build-4")
        atomic_json(self.app.game.profile_path("1.12.2"),{"id":"1.12.2","minecraftArguments":"--username ${auth_player_name}","libraries":[]})
        record={"version":"build-4","artifact_version":"1.12.2-SNAPSHOT","stable":False,"md5":"a"*32,"libraries":[]}
        def download(url,target,**kwargs):
            self.assertEqual(kwargs["md5"],"a"*32)
            Path(target).parent.mkdir(parents=True,exist_ok=True)
            Path(target).write_bytes(b"fixture")
        with patch("launcher.extra_loaders.versions",return_value=[record]), patch.object(self.app.net,"download",side_effect=download):
            profile_id=self.app.game.install_loader(instance,Task("LiteLoader",None))
        profile=self.app.game.profile(profile_id)
        self.assertIn("--username",profile["minecraftArguments"])
        self.assertIn("LiteLoaderTweaker",profile["minecraftArguments"])

    def test_litemod_can_be_listed_described_and_disabled(self):
        instance=self.app.game.create("Lite mods","1.12.2","liteloader","build-4")
        folder=self.app.store.game_dir(instance["id"])/"mods"
        folder.mkdir()
        with zipfile.ZipFile(folder/"fixture.litemod","w") as archive:
            archive.writestr("litemod.json",json.dumps({"name":"fixture","displayName":"Lite fixture","description":"A LiteLoader fixture"}))
        item=self.app.content.list_local(instance["id"])[0]
        self.assertEqual(item["mod_name"],"Lite fixture")
        self.assertEqual(item["description"],"A LiteLoader fixture")
        self.app.content.toggle(instance["id"],"fixture.litemod","mod")
        self.assertFalse(self.app.content.list_local(instance["id"])[0]["enabled"])


if __name__=="__main__":
    unittest.main()
