"""Language switching uses isolated Qt fixtures, never real accounts or disks."""
import re
import unittest
from unittest.mock import patch

import test_native as native

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QLineEdit, QRadioButton, QWidget

from launcher.desktop import InstallDialog, LauncherWindow, MicrosoftDialog, NativeDialog, PAGES, SkinDialog, short_count
from launcher.i18n import set_language
from launcher.widgets import Button, ElidedLabel


class LanguageUiTests(unittest.TestCase):
    def setUp(self):
        native.NativeTests.setUp(self)

    def tearDown(self):
        native.NativeTests.tearDown(self)
        set_language("zh_CN")

    def english(self):
        self.service.config_update({"language": "en_US"})
        self.window.apply_ui_language()

    def chinese_text(self, owner):
        found = []
        for widget in owner.findChildren(QWidget):
            if not isinstance(widget, (QLabel, Button, QCheckBox, QRadioButton)):
                continue
            text = widget.full_text if isinstance(widget, ElidedLabel) else widget.text()
            if re.search(r"[\u4e00-\u9fff]", text) and "星月晓梦" not in text and "中文世界" not in text:
                found.append((type(widget).__name__, widget.objectName(), text))
        for widget in owner.findChildren(QLineEdit):
            if re.search(r"[\u4e00-\u9fff]", widget.placeholderText()):
                found.append(("placeholder", widget.objectName(), widget.placeholderText()))
        return found

    def test_english_covers_all_pages_settings_categories_and_compact_header(self):
        instance = self.service.game.create("中文世界", "1.20.1", "fabric")
        self.service.accounts.offline("EnglishTest")
        self.window.refresh()
        self.english()
        self.window.resize(900, 600)
        for page_id, _, _ in PAGES:
            self.window.navigate(page_id, animated=False)
            QTest.qWait(40)
            self.assertEqual(self.chinese_text(self.window.page), [], page_id)
            self.assertEqual(self.chinese_text(self.window.sidebar), [], page_id)
        for category in ("game", "download", "account", "appearance", "ai", "advanced"):
            self.window.set_settings_category(category)
            QTest.qWait(20)
            self.assertEqual(self.chinese_text(self.window.page), [], category)
        self.assertEqual(self.window.windowTitle(), "StarMoonLauncher")
        close = self.window.titlebar.findChild(Button, "window-close")
        self.assertTrue(self.window.titlebar.rect().contains(close.geometry()))
        buttons = [self.window.top_buttons[key] for key in ("launch", "download", "settings", "more")]
        self.assertEqual([button.text() for button in buttons], ["Launch", "Download", "Settings", "More"])
        for button in buttons:
            self.assertLessEqual(button.fontMetrics().horizontalAdvance(button.text()) + 16, button.width())
        self.window.open_instance(instance["id"])
        for category in ("overview", "settings", "mods", "resourcepacks", "shaders", "saves", "logs"):
            self.window.set_instance_category(category)
            QTest.qWait(60)
            self.assertEqual(self.chinese_text(self.window.page), [], category)
            self.assertEqual(self.chinese_text(self.window.sidebar), [], category)

    def test_switch_is_live_persistent_and_preserves_user_data(self):
        instance = self.service.game.create("中文世界", "1.21.1", "vanilla")
        account = self.service.accounts.offline("KeepPlayer")
        self.window.refresh()
        self.window.set_settings_category("appearance")
        self.window.settings_fields["jvm_args"].setPlainText("-Dworld=中文世界")
        choice = self.window.settings_fields["language"]
        choice.setCurrentIndex(choice.findData("en_US"))
        native.wait_for(lambda: self.window.windowTitle() == "StarMoonLauncher" and self.window.settings_fields["language"] is not choice)
        self.assertEqual(self.window.settings_category, "appearance")
        self.assertEqual(self.service.store.config()["jvm_args"], "-Dworld=中文世界")
        self.assertEqual(self.service.store.config()["selected_account"], account["id"])
        self.assertEqual(self.service.store.instance(instance["id"])["name"], "中文世界")
        restarted = LauncherWindow(self.root, service=self.service)
        restarted.timer.stop()
        self.assertEqual(restarted.windowTitle(), "StarMoonLauncher")
        restarted.close()
        restarted.pool.waitForDone(5000)
        restarted.images.pool.waitForDone(10000)
        restarted.deleteLater()
        native.APP.processEvents()
        choice = self.window.settings_fields["language"]
        choice.setCurrentIndex(choice.findData("zh_CN"))
        native.wait_for(lambda: self.window.windowTitle() == "星月启动器")
        self.assertEqual(self.service.store.config()["language"], "zh_CN")

    def test_dialogs_skin_wallpaper_loaders_and_authentication_errors_are_english(self):
        account = self.service.accounts.offline("SkinPlayer")
        self.english()
        for dialog in (NativeDialog(self.window, "English test"), SkinDialog(self.window, account["id"]), InstallDialog(self.window, "1.20.1")):
            self.window.open_dialog(dialog)
            QTest.qWait(50)
            self.assertEqual(self.chinese_text(dialog), [])
            dialog.reject()
        wallpaper = self.window.choose_preset_background()
        self.assertTrue(all(not re.search(r"[\u4e00-\u9fff]", tile.name) for tile in wallpaper.findChildren(Button) if hasattr(tile, "name")))
        wallpaper.reject()
        with patch.object(self.window, "request"):
            auth = self.window.open_dialog(MicrosoftDialog(self.window))
            auth.failed("Minecraft 登录失败（HTTP 403 · Invalid app registration）\n接口：https://api.minecraftservices.com/authentication/login_with_xbox")
            self.assertIn("Minecraft sign-in failed", auth.error_label.text())
            self.assertNotRegex(auth.error_label.text(), r"[\u4e00-\u9fff]")
            auth.reject()

    def test_activity_translates_existing_task_messages_and_uses_english_counts(self):
        from launcher.core import Task
        task = Task("修复游戏文件", None)
        task.status = "running"
        task.update("校验 / 下载游戏文件", 20)
        self.service.tasks.tasks[task.id] = task
        self.english()
        self.window.navigate("downloads", animated=False)
        task.status = "completed"
        task.finished = 1
        self.assertEqual(self.window.task_rows[task.id][0].text(), "Verify / download game files")
        self.assertEqual(self.window.task_rows[task.id][1].text(), "Running")
        self.assertNotRegex(self.window.task_bar.text(), r"[\u4e00-\u9fff]")
        self.assertEqual(short_count(12000), "12.0K")
        self.assertEqual(short_count(1200000), "1.2M")


if __name__ == "__main__":
    unittest.main()
