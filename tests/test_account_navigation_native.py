"""Account management lives directly under Settings, with one canonical route."""
import unittest
from unittest.mock import patch

import test_native as native
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QLineEdit, QMessageBox
from launcher.i18n import set_language
from launcher.widgets import Button


class AccountNavigationTests(unittest.TestCase):
    def setUp(self):
        native.NativeTests.setUp(self)
        self.account = self.service.accounts.offline("ManagePlayer")
        self.window.refresh()
        self.window.timer.start(30)

    def tearDown(self):
        native.NativeTests.tearDown(self)
        set_language("zh_CN")

    def page_button(self, text):
        native.wait_for(lambda: any(button.text() == text and button.isVisible() for button in self.window.page.findChildren(Button)))
        return next(button for button in self.window.page.findChildren(Button) if button.text() == text and button.isVisible())

    def account_category(self):
        native.wait_for(lambda: any(button.isVisible() for button in self.window.sidebar.findChildren(Button, "settings-account")))
        return next(button for button in self.window.sidebar.findChildren(Button, "settings-account") if button.isVisible())

    def assert_account_settings(self):
        self.assertEqual(self.window.page_id, "settings")
        self.assertEqual(self.window.settings_category, "account")
        self.assertTrue(self.window.top_buttons["settings"].isChecked())
        self.assertTrue(self.account_category().primary)

    def test_more_has_only_activity_and_about_and_home_shortcut_opens_settings(self):
        QTest.mouseClick(next(button for button in self.window.sidebar.findChildren(Button) if button.text() == "账号管理"), Qt.LeftButton)
        self.assert_account_settings()
        self.assertTrue(any(label.text() == "ManagePlayer" for label in self.window.page.findChildren(QLabel)))
        self.assertFalse(any(button.text() == "账号管理" for button in self.window.page.findChildren(Button)))
        QTest.mouseClick(self.window.top_buttons["more"], Qt.LeftButton)
        self.assertEqual(self.window.page_id, "downloads")
        self.assertEqual(set(self.window.nav_buttons), {"downloads", "about"})
        self.assertFalse(any(button.text() == "账号管理" and button.isVisible() for button in self.window.sidebar.findChildren(Button)))

    def test_sidebar_account_category_shows_management_directly_and_legacy_route_redirects(self):
        self.window.navigate("settings", animated=False)
        QTest.mouseClick(self.account_category(), Qt.LeftButton)
        self.assert_account_settings()
        self.assertIsNotNone(self.page_button("添加离线账号"))
        self.assertIsNotNone(self.page_button("正版登录"))
        self.assertIsNotNone(self.page_button("下载玩家皮肤"))
        self.window.navigate("accounts", animated=False)
        self.assert_account_settings()
        self.assertEqual(self.window.host.page.objectName(), "page-settings")

    def test_add_and_remove_refresh_accounts_in_settings(self):
        self.window.set_settings_category("account")
        QTest.mouseClick(self.page_button("添加离线账号"), Qt.LeftButton)
        dialog = self.window.dialogs[-1]
        dialog.findChild(QLineEdit, "offline-name").setText("NewManaged")
        QTest.mouseClick(dialog.findChild(Button, "offline-submit"), Qt.LeftButton)
        native.wait_for(lambda: any(label.text() == "NewManaged" for label in self.window.page.findChildren(QLabel)))
        self.assert_account_settings()
        new = next(account for account in self.service.store.config()["accounts"] if account["name"] == "NewManaged")
        with patch("launcher.desktop.QMessageBox.question", return_value=QMessageBox.Yes):
            self.window.delete_account(new["id"])
        native.wait_for(lambda: all(account["id"] != new["id"] for account in self.window.data["config"]["accounts"]))
        native.wait_for(lambda: not any(label.text() == "NewManaged" for label in self.window.page.findChildren(QLabel)))
        self.assert_account_settings()

    def test_microsoft_login_action_and_live_english_keep_settings_location(self):
        with patch.object(self.window, "microsoft_login") as login:
            self.window.set_settings_category("account")
            QTest.mouseClick(self.page_button("正版登录"), Qt.LeftButton)
            login.assert_called_once()
        self.service.config_update({"language": "en_US"})
        self.window.apply_ui_language()
        self.assert_account_settings()
        self.assertEqual(self.account_category().text(), "Accounts")
        self.assertIsNotNone(self.page_button("Add offline account"))
        self.assertIsNotNone(self.page_button("Microsoft sign-in"))
        self.assertFalse(any(button.text() == "Accounts" for button in self.window.page.findChildren(Button)))


if __name__ == "__main__":
    unittest.main()
