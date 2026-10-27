"""Per-instance arguments, JVM selection, native adaptation and renderer runtime."""
import copy
import struct
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from launcher.core import Cancelled, LauncherError, Task, atomic_json
from launcher.launch_options import DEFAULTS, MESA, java_architecture, mesa_path, prepare_renderer, renderer_arguments
from launcher.service import Application
from test_multigame import ControlledProcess


def java_fixture(path, machine=0x8664):
    data=bytearray(160)
    data[:2]=b"MZ"
    struct.pack_into("<I",data,60,128)
    data[128:132]=b"PE\0\0"
    struct.pack_into("<H",data,132,machine)
    Path(path).write_bytes(data)
    return str(path)


class LaunchOptionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.app=Application(self.root/"data")
        self.app.store.create_minecraft()
        self.instance=self.app.game.create("Launch options","1.20.1")
        self.account=self.app.accounts.offline("OptionsTester")
        self.profile={"id":"1.20.1","mainClass":"Main","javaVersion":{"majorVersion":17},"libraries":[],
            "arguments":{"jvm":["-Drequired=true","-Djava.library.path=${natives_directory}","-cp","${classpath}"],
                         "game":["--username","${auth_player_name}"]}}
        atomic_json(self.app.game.profile_path("1.20.1"),self.profile)
        self.java=java_fixture(self.root/"java.exe")
        self.children=[]

    def tearDown(self):
        for child in self.children:
            child.finish()
        deadline=time.time()+4
        while self.children and time.time()<deadline and any(not item.get("finalized") for item in self.app.game.process_status()):
            time.sleep(.01)
        self.temp.cleanup()

    def update(self, **changes):
        self.app.route("POST","/api/instances/update",{},dict(changes,id=self.instance["id"]))
        self.instance=self.app.store.instance(self.instance["id"])

    def test_defaults_and_strict_instance_scoped_validation(self):
        self.assertTrue(all(self.instance[key]==value for key,value in DEFAULTS.items()))
        other=self.app.game.create("Other","1.20.1")
        self.update(renderer="zink",skip_game_check=True,skip_java_check=True)
        self.assertEqual(self.app.store.instance(other["id"])["renderer"],"opengl")
        for changes in ({"renderer":"wulkan"},{"skip_game_check":"true"},{"skip_native_patch":1}):
            with self.assertRaises(LauncherError):
                self.update(**changes)
        self.assertEqual(self.app.store.instance(self.instance["id"])["renderer"],"zink")

    def test_omit_generated_jvm_arguments_keeps_required_and_custom_arguments(self):
        self.app.store.save({"jvm_args":"-Dglobal=true"})
        self.update(jvm_args="-Xmx3G -Dcustom=true")
        account=self.app.accounts.for_launch(self.account["id"])
        command=self.app.game.command(self.instance,account,self.java)
        self.assertIn("-Xms256M",command)
        self.update(no_default_jvm_args=True)
        command=self.app.game.command(self.instance,account,self.java)
        self.assertNotIn("-Xms256M",command)
        self.assertNotIn("-Dfile.encoding=UTF-8",command)
        self.assertEqual([value for value in command if value.startswith("-Xmx")],["-Xmx3G"])
        for required in ("-Drequired=true","-cp","-Dglobal=true","-Dcustom=true","Main"):
            self.assertIn(required,command)

    def test_java_compatibility_bypass_uses_the_valid_selected_executable(self):
        self.app.store.save({"java_auto":False})
        self.update(java_path=self.java)
        candidates=[{"path":self.java,"major":8,"version":"1.8"}]
        with patch.object(self.app.game,"java_candidates",return_value=candidates):
            with self.assertRaises(LauncherError):
                self.app.game.ensure_java(17,Task("test",None),self.instance)
            self.update(skip_java_check=True)
            self.assertEqual(self.app.game.ensure_java(17,Task("test",None),self.instance),self.java)
            self.update(java_path="")
            self.assertEqual(self.app.game.ensure_java(17,Task("test",None),self.instance),self.java)
        self.update(java_path=str(self.root/"missing.exe"))
        with self.assertRaises(LauncherError):
            self.app.game.ensure_java(17,Task("test",None),self.instance)

    def test_native_artifact_rules_follow_selected_java_unless_adaptation_disabled(self):
        x86=java_fixture(self.root/"java32.exe",0x14c)
        arm=java_fixture(self.root/"java-arm.exe",0xaa64)
        self.assertEqual(java_architecture(x86),"x86")
        self.assertEqual(java_architecture(arm),"arm64")
        profile={"libraries":[{"name":"org.lwjgl:lwjgl:3.3.3:natives-windows"+("-x86" if arch=="x86" else ""),
            "rules":[{"action":"allow","os":{"name":"windows","arch":arch}}],
            "downloads":{"artifact":{"path":arch+".jar","url":"https://example.com/"+arch+".jar"}}}
            for arch in ("x86","x86_64")]}
        original=copy.deepcopy(profile)
        with patch("launcher.game.platform.machine",return_value="AMD64"),patch("launcher.game.platform.system",return_value="Windows"):
            self.assertEqual(self.app.game.libraries(profile,self.instance,x86)[0]["path"],"x86.jar")
            self.update(skip_native_patch=True)
            self.assertEqual(self.app.game.libraries(profile,self.instance,x86)[0]["path"],"x86_64.jar")
        self.assertEqual(profile,original)

    def test_native_extraction_keeps_existing_files_when_automatic_replacement_disabled(self):
        path=self.app.game.mc/"libraries/native.zip"
        path.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(path,"w") as archive:
            archive.writestr("glfw.dll",b"official")
        natives=self.app.store.instance_dir(self.instance["id"])/"natives"
        natives.mkdir()
        (natives/"glfw.dll").write_bytes(b"custom")
        libraries=[{"path":"native.zip","url":"https://example.com/native.zip","native":True}]
        self.update(skip_native_patch=True)
        with patch.object(self.app.game,"libraries",return_value=libraries),patch.object(self.app.game,"download_batch"):
            self.app.game.install_files(self.instance,Task("test",None))
            self.assertEqual((natives/"glfw.dll").read_bytes(),b"custom")
            self.update(skip_native_patch=False)
            self.app.game.install_files(self.instance,Task("test",None))
            self.assertEqual((natives/"glfw.dll").read_bytes(),b"official")

    def test_renderer_download_is_pinned_and_cancellation_does_not_try_another_source(self):
        self.update(renderer="zink")
        with patch("launcher.launch_options.platform.system",return_value="Windows"),patch("launcher.launch_options.platform.version",return_value="10.0.22631"):
            with patch.object(self.app.net,"download",side_effect=[LauncherError("fixture"),None]) as download:
                prepare_renderer(self.app.game,self.instance,self.java,Task("test",None))
                self.assertEqual(download.call_count,2)
                self.assertEqual(download.call_args.kwargs["sha1"],MESA["x64"][0])
                self.assertEqual(download.call_args.kwargs["size"],MESA["x64"][1])
            with patch.object(self.app.net,"download",side_effect=Cancelled("fixture")) as download:
                with self.assertRaises(Cancelled):
                    prepare_renderer(self.app.game,self.instance,self.java,Task("test",None))
                self.assertEqual(download.call_count,1)
        self.assertEqual(renderer_arguments(self.app.store,self.instance,self.java,self.root/"run")[-1],"-javaagent:"+str(mesa_path(self.app.store,self.java))+"=zink")

    def test_renderer_rejects_unsupported_system_and_default_needs_no_download(self):
        with patch.object(self.app.net,"download") as download:
            prepare_renderer(self.app.game,self.instance,self.java,Task("test",None))
            self.update(renderer="d3d12")
            with patch("launcher.launch_options.platform.system",return_value="Linux"),self.assertRaises(LauncherError):
                prepare_renderer(self.app.game,self.instance,self.java,Task("test",None))
            download.assert_not_called()

    def test_game_check_switch_controls_launch_verification_and_runs_keep_private_natives(self):
        self.app.store.update_instance(self.instance["id"],{"ready":True})
        directory=self.app.store.instance_dir(self.instance["id"])
        (directory/"natives").mkdir()
        (directory/"natives/custom.dll").write_bytes(b"keep")
        def spawn(command,**kwargs):
            child=ControlledProcess(5200+len(self.children),command)
            self.children.append(child)
            return child
        with patch.object(self.app.game,"ensure_java",return_value=self.java),patch.object(self.app.game,"install_files") as verify, \
             patch.object(self.app.game.skins,"prepare",return_value=None),patch("launcher.game.subprocess.Popen",side_effect=spawn):
            first=self.app.game.launch(self.instance["id"],self.account["id"],Task("test",None))
            self.children[0].finish()
            deadline=time.time()+4
            while time.time()<deadline and any(not item.get("finalized") for item in self.app.game.process_status()):
                time.sleep(.01)
            self.update(skip_game_check=True)
            second=self.app.game.launch(self.instance["id"],self.account["id"],Task("test",None))
        self.assertEqual(verify.call_count,1)
        self.assertNotEqual(first["process_id"],second["process_id"])
        for runtime in (directory/"runs").iterdir():
            self.assertEqual((runtime/"natives/custom.dll").read_bytes(),b"keep")

    def test_skip_game_check_never_marks_an_incomplete_pack_ready(self):
        self.update(skip_game_check=True)
        with patch.object(self.app.game,"install") as install,patch("launcher.game.subprocess.Popen") as spawn:
            with self.assertRaises(LauncherError):
                self.app.game.launch(self.instance["id"],self.account["id"],Task("test",None))
            install.assert_called_once()
            spawn.assert_not_called()


if __name__=="__main__":
    unittest.main()
