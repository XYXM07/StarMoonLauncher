"""Offline skin avatars must not stop account management or its refresh timer."""
import unittest
from unittest.mock import patch

import test_native as native
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from launcher.skin_ui import AccountAvatar
from launcher.widgets import Button


class AccountManagementTests(unittest.TestCase):
    def setUp(self):
        native.NativeTests.setUp(self)
        offline = self.service.accounts.offline("SkinPlayer")
        self.offline_id = offline["id"]
        skin = QImage(64, 64, QImage.Format_ARGB32)
        skin.fill(QColor("red"))
        self.skin_path = self.root / "account-fixture.png"
        skin.save(str(self.skin_path))
        self.service.skins.save(self.offline_id, self.skin_path)
        self.microsoft = {"id": "f" * 32, "name": "OnlineFixture", "type": "microsoft"}
        avatar_patch = patch.object(self.service.player_skins, "account_skin", return_value="")
        avatar_patch.start()
        self.addCleanup(avatar_patch.stop)
        self.service.accounts._save_account(self.microsoft)
        self.service.config_update({"login_mode": "microsoft"})
        self.window.login_mode = "microsoft"
        self.window.data = self.service.state()
        self.window.navigate("home", animated=False)

    def tearDown(self):
        native.NativeTests.tearDown(self)

    def test_account_page_renders_offline_skin_while_microsoft_account_is_selected(self):
        with patch.object(self.service.player_skins, "account_skin", return_value=""):
            self.window.navigate("accounts", animated=False)
            avatars = self.window.page.findChildren(AccountAvatar)
            self.assertEqual(len(avatars), 2)
            self.assertEqual(avatars[0].pixmap().toImage().pixelColor(20, 20), QColor("red"))
            self.assertEqual(self.service.store.config()["selected_account"], self.microsoft["id"])

    def test_account_refresh_and_switch_with_custom_offline_skin_keep_timer_running(self):
        self.window.timer.start(30)
        self.window.set_settings_category("account")
        self.window.instance_signature = "force-account-page-refresh"
        self.window.refresh()
        self.assertTrue(self.window.timer.isActive())
        self.assertEqual(self.window.host.page.objectName(), "page-settings")
        switch = next(button for button in self.window.page.findChildren(Button) if button.text() == "切换账号")
        QTest.mouseClick(switch, Qt.LeftButton)
        self.assertEqual(self.window.page_id, "home")
        self.assertEqual(self.service.store.config()["selected_account"], self.offline_id)
        self.assertEqual(self.window.avatar_label.pixmap().toImage().pixelColor(20, 20), QColor("red"))
        self.assertTrue(self.window.timer.isActive())


if __name__ == "__main__":
    unittest.main()
