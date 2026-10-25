"""Native file drops, failed-install cleanup popups and preserved original archives."""
import json
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import test_native as native
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QLabel
from launcher.core import LauncherError
from launcher.i18n import set_language
from launcher.pack_ui import PackFailureDialog


class PackNativeTests(unittest.TestCase):
    def setUp(self):
        native.NativeTests.setUp(self)
        self.window.timer.start(30)
        self.base_patch = patch.object(self.service.game, "install")
        self.base_install = self.base_patch.start()

    def tearDown(self):
        for task in self.service.tasks.tasks.values():
            task.cancel.set()
        native.wait_for(lambda: all(task.finished for task in self.service.tasks.tasks.values()))
        self.window.pack_failure_queue.clear()
        self.base_patch.stop()
        native.NativeTests.tearDown(self)
        set_language("zh_CN")

    def archive(self, name="Fixture", suffix=".mrpack"):
        path = self.root / (name + suffix)
        with zipfile.ZipFile(path, "w") as output:
            output.writestr("modrinth.index.json", json.dumps({"formatVersion": 1, "game": "minecraft", "name": name,
                "dependencies": {"minecraft": "1.21.1"}, "files": []}))
            output.writestr("overrides/config/settings.txt", "fixture")
        return path

    def drop(self, paths, target=None):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        target = target or self.window
        enter = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        native.APP.sendEvent(target, enter)
        self.assertTrue(enter.isAccepted())
        drop = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        native.APP.sendEvent(target, drop)
        self.assertTrue(drop.isAccepted())
        self.assertEqual(drop.dropAction(), Qt.CopyAction)

    def wait_tasks(self):
        native.wait_for(lambda: bool(self.service.tasks.tasks) and all(task.finished for task in self.service.tasks.tasks.values()))

    def test_drop_installs_archive_and_preserves_original_file(self):
        archive = self.archive()
        original = archive.read_bytes()
        self.drop([archive])
        self.wait_tasks()
        task = next(iter(self.service.tasks.tasks.values()))
        self.assertEqual(task.status, "completed")
        self.assertTrue(self.service.store.instance(task.result["instance_id"])["ready"])
        self.assertEqual(archive.read_bytes(), original)
        self.assertFalse(any((self.root / "cache/pack-sessions").iterdir()))

    def test_drop_over_settings_input_installs_instead_of_inserting_path(self):
        self.window.navigate("settings", animated=False)
        self.window.set_settings_category("ai")
        model = self.window.settings_fields["ai_model"]
        model.setText("fixture-model")
        self.drop([self.archive()], model)
        self.wait_tasks()
        self.assertEqual(self.service.store.config()["ai_model"], "fixture-model")
        self.assertEqual(self.window.page_id, "downloads")

    def test_multiple_drops_queue_distinct_files_and_deduplicate_same_drop(self):
        first, second = self.archive("One"), self.archive("Two", ".ZIP")
        self.drop([first, second, first])
        self.wait_tasks()
        self.assertEqual(len(self.service.tasks.tasks), 2)
        self.assertEqual({item["name"] for item in self.service.store.instances()}, {"One", "Two"})

    def test_unsupported_file_and_remote_url_do_not_start_installation(self):
        for url in (QUrl.fromLocalFile(str(self.root / "ordinary.txt")), QUrl("https://example.invalid/pack.mrpack")):
            mime = QMimeData()
            mime.setUrls([url])
            event = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            native.APP.sendEvent(self.window, event)
            self.assertFalse(event.isAccepted())
        self.assertFalse(self.service.tasks.tasks)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.root / "move-only.mrpack"))])
        event = QDragEnterEvent(QPoint(10, 10), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        native.APP.sendEvent(self.window, event)
        self.assertFalse(event.isAccepted())
        self.assertFalse(self.service.tasks.tasks)

    def test_failed_pack_popup_appears_after_cleanup_and_only_once(self):
        archive = self.archive()
        self.base_install.side_effect = LauncherError("fixture download failed")
        self.drop([archive])
        native.wait_for(lambda: self.window.pack_failure_dialog is not None)
        dialog = self.window.pack_failure_dialog
        task = next(iter(self.service.tasks.tasks.values()))
        self.assertEqual(task.status, "failed")
        self.assertTrue(task.pack_install["cleaned"], task.pack_install["cleanup_errors"])
        self.assertFalse(self.service.store.instances())
        self.assertTrue(archive.exists())
        text = " ".join(item.text() for item in dialog.findChildren(QLabel))
        self.assertIn("残留文件已清理", text)
        dialog.reject()
        self.window.refresh()
        self.assertIsNone(self.window.pack_failure_dialog)

    def test_corrupt_pack_failures_are_queued_one_popup_at_a_time(self):
        first, second = self.root / "broken-one.zip", self.root / "broken-two.mrpack"
        first.write_bytes(b"broken")
        second.write_bytes(b"broken")
        self.drop([first, second])
        self.wait_tasks()
        self.window.refresh()
        self.assertEqual(len([dialog for dialog in self.window.dialogs if isinstance(dialog, PackFailureDialog)]), 1)
        current = self.window.pack_failure_dialog
        current.reject()
        self.window.refresh()
        self.assertIsNotNone(self.window.pack_failure_dialog)
        self.assertIsNot(self.window.pack_failure_dialog, current)
        self.assertTrue(first.exists() and second.exists())

    def test_cleanup_warning_and_failure_dialog_support_english(self):
        self.service.config_update({"language": "en_US"})
        set_language("en_US")
        def failed(task):
            task.pack_install = {"status": "failed", "cleaned": False, "cleanup_errors": ["fixture file is locked"]}
            raise LauncherError("fixture install failed")
        self.service.tasks.submit("Fixture", failed)
        native.wait_for(lambda: self.window.pack_failure_dialog is not None)
        dialog = self.window.pack_failure_dialog
        self.assertEqual(dialog.windowTitle(), "Modpack installation failed")
        text = " ".join(item.text() for item in dialog.findChildren(QLabel))
        self.assertIn("could not be removed", text)
        self.assertIn("fixture file is locked", text)


if __name__ == "__main__":
    unittest.main()
