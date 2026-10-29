"""Native source selection, installation routing, and MCMOD browser fallbacks."""
import unittest
from unittest.mock import patch

import test_native as native
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from launcher.content import ContentManager
from launcher.core import LauncherError
from launcher.i18n import set_language
from launcher.widgets import Button


class ProviderUiTests(unittest.TestCase):
    def setUp(self):
        native.NativeTests.setUp(self)

    def tearDown(self):
        native.NativeTests.tearDown(self)
        set_language("zh_CN")

    def select_provider(self, provider):
        choice = self.window.content_provider
        choice.setCurrentIndex(choice.findData(provider))
        native.APP.processEvents()

    def test_pack_sources_appear_only_for_supported_categories(self):
        self.window.explore("modpack")
        choice = self.window.content_provider
        self.assertEqual([choice.itemData(i) for i in range(choice.count())], ["modrinth", "curseforge", "atlauncher", "ftb", "mcmod"])
        self.select_provider("ftb")
        self.window.explore("mod")
        self.assertEqual(self.window.content_provider.currentData(), "modrinth")
        self.assertGreaterEqual(self.window.content_provider.findData("mcmod"), 0)
        self.assertEqual(self.window.content_provider.findData("ftb"), -1)
        self.window.explore("shader")
        self.assertEqual(self.window.content_provider.findData("mcmod"), -1)

    def test_ftb_version_dialog_routes_install_without_existing_instance(self):
        project = {"project_id": "125", "title": "FTB Fixture", "description": "Pack", "author": "FTB", "downloads": 12}
        with patch.object(self.service.content, "search", return_value={"hits": [project], "total_hits": 1, "order_label": "平台顺序"}), \
             patch.object(self.service.content, "versions", return_value=[{"id": "10", "name": "1.0", "game_versions": ["1.21.1"]}]) as versions, \
             patch.object(self.service.content, "install_project", return_value={"instance_id": "fixture"}) as install:
            self.window.explore("modpack")
            self.select_provider("ftb")
            native.wait_for(lambda: any(b.text() == "选择版本" for b in self.window.page.findChildren(Button)))
            QTest.mouseClick(next(b for b in self.window.page.findChildren(Button) if b.text() == "选择版本"), Qt.LeftButton)
            native.wait_for(lambda: bool(self.window.dialogs))
            dialog = self.window.dialogs[-1]
            native.wait_for(lambda: any(b.text() == "↓ 安装" for b in dialog.findChildren(Button)))
            self.assertFalse(dialog.target.isVisible())
            self.assertEqual(versions.call_args.args, ("125", "", "modpack", "ftb"))
            QTest.mouseClick(next(b for b in dialog.findChildren(Button) if b.text() == "↓ 安装"), Qt.LeftButton)
            native.wait_for(lambda: install.called)
            self.assertEqual(install.call_args.args[:5], ("125", "10", "", "modpack", "ftb"))
            native.wait_for(lambda: all(t["status"] == "completed" for t in self.service.tasks.list()))

    def test_mcmod_results_open_canonical_article_without_install_dialog(self):
        project = {"project_id": "459", "title": "JEI", "description": "资料", "author": "MC百科", "downloads": None,
                   "action": "article", "project_url": "https://www.mcmod.cn/class/459.html"}
        with patch.object(self.service.content, "search", return_value={"hits": [project], "total_hits": 1}), \
             patch("launcher.desktop.QDesktopServices.openUrl", return_value=True) as browser:
            self.window.explore("mod")
            self.select_provider("mcmod")
            native.wait_for(lambda: any(b.text() == "查看百科" for b in self.window.page.findChildren(Button)))
            QTest.mouseClick(next(b for b in self.window.page.findChildren(Button) if b.text() == "查看百科"), Qt.LeftButton)
            self.assertEqual(browser.call_args.args[0].toString(), "https://www.mcmod.cn/class/459.html")
            self.assertEqual(self.window.dialogs, [])

    def test_mcmod_upstream_failure_keeps_chinese_query_and_browser_fallback(self):
        def real_search(*args, **kwargs):
            return ContentManager.search(self.service.content, *args, **kwargs)
        with patch.object(self.service.content, "search", side_effect=real_search), \
             patch.object(self.service.content.providers, "fetch_json", side_effect=LauncherError("upstream unavailable")), \
             patch("launcher.desktop.QDesktopServices.openUrl", return_value=True) as browser:
            self.window.explore("mod")
            self.select_provider("mcmod")
            self.window.content_query.setText("合成表")
            self.window.search_content()
            native.wait_for(lambda: any(b.text() == "在MC百科搜索" and b.isVisible() for b in self.window.page.findChildren(Button)))
            self.assertEqual(self.window.content_query.text(), "合成表")
            QTest.mouseClick(next(b for b in self.window.page.findChildren(Button) if b.text() == "在MC百科搜索" and b.isVisible()), Qt.LeftButton)
            self.assertIn("search.mcmod.cn", browser.call_args.args[0].toString())
            self.assertIn("合成表", browser.call_args.args[0].toString())

    def test_new_provider_controls_are_localized_in_english(self):
        self.service.config_update({"language": "en_US"})
        set_language("en_US")
        result = {"hits": [], "total_hits": 0, "order_label": "相关程度", "notice": "百科检索暂不可用，可在原站继续搜索。",
                  "external_search_url": "https://search.mcmod.cn/s?key=test&filter=1"}
        with patch.object(self.service.content, "search", return_value=result):
            self.window.explore("modpack")
            self.select_provider("mcmod")
            native.wait_for(lambda: any(b.text() == "Search on MCMOD" for b in self.window.page.findChildren(Button)))
            self.assertEqual(self.window.content_provider.currentText(), "MCMOD")

    def test_github_source_placeholder_and_opt_in_are_routed_with_target(self):
        instance = self.service.game.create("GitHub Target", "1.21.1", "fabric", "0.16.5")
        self.window.refresh()
        project = {"project_id": "Example/Mods", "title": "Mods", "description": "Release JARs", "author": "Example", "downloads": None}
        with patch.object(self.service.content, "search", return_value={"hits": [project], "total_hits": 1}), \
             patch.object(self.service.content, "versions", return_value=[{"id": "11", "name": "v1 · example.jar"}]), \
             patch.object(self.service.content, "install_project", return_value={"instance_id": instance["id"]}) as install:
            self.window.explore("mod", instance["id"])
            self.select_provider("github")
            self.assertIn("模组名称", self.window.content_query.placeholderText())
            native.wait_for(lambda: any(b.text() == "选择版本" and b.isVisible() for b in self.window.page.findChildren(Button)))
            QTest.mouseClick(next(b for b in self.window.page.findChildren(Button) if b.text() == "选择版本" and b.isVisible()), Qt.LeftButton)
            dialog = self.window.dialogs[-1]
            native.wait_for(lambda: any(b.text() == "↓ 安装" and b.isVisible() for b in dialog.findChildren(Button)))
            self.assertFalse(dialog.github_allow_unknown.isChecked())
            dialog.github_allow_unknown.setChecked(True)
            QTest.mouseClick(next(b for b in dialog.findChildren(Button) if b.text() == "↓ 安装" and b.isVisible()), Qt.LeftButton)
            native.wait_for(lambda: install.called)
            self.assertEqual(install.call_args.args[:5], ("Example/Mods", "11", instance["id"], "mod", "github"))
            self.assertTrue(install.call_args.kwargs["allow_unknown"])
            native.wait_for(lambda: all(t["status"] == "completed" for t in self.service.tasks.list()))

    def test_github_english_placeholder_and_empty_search_hint(self):
        self.service.config_update({"language": "en_US"})
        set_language("en_US")
        result = {"hits": [], "total_hits": 0, "waiting_query": True,
                  "notice": "输入公开 GitHub 仓库地址或 owner/repo，浏览作者发布的模组。"}
        with patch.object(self.service.content, "search", return_value=result):
            self.window.explore("mod")
            self.select_provider("github")
            self.assertEqual(self.window.content_query.placeholderText(), "Mod name or GitHub repository URL")

    def test_single_source_result_card_keeps_natural_height(self):
        from PySide6.QtWidgets import QWidget
        project = {"project_id": "1", "title": "One Pack", "description": "A short description", "author": "Author", "downloads": 1}
        with patch.object(self.service.content, "search", return_value={"hits": [project], "total_hits": 1}):
            self.window.resize(900, 600)
            self.window.explore("modpack")
            self.select_provider("ftb")
            native.wait_for(lambda: any(w.isVisible() for w in self.window.page.findChildren(QWidget, "project-card")))
            QTest.qWait(380)
            frame = next(w for w in self.window.page.findChildren(QWidget, "project-card") if w.isVisible())
            self.assertLessEqual(frame.height(), 160)

    def test_curseforge_search_keeps_width_during_loading_and_result_changes(self):
        import threading
        from PySide6.QtWidgets import QWidget
        gate, entered = threading.Event(), threading.Event()
        project = {'project_id': '1', 'title': 'Just Enough Items (JEI) ' * 30,
                   'description': 'A mod description ' * 50, 'author': 'LongAuthor' * 60,
                   'downloads': 12345678, 'icon_url': ''}
        initial = {'hits': [dict(project, project_id=str(index)) for index in range(18)], 'total_hits': 18}
        response = {'hits': [project], 'total_hits': 1}
        def search(query='', *args, **kwargs):
            if query:
                entered.set()
                gate.wait(5)
                return response
            return initial
        try:
            with patch.object(self.service.content, 'search', side_effect=search):
                self.window.resize(900, 600)
                self.window.search_state['provider'] = 'curseforge'
                self.window.explore('mod')
                native.wait_for(lambda: len(self.window.content_result.findChildren(QWidget, 'project-card')) == 18)
                QTest.qWait(350)
                width = self.window.content_query.width()
                self.window.content_query.setText('fixture')
                QTest.mouseClick(next(b for b in self.window.page.findChildren(Button) if b.text() == '搜索'), Qt.LeftButton)
                native.wait_for(entered.is_set)
                QTest.qWait(80)
                self.assertEqual(self.window.content_query.width(), width, 'Loading changed the search row width')
                gate.set()
                native.wait_for(lambda: len(self.window.content_result.findChildren(QWidget, 'project-card')) == 1)
                QTest.qWait(80)
                self.assertEqual(self.window.content_query.width(), width, 'A short result list widened the page')
                self.assertLessEqual(self.window.page.body.width(), self.window.page.viewport().width())
                result = self.window.content_result
                frame = result.findChild(QWidget, 'project-card')
                self.assertLessEqual(frame.geometry().right(), result.width())
                button = next(b for b in frame.findChildren(Button) if b.text() == '选择版本')
                self.assertLessEqual(button.mapTo(result, button.rect().bottomRight()).x(), result.width())
        finally:
            gate.set()

    def test_ai_download_switches_save_immediately_and_round_trip(self):
        self.window.navigate("settings", animated=False)
        self.window.set_settings_category("ai")
        first = self.window.settings_fields["ai_github_search"]
        second = self.window.settings_fields["ai_pack_download_fallback"]
        self.assertFalse(first.isChecked())
        self.assertTrue(second.isChecked())
        first.setChecked(True)
        native.wait_for(lambda: self.service.store.config()["ai_github_search"])
        self.assertTrue(self.service.store.config()["ai_pack_download_fallback"])
        second.setChecked(False)
        native.wait_for(lambda: not self.service.store.config()["ai_pack_download_fallback"])
        second.setChecked(True)
        native.wait_for(lambda: self.service.store.config()["ai_pack_download_fallback"])
        self.window.navigate("home", animated=False)
        self.window.navigate("settings", animated=False)
        self.window.set_settings_category("ai")
        self.assertTrue(self.window.settings_fields["ai_github_search"].isChecked())
        self.assertTrue(self.window.settings_fields["ai_pack_download_fallback"].isChecked())

    def test_ai_download_switches_have_english_labels(self):
        self.service.config_update({"language": "en_US"})
        set_language("en_US")
        self.window.navigate("settings", animated=False)
        self.window.set_settings_category("ai")
        self.assertEqual(self.window.settings_fields["ai_github_search"].text(), "Find GitHub mods with AI")
        self.assertEqual(self.window.settings_fields["ai_pack_download_fallback"].text(), "Use AI to find failed modpack downloads")


if __name__ == "__main__":
    unittest.main()
