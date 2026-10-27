"""Advanced renderer and launch toggles persist immediately per instance."""
import unittest
from unittest.mock import patch
import test_native as native
from PySide6.QtTest import QTest
from launcher.i18n import set_language
from launcher.launch_options import DEFAULTS


class LaunchOptionUiTests(unittest.TestCase):
    def setUp(self):
        native.NativeTests.setUp(self)
        self.window.resize(900,600)
        self.instance=self.service.game.create("Advanced","1.20.1")
        self.window.data=self.service.state()

    def tearDown(self):
        native.NativeTests.tearDown(self)
        set_language("zh_CN")

    def panel(self):
        self.window.open_instance(self.instance["id"])
        self.window.set_instance_category("settings")
        QTest.qWait(60)
        return self.window.instance_panel

    def test_renderer_and_four_switches_are_live_and_survive_navigation(self):
        panel=self.panel()
        fields=panel.instance_fields
        renderer=fields["renderer"]
        self.assertEqual({renderer.itemData(index) for index in range(renderer.count())},{"opengl","zink","d3d12","llvmpipe"})
        self.assertNotIn("use_system_glfw",fields)
        self.assertNotIn("use_system_openal",fields)
        with patch.object(self.service.net,"download") as download:
            renderer.setCurrentIndex(renderer.findData("zink"))
            for key in DEFAULTS:
                if key!="renderer":
                    fields[key].click()
            self.window.flush_instance_settings()
            download.assert_not_called()
        saved=self.service.store.instance(self.instance["id"])
        self.assertEqual(saved["renderer"],"zink")
        self.assertTrue(all(saved[key] for key in DEFAULTS if key!="renderer"))
        self.window.set_instance_category("overview")
        self.window.set_instance_category("settings")
        self.assertEqual(self.window.instance_panel.instance_fields["renderer"].currentData(),"zink")
        self.assertTrue(self.window.instance_panel.instance_fields["skip_java_check"].isChecked())

    def test_english_labels_and_directx_selection_fit_compact_width(self):
        self.service.config_update({"language":"en_US"})
        self.window.apply_ui_language()
        panel=self.panel()
        renderer=panel.instance_fields["renderer"]
        self.assertIn("DirectX 12",renderer.itemText(renderer.findData("d3d12")))
        self.assertNotRegex(panel.instance_fields["skip_game_check"].text(),r"[\u4e00-\u9fff]")
        renderer.setCurrentIndex(renderer.findData("d3d12"))
        self.assertEqual(self.service.store.instance(self.instance["id"])["renderer"],"d3d12")
        self.assertLessEqual(renderer.width(),self.window.page.viewport().width())


if __name__=="__main__":
    unittest.main()
