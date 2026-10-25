"""Installed mod status categories and the rounded full-size fallback picture."""
import json
import unittest
import zipfile

import test_native as native
from PySide6.QtCore import QAbstractAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel
from launcher.images import ProjectPreview
from launcher.i18n import set_language
from launcher.widgets import Button, NavigationSwitch


class ModFilterTests(unittest.TestCase):
    def setUp(self):
        native.NativeTests.setUp(self)
        self.window.timer.start(30)
        self.instance = self.service.game.create("Categories", "1.21.1", "fabric", "0.16.0")
        self.folder = self.service.store.game_dir(self.instance["id"]) / "mods"
        self.folder.mkdir()
        for filename, name in (("enabled.jar", "Enabled Mod"), ("disabled.jar.disabled", "Disabled Mod")):
            with zipfile.ZipFile(self.folder / filename, "w") as archive:
                archive.writestr("fabric.mod.json", json.dumps({"id": name.lower().replace(" ", "_"), "name": name}))
        self.window.refresh()
        self.window.open_instance(self.instance["id"])
        self.window.set_instance_category("mods")
        native.wait_for(lambda: len(self.panel.local_items) == 2)

    @property
    def panel(self):
        return self.window.instance_panel

    def tearDown(self):
        native.NativeTests.tearDown(self)
        set_language("zh_CN")

    def count(self):
        if self.panel.local_cards is not None:
            return len(self.panel.local_cards.items)
        return sum(1 for index in range(self.panel.local_layout.count())
                   if self.panel.local_layout.itemAt(index).widget() and
                   self.panel.local_layout.itemAt(index).widget().findChild(ProjectPreview, "local-mod-preview") is not None)

    def choose(self, status):
        QTest.mouseClick(self.panel.mod_status_switch.buttons[status], Qt.LeftButton)
        native.APP.processEvents()

    def test_categories_filter_exclusively_and_combine_with_search(self):
        self.assertEqual(self.count(), 2)
        self.choose("enabled")
        self.assertEqual(self.count(), 1)
        self.assertTrue(self.panel.mod_status_switch.buttons["enabled"].isChecked())
        self.assertFalse(self.panel.mod_status_switch.buttons["all"].isChecked())
        self.panel.local_query.setText("disabled")
        self.assertEqual(self.count(), 0)
        self.choose("disabled")
        self.assertEqual(self.count(), 1)
        self.panel.local_query.setText("not-found")
        self.assertEqual(self.count(), 0)
        self.assertTrue(any(item.text() == "没有匹配的文件" for item in self.panel.findChildren(QLabel)))
        self.panel.local_query.clear()
        self.choose("all")
        self.assertEqual(self.count(), 2)

    def test_enabling_a_mod_updates_current_category_after_rename(self):
        self.choose("disabled")
        QTest.mouseClick(next(button for button in self.panel.findChildren(Button) if button.text() == "启用"), Qt.LeftButton)
        native.wait_for(lambda: (self.folder / "disabled.jar").exists())
        native.wait_for(lambda: all(item["enabled"] for item in self.panel.local_items))
        self.assertEqual(self.panel.mod_status, "disabled")
        self.assertEqual(self.count(), 0)
        self.choose("enabled")
        self.assertEqual(self.count(), 2)

    def test_categories_do_not_appear_for_resource_packs(self):
        self.window.set_instance_category("resourcepacks")
        self.assertIsNone(self.panel.findChild(NavigationSwitch, "mod-status-switch"))

    def test_animation_survives_scrollbar_changes_and_rapid_switches(self):
        self.window.resize(900, 600)
        # Force the unfiltered list to overflow, while each category fits.
        self.panel.local_items *= 4
        self.panel.filter_local()
        QTest.qWait(450)
        switch = self.panel.mod_status_switch
        for status in ("enabled", "disabled", "all", "disabled"):
            self.choose(status)
            QTest.qWait(45)
            self.assertEqual(switch.motion.state(), QAbstractAnimation.Running)
            target = QRectF(switch.buttons[status].geometry()).adjusted(0, 1, 0, -1)
            self.assertNotEqual(switch.pill, target)
        QTest.qWait(320)
        self.assertEqual(switch.motion.state(), QAbstractAnimation.Stopped)
        self.assertEqual(switch.pill, QRectF(switch.buttons["disabled"].geometry()).adjusted(0, 1, 0, -1))

    def test_resizing_track_keeps_animation_running_and_settles_on_button(self):
        switch = self.panel.mod_status_switch
        QTest.qWait(450)
        self.choose("disabled")
        QTest.qWait(45)
        switch.setMaximumWidth(310)
        QTest.qWait(35)
        self.assertEqual(switch.motion.state(), QAbstractAnimation.Running)
        self.assertNotEqual(switch.pill, QRectF(switch.buttons["disabled"].geometry()).adjusted(0, 1, 0, -1))
        QTest.qWait(300)
        self.assertEqual(switch.pill, QRectF(switch.buttons["disabled"].geometry()).adjusted(0, 1, 0, -1))

    def test_filtering_keeps_content_width_when_scrollbar_is_not_needed(self):
        self.window.resize(900, 600)
        enabled = next(item for item in self.panel.local_items if item["enabled"])
        disabled = next(item for item in self.panel.local_items if not item["enabled"])
        self.panel.local_items = [enabled] * 8 + [disabled]
        self.panel.filter_local()
        QTest.qWait(450)
        page = self.window.page
        self.assertGreater(page.verticalScrollBar().maximum(), 0)

        def widths():
            return (page.viewport().width(), self.panel.width(), self.panel.local_query.width(),
                    tuple(button.width() for button in self.panel.mod_status_switch.buttons.values()))

        original = widths()
        for status in ("disabled", "all", "enabled", "disabled"):
            self.choose(status)
            for delay in (30, 100, 220):
                QTest.qWait(delay)
                self.assertEqual(widths(), original, status)
        self.assertEqual(page.verticalScrollBar().maximum(), 0)
        self.panel.local_query.setText("no-matches")
        QTest.qWait(80)
        self.assertEqual(widths(), original)
        self.panel.local_query.clear()
        QTest.qWait(80)
        self.assertEqual(widths(), original)

    def test_compact_equal_segments_in_chinese_and_english(self):
        for language in ("zh_CN", "en_US"):
            set_language(language)
            self.window.set_instance_category("mods")
            self.window.resize(900, 600)
            QTest.qWait(450)
            switch = self.panel.mod_status_switch
            self.assertLessEqual(switch.width(), 360)
            self.assertEqual(switch.height(), 44)
            widths = [button.width() for button in switch.buttons.values()]
            self.assertLessEqual(max(widths) - min(widths), 1)
            for status, button in switch.buttons.items():
                self.assertGreaterEqual(button.width(), button.fontMetrics().horizontalAdvance(button.text()) + 18)
                switch.select(status, False)
                self.assertEqual(switch.pill, QRectF(button.geometry()).adjusted(0, 1, 0, -1))
        self.window.animations = False
        self.choose("enabled")
        self.assertEqual(switch.motion.state(), QAbstractAnimation.Stopped)

    def test_placeholder_covers_edges_and_is_clipped_to_rounded_corners(self):
        icon = QImage(120, 60, QImage.Format_ARGB32)
        icon.fill(QColor("red"))
        for x in range(60, 120):
            for y in range(60):
                icon.setPixelColor(x, y, QColor("blue"))
        self.window.setWindowIcon(QIcon(QPixmap.fromImage(icon)))
        preview = ProjectPreview(self.window.images, "", 72, self.window)
        preview.move(500, 160)
        preview.show()
        QTest.qWait(50)
        image = preview.grab().toImage()
        middle = image.height() // 2
        self.assertEqual(image.pixelColor(2, middle), QColor("red"))
        self.assertEqual(image.pixelColor(image.width() - 3, middle), QColor("blue"))
        self.assertNotEqual(image.pixelColor(0, 0), QColor("red"))


if __name__ == "__main__":
    unittest.main()
