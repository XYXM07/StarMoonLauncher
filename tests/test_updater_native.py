"""Update UI uses mocked release data; never replaces the developer's EXE."""
import unittest
from unittest.mock import Mock, patch

import test_native as native
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from launcher.core import LauncherError, Task
from launcher.i18n import set_language
from launcher.update_ui import UpdateDialog
from launcher.updater import parse_release
from launcher.widgets import Button
from test_updater import release_data


class UpdateUiTests(unittest.TestCase):
    def setUp(self):
        native.NativeTests.setUp(self)
        self.service.updater.current_version = "0.7.5"  # UI simulates an older client.

    def tearDown(self):
        native.NativeTests.tearDown(self)
        set_language("zh_CN")

    def test_update_setting_is_live_and_about_opens_manual_check(self):
        self.window.set_settings_category("advanced")
        checkbox = self.window.settings_fields["auto_check_updates"]
        self.assertTrue(checkbox.isChecked())
        checkbox.setChecked(False)
        self.assertFalse(self.service.store.config()["auto_check_updates"])
        self.assertIs(self.window.collect_settings()["auto_check_updates"], False)
        self.window.navigate("about", animated=False)
        info = parse_release(release_data("0.7.5"), "0.7.5")
        with patch.object(self.service.updater, "check", return_value=info):
            button = self.window.page.findChild(Button, "check-launcher-updates")
            QTest.mouseClick(button, Qt.LeftButton)
            native.wait_for(lambda: any(isinstance(dialog, UpdateDialog) and not dialog.checking for dialog in self.window.dialogs))
        dialog = next(dialog for dialog in self.window.dialogs if isinstance(dialog, UpdateDialog))
        self.assertIn("最新正式版", dialog.status.text())
        self.assertFalse(dialog.action.isEnabled())
        self.assertIs(self.window.open_launcher_updates(), dialog)

    def test_release_notes_and_update_controls_support_english(self):
        self.service.config_update({"language": "en_US"})
        self.window.apply_ui_language()
        info = parse_release(release_data(), "0.7.5")
        info["notes"] = "<script>this is displayed as plain text</script>"
        dialog = self.window.open_launcher_updates(info)
        self.assertEqual(dialog.windowTitle(), "Launcher updates")
        self.assertEqual(dialog.action.text(), "Download update")
        self.assertEqual(dialog.notes.toPlainText(), info["notes"])
        self.assertTrue(dialog.notes.isReadOnly())
        self.assertNotRegex(dialog.status.text(), r"[\u4e00-\u9fff]")

    def test_download_completion_is_ready_but_source_runs_do_not_install(self):
        info = parse_release(release_data(), "0.7.5")
        self.service.updater.latest = info
        ready = {"path": "cached.exe", "version": "0.7.6", "sha256": "a" * 64, "size": 128}
        def downloaded(task):
            self.service.updater.ready = ready
            return dict(ready, launcher_update_ready=True)
        dialog = self.window.open_launcher_updates(info)
        with patch.object(self.service.updater, "download", side_effect=downloaded):
            QTest.mouseClick(dialog.action, Qt.LeftButton)
            native.wait_for(lambda: self.service.updater.ready is not None and dialog.task_id is None)
        self.assertEqual(dialog.action.text(), "安装并重启")
        self.assertFalse(dialog.action.isEnabled())
        self.assertIn("源码运行", dialog.status.text())

    def test_install_is_blocked_by_any_game_task_or_pending_signin(self):
        with patch.object(self.service.game, "process_status", return_value=[{"running": True, "finalized": False}]):
            self.assertIn("游戏", self.window.update_block_reason())
            failed = Mock()
            with patch.object(self.service.updater, "prepare") as prepare:
                self.window.install_launcher_update(failed)
                prepare.assert_not_called()
                failed.assert_called_once()
        task = Task("Other download", None)
        task.status = "running"
        self.service.tasks.tasks[task.id] = task
        reason = self.window.update_block_reason()
        task.status = "completed"
        task.finished = 1
        self.assertIn("任务", reason)
        self.service.accounts.sessions["test"] = {"status": "pending"}
        self.assertIn("登录", self.window.update_block_reason())
        self.service.accounts.sessions.clear()
        self.assertEqual(self.window.update_block_reason(), "")

    def test_install_starts_helper_before_closing_and_never_executes_shell_scripts(self):
        failed = Mock()
        with patch.object(self.service.updater, "prepare", return_value=("install.json", "helper.exe")), patch("launcher.update_install.launch_executable") as launch, patch.object(self.window, "close") as close:
            self.window.install_launcher_update(failed)
            native.wait_for(lambda: close.called)
            launch.assert_called_once_with("helper.exe", ["--apply-update", "install.json"])
            self.assertTrue(self.window._installing_update)
            failed.assert_not_called()
        self.window._installing_update = False

    def test_failed_settings_save_keeps_the_launcher_running(self):
        failed = Mock()
        with patch.object(self.window, "flush_settings", return_value=False), patch.object(self.service.updater, "prepare") as prepare:
            self.window.install_launcher_update(failed)
            prepare.assert_not_called()
            failed.assert_called_once()
        self.assertFalse(self.window._installing_update)

    def test_background_check_is_quiet_on_failure_and_respects_disable_setting(self):
        with patch.object(self.service.updater, "check", side_effect=LauncherError("network unavailable")) as check:
            self.window.startup_updates()
            native.wait_for(lambda: not self.window._update_checking)
            check.assert_called_once()
        self.assertFalse(any(isinstance(dialog, UpdateDialog) for dialog in self.window.dialogs))
        self.service.config_update({"auto_check_updates": False})
        with patch.object(self.service.updater, "check") as check:
            self.window.startup_updates()
            native.APP.processEvents()
            check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
