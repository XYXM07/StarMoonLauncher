"""Website actions open only the chosen link, including MCMOD for disabled mods."""
import json
import unittest
import urllib.parse
import zipfile
from unittest.mock import patch

import test_native as native
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from launcher.i18n import set_language
from launcher.widgets import Button


class ModSiteUiTests(unittest.TestCase):
    def setUp(self):
        native.NativeTests.setUp(self)

    def tearDown(self):
        native.NativeTests.tearDown(self)
        set_language("zh_CN")

    def open_mod(self, direct=True, disabled=False):
        instance = self.service.game.create("Websites", "1.21.1", "fabric", "0.16.0")
        folder = self.service.store.game_dir(instance["id"]) / "mods"
        folder.mkdir()
        data = {"id": "example", "name": "Example Mod"}
        if direct:
            data["contact"] = {"homepage": "http://example.com/mod", "sources": "https://github.com/Author/Mod"}
            data["custom"] = {"modmenu": {"links": {"modmenu.mcmod": "https://www.mcmod.cn/class/2361.html"}}}
        with zipfile.ZipFile(folder / ("example.jar.disabled" if disabled else "example.jar"), "w") as archive:
            archive.writestr("fabric.mod.json", json.dumps(data))
        self.window.refresh()
        self.window.open_instance(instance["id"])
        self.window.set_instance_category("mods")
        native.wait_for(lambda: any(button.text() in ("查看网站", "View websites") and button.isVisible() for button in self.window.instance_panel.findChildren(Button)))
        button = next(button for button in self.window.instance_panel.findChildren(Button) if button.text() in ("查看网站", "View websites") and button.isVisible())
        QTest.mouseClick(button, Qt.LeftButton)
        return self.window.dialogs[-1]

    def test_homepage_and_mcmod_open_only_after_explicit_click(self):
        with patch("launcher.desktop.QDesktopServices.openUrl", return_value=True) as browser, patch.object(self.service.content.ai_downloads, "ask") as ai:
            dialog = self.open_mod()
            self.assertEqual(dialog.objectName(), "mod-websites-dialog")
            browser.assert_not_called()
            QTest.mouseClick(next(button for button in dialog.findChildren(Button) if button.text() == "MC百科"), Qt.LeftButton)
            self.assertEqual(browser.call_args.args[0].toString(), "https://www.mcmod.cn/class/2361.html")
            QTest.mouseClick(next(button for button in dialog.findChildren(Button) if button.text() == "项目主页"), Qt.LeftButton)
            self.assertEqual(browser.call_args.args[0].toString(), "http://example.com/mod")
            ai.assert_not_called()

    def test_disabled_mod_without_known_page_has_mcmod_name_search(self):
        with patch("launcher.desktop.QDesktopServices.openUrl", return_value=True) as browser:
            dialog = self.open_mod(direct=False, disabled=True)
            QTest.mouseClick(next(button for button in dialog.findChildren(Button) if button.text() == "在MC百科搜索"), Qt.LeftButton)
            url = urllib.parse.urlsplit(browser.call_args.args[0].toString())
            self.assertEqual(url.hostname, "search.mcmod.cn")
            self.assertEqual(urllib.parse.parse_qs(url.query)["key"], ["Example Mod"])

    def test_english_labels_and_unsafe_browser_targets(self):
        self.service.config_update({"language": "en_US"})
        set_language("en_US")
        with patch("launcher.desktop.QDesktopServices.openUrl", return_value=True) as browser:
            dialog = self.open_mod(direct=False)
            self.assertEqual(dialog.windowTitle(), "Mod websites")
            self.assertTrue(any(button.text() == "Search on MCMOD" for button in dialog.findChildren(Button)))
            self.window.open_mod_website("javascript:alert(1)")
            browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
