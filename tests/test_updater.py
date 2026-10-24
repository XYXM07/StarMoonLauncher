"""Update transactions touch synthetic EXEs and isolated data only."""
import copy
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from launcher.core import Cancelled, LauncherError, Task, atomic_json
from launcher.service import Application
from launcher.update_install import apply_install, load_plan, move_when_unlocked, prepare_install, sha256_file, validate_executable, wait_for_parent
from launcher.updater import CHECK_INTERVAL, LATEST_URL, RELEASES_URL, UpdateProgress, asset_url, parse_release, read_url, version_key


def pe_bytes(value=b"new launcher"):
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:70] = b"PE\0\0d\x86"
    return bytes(data) + value


def release_data(version="0.7.6", payload=None):
    payload = pe_bytes() if payload is None else payload
    name, tag = "StarMoonLauncher-" + version + "-windows-x64.exe", "v" + version
    return {"tag_name": tag, "html_url": RELEASES_URL + "/tag/" + tag, "draft": False, "prerelease": False,
            "body": "Release notes", "assets": [{"name": name, "state": "uploaded", "size": len(payload),
                "digest": "sha256:" + hashlib.sha256(payload).hexdigest(), "browser_download_url": asset_url(tag, name)}]}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.app = Application(self.root / "data")
        self.manager = self.app.updater
        self.manager.current_version = "0.7.5"  # Stable legacy-client fixture.

    def tearDown(self):
        self.temp.cleanup()

    def ready(self):
        source = self.manager.cache / "packages/0.7.6/new.exe"
        source.parent.mkdir(parents=True)
        source.write_bytes(pe_bytes())
        return {"path": str(source), "sha256": sha256_file(source), "size": source.stat().st_size, "version": "0.7.6"}

    def transaction(self):
        target = self.root / "星月启动器.exe"
        target.write_bytes(pe_bytes(b"old launcher"))
        ready = self.ready()
        plan, helper = prepare_install(self.manager.cache, target, ready, self.app.store.root, parent_pid=12345)
        return target, ready, plan, helper

    def checked(self, release=None):
        release = release_data() if release is None else release
        with patch("launcher.updater.read_url", return_value=json.dumps(release).encode()):
            return self.manager.check()

    def download_engine(self, payload):
        def fetch(url, target, task, **kwargs):
            target.write_bytes(payload)
            task.add_bytes(len(payload))
            return len(payload)
        return fetch

    def test_version_order_does_not_downgrade_or_compare_lexically(self):
        self.assertGreater(version_key("v0.7.10"), version_key("0.7.9"))
        self.assertEqual(version_key("0.7.5"), version_key("0.7.5.0"))
        for version in ("0.7.5", "0.7.4"):
            self.assertFalse(parse_release(release_data(version), "0.7.5")["available"])
        for version in ("0.7.6-beta", "../../0.7.6", "latest", "1.2"):
            with self.assertRaises(LauncherError):
                version_key(version)

    def test_release_validation_rejects_preview_foreign_hosts_and_wrong_architecture(self):
        cases = []
        for flag in ("draft", "prerelease"):
            data = release_data()
            data[flag] = True
            cases.append(data)
        data = release_data()
        data["html_url"] = "https://example.invalid/releases/tag/v0.7.6"
        cases.append(data)
        data = release_data()
        data["assets"][0]["browser_download_url"] = "https://github.com/other/repo/releases/download/v0.7.6/app.exe"
        cases.append(data)
        data = release_data()
        data["assets"][0]["name"] = "StarMoonLauncher-0.7.6-windows-arm64.exe"
        cases.append(data)
        data = release_data()
        data["assets"][0]["size"] = 1024 ** 3
        cases.append(data)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(LauncherError):
                parse_release(data, "0.7.5")

    def test_check_uses_public_latest_endpoint_and_cooldown_is_persistent(self):
        with patch("launcher.updater.read_url", return_value=json.dumps(release_data()).encode()) as fetch, patch("launcher.updater.time.time", return_value=1000000):
            info = self.manager.check()
        fetch.assert_called_once_with(LATEST_URL, api=True)
        self.assertTrue(info["available"])
        self.assertFalse(self.manager.startup_due(1000001))
        self.assertTrue(self.manager.startup_due(1000000 + CHECK_INTERVAL))
        self.assertTrue(self.manager.startup_due(100))  # Clock moved backwards.
        self.app.config_update({"auto_check_updates": False})
        self.assertFalse(Application(self.app.store.root).updater.startup_due())

    def test_checksum_fallback_requires_one_exact_filename_match(self):
        data = release_data()
        digest = data["assets"][0].pop("digest")[7:]
        data["assets"].append({"name": "SHA256SUMS.txt", "state": "uploaded", "browser_download_url": asset_url("v0.7.6", "SHA256SUMS.txt")})
        line = digest + "  " + data["assets"][0]["name"] + "\n"
        for body, valid in ((line, True), (line * 2, False), (digest + "  other.exe", False)):
            with patch("launcher.updater.read_url", side_effect=[json.dumps(data).encode(), body.encode()]):
                if valid:
                    self.assertEqual(self.manager.check()["sha256"], digest)
                else:
                    with self.assertRaises(LauncherError):
                        self.manager.check()
        data["assets"].pop()
        with self.assertRaises(LauncherError):
            parse_release(data, "0.7.5")

    def test_missing_release_is_normal_but_missing_checksum_is_an_error(self):
        error = urllib.error.HTTPError(LATEST_URL, 404, "missing", {}, io.BytesIO())
        with patch("launcher.updater.read_url", side_effect=error):
            self.assertFalse(self.manager.check()["available"])
        data = release_data()
        data["assets"][0].pop("digest")
        checksum_url = asset_url("v0.7.6", "SHA256SUMS.txt")
        data["assets"].append({"name": "SHA256SUMS.txt", "state": "uploaded", "browser_download_url": checksum_url})
        with patch("launcher.updater.read_url", side_effect=[json.dumps(data).encode(), urllib.error.HTTPError(checksum_url, 404, "missing", {}, io.BytesIO())]):
            with self.assertRaises(LauncherError):
                self.manager.check()

    def test_metadata_read_is_bounded_and_rejects_http_redirect(self):
        class Response(io.BytesIO):
            def geturl(self):
                return self.url
        for payload, url in ((b"x" * 12, "https://api.github.com/test"), (b"{}", "http://example.invalid")):
            response = Response(payload)
            response.url = url
            with patch("launcher.updater.urllib.request.urlopen", return_value=response):
                with self.assertRaises(LauncherError):
                    read_url(LATEST_URL, limit=10)

    def test_verified_download_uses_existing_parallel_engine_and_cache(self):
        self.checked()
        with patch.object(self.app.net.engine, "fetch", side_effect=self.download_engine(pe_bytes())) as fetch:
            result = self.manager.download(Task("Update", None))
            again = self.manager.download(Task("Cached update", None))
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(result["launcher_update_ready"])
        self.assertEqual(result["path"], again["path"])
        self.assertEqual(Path(result["path"]).read_bytes(), pe_bytes())

    def test_bad_hash_or_executable_never_becomes_ready_and_cleans_partial_files(self):
        for payload, expected in ((pe_bytes(b"evil content"), pe_bytes()), (b"invalid program", b"invalid program")):
            self.checked(release_data(payload=expected))
            with patch.object(self.app.net.engine, "fetch", side_effect=self.download_engine(payload)):
                with self.assertRaises(LauncherError):
                    self.manager.download(Task("Bad update", None))
            self.assertIsNone(self.manager.ready)
            self.assertFalse(list(self.manager.cache.rglob("*.part-*")))

    def test_cancelled_download_preserves_launcher_and_data(self):
        self.checked()
        task = Task("Cancelled update", None)
        sentinel = self.root / "existing.exe"
        sentinel.write_bytes(b"original")
        def fetch(url, target, progress, **kwargs):
            target.write_bytes(b"partial")
            task.cancel.set()
            progress.check()
        with patch.object(self.app.net.engine, "fetch", side_effect=fetch), self.assertRaises(Cancelled):
            self.manager.download(task)
        self.assertEqual(sentinel.read_bytes(), b"original")
        self.assertFalse(list(self.manager.cache.rglob("*.part-*")))
        self.assertIsNone(self.manager.ready)

    def test_progress_enforces_expected_size_even_without_content_length(self):
        progress = UpdateProgress(Mock(), 10)
        progress.add_bytes(8)
        with self.assertRaises(LauncherError):
            progress.add_bytes(3)
        progress.reset_download()
        progress.add_bytes(10)
        with self.assertRaises(LauncherError):
            progress.set_download_size(11)

    def test_source_mode_cannot_launch_an_installer(self):
        self.manager.ready = self.ready()
        with self.assertRaises(LauncherError):
            self.manager.prepare(frozen=False)

    def test_prepare_does_not_replace_current_exe_or_touch_accounts_and_worlds(self):
        account = self.app.accounts.offline("KeepPlayer")
        world = self.root / ".Minecraft/saves/World/level.dat"
        world.parent.mkdir(parents=True)
        world.write_bytes(b"world data")
        target, ready, plan, helper = self.transaction()
        self.assertEqual(target.read_bytes(), pe_bytes(b"old launcher"))
        self.assertEqual(helper.read_bytes(), target.read_bytes())
        self.assertEqual(load_plan(plan, helper)["data_root"], str(self.app.store.root))
        self.assertEqual(self.app.store.config()["selected_account"], account["id"])
        self.assertEqual(world.read_bytes(), b"world data")

    def test_replace_waits_for_parent_preserves_backup_and_relaunches_same_data_root(self):
        target, ready, plan, helper = self.transaction()
        config = self.app.store.path.read_bytes() if self.app.store.path.exists() else None
        events = []
        def wait(pid):
            self.assertEqual(target.read_bytes(), pe_bytes(b"old launcher"))
            events.append("wait")
        def launch(exe, arguments):
            self.assertEqual(events, ["wait"])
            self.assertEqual(Path(exe).read_bytes(), pe_bytes())
            self.assertEqual(arguments, ["--data-dir", str(self.app.store.root)])
            events.append("launch")
        self.assertTrue(apply_install(plan, helper, wait=wait, launch=launch))
        receipt = json.loads((self.manager.cache / "last-install.json").read_text(encoding="utf-8"))
        self.assertEqual(Path(receipt["backup"]).read_bytes(), pe_bytes(b"old launcher"))
        self.assertEqual(receipt["status"], "updated")
        if config is not None:
            self.assertEqual(self.app.store.path.read_bytes(), config)
        self.assertFalse(list(self.root.glob("*.new")))

    def test_replace_failure_rolls_back_and_reopens_old_launcher(self):
        target, ready, plan, helper = self.transaction()
        replace = os.replace
        def fail_commit(source, destination):
            if str(source).endswith(".new") and Path(destination) == target:
                raise OSError("simulated disk failure")
            return replace(source, destination)
        launch = Mock()
        with patch("launcher.update_install.os.replace", side_effect=fail_commit):
            self.assertFalse(apply_install(plan, helper, wait=lambda pid: None, launch=launch))
        self.assertEqual(target.read_bytes(), pe_bytes(b"old launcher"))
        launch.assert_called_once_with(target, ["--data-dir", str(self.app.store.root)])
        self.assertTrue(self.manager.take_outcome()["restored"])
        self.assertIsNone(self.manager.take_outcome())

    def test_immediate_relaunch_failure_rolls_back_the_new_binary(self):
        target, ready, plan, helper = self.transaction()
        launch = Mock(side_effect=[OSError("cannot start"), None])
        self.assertFalse(apply_install(plan, helper, wait=lambda pid: None, launch=launch))
        self.assertEqual(target.read_bytes(), pe_bytes(b"old launcher"))
        self.assertEqual(launch.call_count, 2)

    def test_tampered_download_or_target_never_overwrites_unexpected_files(self):
        target, ready, plan, helper = self.transaction()
        Path(ready["path"]).write_bytes(pe_bytes(b"tampered download"))
        launch = Mock()
        self.assertFalse(apply_install(plan, helper, wait=lambda pid: None, launch=launch))
        launch.assert_not_called()
        Path(ready["path"]).write_bytes(pe_bytes())
        target.write_bytes(pe_bytes(b"another installed version"))
        self.assertFalse(apply_install(plan, helper, wait=lambda pid: None, launch=launch))
        self.assertEqual(target.read_bytes(), pe_bytes(b"another installed version"))
        launch.assert_not_called()

    def test_wait_timeout_keeps_old_executable_and_does_not_launch_a_second_copy(self):
        target, ready, plan, helper = self.transaction()
        launch = Mock()
        with patch("launcher.update_install.wait_for_parent"):
            self.assertFalse(apply_install(plan, helper, wait=Mock(side_effect=LauncherError("timeout")), launch=launch))
        self.assertEqual(target.read_bytes(), pe_bytes(b"old launcher"))
        launch.assert_not_called()

    def test_install_plan_rejects_paths_outside_its_update_cache(self):
        target, ready, plan, helper = self.transaction()
        data = json.loads(plan.read_text(encoding="utf-8"))
        for changes in ({"source": str(self.root / "outside.exe")}, {"target": str(self.manager.cache / "other.exe")}, {"parent_pid": -1}, {"token": "../escape"}):
            with self.subTest(changes=changes):
                atomic_json(plan, dict(data, **changes))
                with self.assertRaises(LauncherError):
                    load_plan(plan, helper)
        self.assertEqual(target.read_bytes(), pe_bytes(b"old launcher"))

    def test_sharing_violation_is_retried_until_the_old_exe_is_unlocked(self):
        source, target = self.root / "source", self.root / "target"
        source.write_bytes(b"replacement")
        real_replace = os.replace
        attempts = []
        def replace(a, b):
            attempts.append(1)
            if len(attempts) == 1:
                raise PermissionError("locked")
            return real_replace(a, b)
        with patch("launcher.update_install.os.replace", side_effect=replace), patch("launcher.update_install.time.sleep"):
            move_when_unlocked(source, target)
        self.assertEqual(target.read_bytes(), b"replacement")
        self.assertEqual(len(attempts), 2)

    @unittest.skipUnless(os.name == "nt", "Windows process synchronization")
    def test_windows_waits_for_an_owned_test_process(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(.1)"], creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            wait_for_parent(process.pid, timeout=5)
            self.assertEqual(process.wait(timeout=2), 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
