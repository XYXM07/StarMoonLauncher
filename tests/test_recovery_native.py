"""Real native recovery dialogs: consent, explicit selection, retry and cancellation."""
import hashlib
import json
import zipfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_native as native
from test_ai_downloads import mod_jar
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QRadioButton
from launcher.core import LauncherError
from launcher.i18n import set_language


class RecoveryUiTests(unittest.TestCase):
    def setUp(self):
        native.NativeTests.setUp(self)
        self.ai = self.service.content.ai_downloads
        self.payload = mod_jar()
        self.job = {"path": "mods/example.jar", "target": self.root / "game/mods/example.jar", "size": len(self.payload),
                    "sha1": hashlib.sha1(self.payload).hexdigest()}
        self.options = [{"id": "first", "url": "https://cdn.modrinth.com/data/one/example.jar", "filename": "example.jar",
                         "version": "1.0-fabric", "name": "Example Release", "provider": "Modrinth", "game_versions": ["1.21.1"],
                         "loaders": ["fabric"], "source": "https://www.mcmod.cn/class/2361.html"},
                        {"id": "second", "url": "https://github.com/Author/Example/releases/download/v1/example.jar", "filename": "example.jar",
                         "version": "v1.0", "name": "Author release", "provider": "GitHub", "game_versions": [], "loaders": [],
                         "project_url": "https://github.com/Author/Example/releases"}]
        self.search_patch_ai = patch.object(self.ai, "candidates", return_value=self.options)
        self.search = self.search_patch_ai.start()
        self.download_patch_ai = patch.object(self.service.net, "download", side_effect=self.download)
        self.remote = self.download_patch_ai.start()
        self.window.timer.start(30)

    def tearDown(self):
        for task in self.service.tasks.tasks.values():
            task.cancel.set()
        native.wait_for(lambda: all(task.finished for task in self.service.tasks.tasks.values()))
        self.download_patch_ai.stop()
        self.search_patch_ai.stop()
        native.NativeTests.tearDown(self)
        set_language("zh_CN")

    def download(self, url, target, task=None, **kwargs):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.payload)
        task.reset_download()
        task.add_bytes(len(self.payload))
        task.complete_download(len(self.payload))

    def start(self):
        result = self.service.tasks.submit("Install pack", lambda task: self.ai.recover(self.job,
            {"game_version": "1.21.1", "loader": "fabric", "loader_version": "0.16.5"}, task))
        self.task = self.service.tasks.tasks[result["task_id"]]
        self.wait_dialog("ai-recovery-confirm")
        return self.window.recovery_dialog

    def wait_dialog(self, name):
        native.wait_for(lambda: self.window.recovery_dialog and self.window.recovery_dialog.objectName() == name)
        return self.window.recovery_dialog

    def approve_search(self):
        QTest.mouseClick(self.window.recovery_dialog.submit, Qt.LeftButton)
        return self.wait_dialog("ai-recovery-select")

    def test_no_ai_or_download_before_consent_and_no_download_before_version_selection(self):
        self.start()
        self.search.assert_not_called()
        self.remote.assert_not_called()
        dialog = self.approve_search()
        self.assertFalse(dialog.submit.isEnabled())
        self.assertEqual(len(dialog.findChildren(QRadioButton)), 2)
        self.remote.assert_not_called()
        radio = dialog.findChild(QRadioButton, "ai-recovery-choice-second")
        dialog.scroll.ensureWidgetVisible(radio)
        QTest.mouseClick(radio, Qt.LeftButton)
        self.assertTrue(dialog.submit.isEnabled())
        QTest.mouseClick(dialog.submit, Qt.LeftButton)
        native.wait_for(lambda: bool(self.task.finished))
        self.assertEqual(self.task.status, "completed")
        self.assertEqual(self.remote.call_args.args[0], self.options[1]["url"])
        self.assertEqual(self.remote.call_count, 1)
        self.assertEqual(self.job["target"].read_bytes(), self.payload)
        self.assertTrue(self.task.recovery_results[-1]["success"])

    def test_cancel_confirmation_never_calls_model_or_downloads(self):
        dialog = self.start()
        dialog.reject()
        native.wait_for(lambda: bool(self.task.finished))
        self.assertEqual(self.task.status, "cancelled")
        self.search.assert_not_called()
        self.remote.assert_not_called()

    def test_closing_picker_cancels_without_downloading(self):
        self.start()
        dialog = self.approve_search()
        dialog.reject()
        native.wait_for(lambda: bool(self.task.finished))
        self.assertEqual(self.task.status, "cancelled")
        self.remote.assert_not_called()

    def test_activity_cancel_closes_pending_dialog_and_task_finishes(self):
        self.start()
        self.service.tasks.cancel_task(self.task.id)
        native.wait_for(lambda: self.window.recovery_dialog is None)
        native.wait_for(lambda: bool(self.task.finished))
        self.assertEqual(self.task.status, "cancelled")
        self.remote.assert_not_called()

    def test_failed_selected_download_requires_another_explicit_selection(self):
        def download(url, *args, **kwargs):
            if url == self.options[0]["url"]:
                raise LauncherError("404")
            return self.download(url, *args, **kwargs)
        self.remote.side_effect = download
        self.start()
        first = self.approve_search()
        first.findChild(QRadioButton, "ai-recovery-choice-first").setChecked(True)
        QTest.mouseClick(first.submit, Qt.LeftButton)
        native.wait_for(lambda: self.window.recovery_dialog is not None and self.window.recovery_dialog is not first)
        second = self.window.recovery_dialog
        self.assertFalse(second.submit.isEnabled())
        self.assertEqual(self.remote.call_count, 1)
        self.assertFalse(self.task.recovery_results[-1]["success"])
        self.assertEqual(len(second.findChildren(QRadioButton)), 1)
        second.findChild(QRadioButton, "ai-recovery-choice-second").setChecked(True)
        QTest.mouseClick(second.submit, Qt.LeftButton)
        native.wait_for(lambda: bool(self.task.finished))
        self.assertEqual(self.task.status, "completed")
        self.assertEqual(self.remote.call_count, 2)
        self.assertTrue(self.task.recovery_results[-1]["success"])

    def test_recovery_dialogs_support_english(self):
        self.service.config_update({"language": "en_US"})
        set_language("en_US")
        confirm = self.start()
        self.assertEqual(confirm.windowTitle(), "Modpack file download failed")
        self.assertEqual(confirm.submit.text(), "Find versions with AI")
        selection = self.approve_search()
        self.assertEqual(selection.windowTitle(), "Choose a recovery version")
        self.assertEqual(selection.submit.text(), "Download selected version")
        selection.reject()
        native.wait_for(lambda: bool(self.task.finished))

    def test_full_modpack_import_stays_incomplete_until_user_selects_verified_file(self):
        archive = self.root / "recovery.mrpack"
        index = {"formatVersion": 1, "game": "minecraft", "name": "Interactive fixture",
                 "dependencies": {"minecraft": "1.21.1", "fabric-loader": "0.16.5"},
                 "files": [{"path": "mods/example.jar", "downloads": ["https://original.invalid/example.jar"],
                            "hashes": {"sha1": self.job["sha1"]}, "fileSize": len(self.payload)}]}
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("modrinth.index.json", json.dumps(index))
            output.writestr("overrides/config/fixture.txt", "preserved")
        def download(url, *args, **kwargs):
            if url.startswith("https://original.invalid/"):
                raise LauncherError("404")
            return self.download(url, *args, **kwargs)
        self.remote.side_effect = download
        with patch.object(self.service.game, "install"):
            result = self.service.tasks.submit("Import pack", lambda task: self.service.content.import_pack(archive, task))
            self.task = self.service.tasks.tasks[result["task_id"]]
            self.wait_dialog("ai-recovery-confirm")
            incomplete = next(item for item in self.service.store.instances() if item.get("pack_install_pending"))
            self.assertFalse(incomplete["ready"])
            self.search.assert_not_called()
            selection = self.approve_search()
            selection.findChild(QRadioButton, "ai-recovery-choice-first").setChecked(True)
            QTest.mouseClick(selection.submit, Qt.LeftButton)
            native.wait_for(lambda: bool(self.task.finished))
        self.assertEqual(self.task.status, "completed")
        installed = self.service.store.instance(self.task.result["instance_id"])
        self.assertTrue(installed["ready"])
        self.assertFalse(installed["pack_install_pending"])
        directory = self.service.store.game_dir(installed["id"])
        self.assertEqual((directory / "mods/example.jar").read_bytes(), self.payload)
        self.assertEqual((directory / "config/fixture.txt").read_text(), "preserved")

    def test_failed_ai_results_are_verified_then_popup_and_clean_new_pack(self):
        old = self.service.game.create("Keep", "1.21.1")
        world = self.service.store.game_dir(old["id"]) / "saves/World/level.dat"
        world.parent.mkdir(parents=True)
        world.write_bytes(b"keep-world")
        archive = self.root / "failed-ai.mrpack"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("modrinth.index.json", json.dumps({"formatVersion": 1, "game": "minecraft", "name": "Failed pack",
                "dependencies": {"minecraft": "1.21.1", "fabric-loader": "0.16.5"}, "files": [{"path": "mods/example.jar",
                    "downloads": ["https://original.invalid/example.jar"], "hashes": {"sha1": self.job["sha1"]}, "fileSize": len(self.payload)}]}))
        def download(url, target, task=None, **kwargs):
            if url.startswith("https://original.invalid/"):
                raise LauncherError("404")
            Path(target).write_bytes(b"wrong bytes")
            task.complete_download(11)
        self.remote.side_effect = download
        with patch.object(self.service.game, "install"):
            result = self.service.tasks.submit("Import pack", lambda task: self.service.content.import_file(archive, task))
            self.task = self.service.tasks.tasks[result["task_id"]]
            self.wait_dialog("ai-recovery-confirm")
            self.search.assert_not_called()
            first = self.approve_search()
            first.findChild(QRadioButton, "ai-recovery-choice-first").setChecked(True)
            QTest.mouseClick(first.submit, Qt.LeftButton)
            native.wait_for(lambda: self.window.recovery_dialog is not None and self.window.recovery_dialog is not first)
            second = self.window.recovery_dialog
            second.findChild(QRadioButton, "ai-recovery-choice-second").setChecked(True)
            QTest.mouseClick(second.submit, Qt.LeftButton)
            native.wait_for(lambda: self.window.pack_failure_dialog is not None)
        self.assertEqual(self.task.status, "failed")
        self.assertTrue(self.task.pack_install["cleaned"])
        self.assertEqual([item["success"] for item in self.task.recovery_results], [False, False])
        self.assertEqual([item["id"] for item in self.service.store.instances()], [old["id"]])
        self.assertEqual(world.read_bytes(), b"keep-world")
        self.assertTrue(archive.exists())


if __name__ == "__main__":
    unittest.main()
