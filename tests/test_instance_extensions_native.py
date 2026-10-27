"""New downloads, loader selectors, export controls, titles and screenshot viewing."""
import unittest
from unittest.mock import patch

import test_native as native
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QListWidget
from launcher.desktop import ProjectDialog
from launcher.instance_ui import PackExportDialog
from launcher.widgets import Button
from launcher.i18n import set_language


class InstanceExtensionUiTests(unittest.TestCase):
    def setUp(self):
        native.NativeTests.setUp(self)
        self.window.timer.start(30)
        self.instance = self.service.game.create("Extensions", "1.20.1", "fabric", "0.16.0")
        self.game = self.service.store.game_dir(self.instance["id"])
        self.window.refresh()

    def tearDown(self):
        native.NativeTests.tearDown(self)
        set_language("zh_CN")

    def test_export_dialog_passes_explicit_groups_format_folder_and_metadata(self):
        dialog = self.window.open_dialog(PackExportDialog(self.window, self.instance["id"]))
        QTest.qWait(80)
        for widget in dialog.groups.values():
            widget.setChecked(False)
        with patch.object(self.window, "mutate") as submit:
            dialog.export()
            submit.assert_not_called()
            dialog.groups["saves"].setChecked(True)
            dialog.groups["config"].setChecked(True)
            dialog.format.setCurrentIndex(dialog.format.findData("modrinth"))
            dialog.destination.setText(str(self.root / "chosen-export"))
            dialog.name.setText("Chosen Pack")
            dialog.export()
            args = submit.call_args.args
            self.assertEqual(args[0], "pack/export")
            options = args[1]["options"]
            self.assertEqual(set(options["include"]), {"saves", "config"})
            self.assertEqual(options["format"], "modrinth")
            self.assertEqual(options["name"], "Chosen Pack")
            self.assertEqual(options["destination"], str(self.root / "chosen-export"))

    def test_title_field_is_live_and_screenshots_have_real_thumbnails_and_preview(self):
        root = self.game / "screenshots"
        root.mkdir()
        for i in range(23):
            image = QImage(320, 180, QImage.Format_RGB32)
            image.fill(QColor("#ff7050"))
            image.save(str(root / ("image-{:02d}.png".format(i))))
        self.window.open_instance(self.instance["id"])
        self.window.set_instance_category("settings")
        panel = self.window.instance_panel
        panel.instance_fields["window_title"].setText("{name} | {player}")
        self.window.flush_instance_settings()
        self.assertEqual(self.service.store.instance(self.instance["id"])["window_title"], "{name} | {player}")
        self.window.set_instance_category("screenshots")
        native.wait_for(lambda: self.window.instance_panel.findChild(QListWidget, "screenshot-gallery").count() == 20)
        gallery = self.window.instance_panel.findChild(QListWidget, "screenshot-gallery")
        native.wait_for(lambda: not gallery.item(0).icon().isNull())
        owner = gallery.parentWidget()
        owner.page(1)
        self.assertEqual(gallery.count(), 3)
        owner.preview()
        dialog = self.window.dialogs[-1]
        self.assertEqual(dialog.objectName(), "screenshot-preview")
        picture = dialog.findChild(QLabel, "screenshot-image")
        native.wait_for(lambda: picture.pixmap() is not None and not picture.pixmap().isNull())
        self.assertEqual(picture.pixmap().toImage().pixelColor(10, 10), QColor("#ff7050"))

    def test_datapack_dialog_requires_existing_world_and_passes_selection(self):
        world = self.game / "saves/Chosen world"
        world.mkdir(parents=True)
        (world / "level.dat").write_bytes(b"fixture")
        self.window.refresh()
        state = {"kind":"datapack", "provider":"modrinth", "instance":self.instance["id"]}
        with patch.object(self.service.content, "versions", return_value=[{"id":"file","name":"Datapack","game_versions":["1.20.1"],"date_published":"","version_type":"release"}]):
            dialog = self.window.open_dialog(ProjectDialog(self.window,{"project_id":"project","title":"Data pack"},state))
            native.wait_for(lambda: dialog.world.count() == 1)
        self.assertEqual(dialog.world.currentData(), "Chosen world")
        with patch.object(self.window, "mutate") as install:
            dialog.install({"id":"file"})
            self.assertEqual(install.call_args.args[1]["world"], "Chosen world")
            self.assertEqual(install.call_args.args[1]["kind"], "datapack")

    def test_download_sidebar_and_all_loader_choices_are_available_in_compact_window(self):
        self.window.navigate("install", animated=False)
        texts = {button.text() for button in self.window.sidebar.findChildren(Button)}
        self.assertIn("数据包", texts)
        self.assertIn("地图", texts)
        self.window.install_version = "1.12.2"
        self.window.navigate("install", animated=False)
        form = self.window.install_form
        self.assertEqual(set(form.rows), {"vanilla","forge","neoforge","fabric","quilt","optifine","cleanroom","liteloader"})
        native.wait_for(lambda: form.rows["cleanroom"]["radio"].isEnabled())
        form.rows["cleanroom"]["radio"].click()
        with patch.object(self.window, "request") as request:
            form.install()
            self.assertEqual(request.call_args.args[1]["loader"], "cleanroom")

    def test_new_instance_options_are_translated_to_english(self):
        self.service.config_update({"language":"en_US"})
        self.window.apply_ui_language()
        self.window.open_instance(self.instance["id"])
        self.window.set_instance_category("settings")
        self.assertTrue(any(label.text()=="Game window title" for label in self.window.page.findChildren(QLabel)))
        dialog = self.window.open_dialog(PackExportDialog(self.window,self.instance["id"]))
        self.assertEqual(dialog.submit.text(), "Export")
        self.assertEqual(dialog.groups["screenshots"].text(), "Screenshots")


if __name__ == "__main__":
    unittest.main()
