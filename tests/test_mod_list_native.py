"""Large installed-mod lists stay bounded and target the right file after scrolling."""
import time
import unittest
from unittest.mock import Mock, patch

import test_native as native
import test_mod_filters as filters
from PySide6.QtCore import QAbstractAnimation, Qt
from PySide6.QtTest import QTest
from launcher.widgets import Button, ElidedLabel


class ModListNativeTests(unittest.TestCase):
    setUp = filters.ModFilterTests.setUp
    tearDown = filters.ModFilterTests.tearDown
    panel = filters.ModFilterTests.panel

    def large_list(self):
        self.window.timer.stop()
        self.window.resize(900, 600)
        QTest.qWait(450)
        self.panel.local_items = [dict(kind="mod", filename="mod-{:04d}.jar{}".format(i, ".disabled" if i % 2 else ""),
            mod_name="Example Mod {}".format(i), description="A synthetic local mod for UI verification.",
            size=1024, enabled=not bool(i % 2)) for i in range(1000)]
        self.panel.filter_local()
        self.cards = self.panel.local_cards
        native.wait_for(lambda: bool(self.cards.rows))
        native.wait_for(lambda: self.window.page.verticalScrollBar().maximum() >=
                        self.cards.height() - self.window.page.viewport().height())

    def test_large_list_creates_only_nearby_cards_and_last_row_actions_target_correct_file(self):
        self.large_list()
        self.assertEqual(len(self.cards.items), 1000)
        self.assertLessEqual(len(self.cards.rows), 8)
        self.window.page.verticalScrollBar().setValue(self.window.page.verticalScrollBar().maximum())
        native.wait_for(lambda: 999 in self.cards.rows)
        self.assertLessEqual(len(self.cards.rows), 8)
        item, row = self.cards.rows[999]
        self.assertTrue(any(label.full_text == "Example Mod 999" for label in row.findChildren(ElidedLabel)))
        with patch.object(self.window, "mutate") as mutate:
            QTest.mouseClick(next(button for button in row.findChildren(Button) if button.text() == "启用"), Qt.LeftButton)
            self.assertEqual(mutate.call_args.args[1]["filename"], "mod-0999.jar.disabled")
        with patch.object(self.panel, "show_mod_websites") as website:
            QTest.mouseClick(next(button for button in row.findChildren(Button) if button.text() == "查看网站"), Qt.LeftButton)
            website.assert_called_once_with(item)

    def test_rapid_categories_keep_animation_running_and_only_render_latest_results(self):
        self.large_list()
        switch = self.panel.mod_status_switch
        self.cards.factory = Mock(wraps=self.cards.factory)
        for status in ("disabled", "enabled", "all", "disabled"):
            started = time.perf_counter()
            QTest.mouseClick(switch.buttons[status], Qt.LeftButton)
            self.assertLess(time.perf_counter() - started, .15)
            QTest.qWait(45)
            self.assertEqual(switch.motion.state(), QAbstractAnimation.Running)
            self.assertLessEqual(len(self.cards.rows), 8)
        QTest.qWait(320)
        self.assertEqual(len(self.cards.items), 500)
        self.assertTrue(all(not item["enabled"] for item in self.cards.items))
        self.assertTrue(all(not item["enabled"] for item, _ in self.cards.rows.values()))
        self.assertLess(self.cards.factory.call_count, 30)
        self.assertEqual(switch.motion.state(), QAbstractAnimation.Stopped)

    def test_bottom_to_empty_search_then_restore_and_leave_page_during_pending_render(self):
        self.large_list()
        bar = self.window.page.verticalScrollBar()
        bar.setValue(bar.maximum())
        native.wait_for(lambda: 999 in self.cards.rows)
        self.panel.local_query.setText("missing-mod")
        native.wait_for(lambda: not self.cards.rows and not self.cards.items)
        self.panel.local_query.setText("Example Mod 999")
        native.wait_for(lambda: 0 in self.cards.rows)
        self.assertEqual(self.cards.rows[0][0]["filename"], "mod-0999.jar.disabled")
        self.panel.local_query.clear()
        self.window.set_instance_category("resourcepacks")
        QTest.qWait(100)
        self.assertEqual(self.panel.content_kind, "resourcepack")

    def test_update_results_can_be_replaced_by_normal_filter_again(self):
        self.large_list()
        result = dict(filename="mod-0000.jar", kind="mod", new_name="New release", new_version="new",
                      provider="modrinth", project_id="example")
        def respond(path, **callbacks):
            callbacks["success"]([result])
        with patch.object(self.window, "request", side_effect=respond):
            self.panel.check_updates()
        self.assertIsNone(self.panel.local_cards)
        self.panel.set_mod_status("disabled")
        native.wait_for(lambda: bool(self.panel.local_cards.rows))
        self.assertEqual(len(self.panel.local_cards.items), 500)
        self.assertTrue(all(not item["enabled"] for item, _ in self.panel.local_cards.rows.values()))


if __name__ == "__main__":
    unittest.main()
