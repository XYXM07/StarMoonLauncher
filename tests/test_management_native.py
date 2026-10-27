import json
import time
import unittest
import zipfile
from unittest.mock import patch

import test_native as native
from test_native import APP, wait_for, QTest
from PySide6.QtCore import Qt
from launcher.management_ui import BulkModsDialog, DependencyDialog, JavaDialog, PackUpgradeDialog, PackReviewDialog, LogPanel
from launcher.widgets import Button
from launcher.i18n import set_language


class ManagementNativeTests(unittest.TestCase):
    setUp = native.NativeTests.setUp

    def tearDown(self):
        # A failed background-action assertion must not leave CI waiting for
        # the production "tasks still running" confirmation dialog.
        for task in self.service.tasks.tasks.values():
            task.cancel.set()
        deadline = time.monotonic() + 5
        while any(not task.finished for task in self.service.tasks.tasks.values()) and time.monotonic() < deadline:
            QTest.qWait(20)
        with patch('launcher.desktop.QMessageBox.exec', return_value=0):
            native.NativeTests.tearDown(self)

    def wait_completed(self, dialog):
        try:
            wait_for(lambda: bool(self.service.tasks.tasks) and all(task.finished for task in self.service.tasks.tasks.values()))
        except AssertionError:
            self.fail('Background action did not finish: tasks={}, dialog={}'.format(self.service.tasks.list(), dialog.error_label.text()))
        self.assertTrue(all(task.status == 'completed' for task in self.service.tasks.tasks.values()), self.service.tasks.list())
        wait_for(lambda: dialog.job is None)

    def instance(self):
        item = self.service.game.create('Test', '1.20.1', 'fabric', '0.16.0')
        self.identifier = item['id']
        mods = self.service.store.game_dir(self.identifier) / 'mods'
        mods.mkdir()
        for name, data in [('a.jar', {'id': 'a', 'version': '1', 'depends': {'b': '>=1'}}), ('b.jar.disabled', {'id': 'b', 'version': '1'})]:
            with zipfile.ZipFile(mods / name, 'w') as archive: archive.writestr('fabric.mod.json', json.dumps(data))
        self.window.refresh()
        return self.identifier

    def test_bulk_selection_filter_and_real_background_action(self):
        identifier = self.instance()
        dialog = self.window.open_dialog(BulkModsDialog(self.window, identifier))
        wait_for(lambda: dialog.table.topLevelItemCount() == 2)
        dialog.search.setText('a.jar')
        dialog.select(True)
        self.assertEqual(dialog.selected(), ['a.jar'])
        dialog.actions.setCurrentIndex(dialog.actions.findData('lock'))
        dialog.apply()
        self.wait_completed(dialog)
        self.assertTrue(self.service.content.list_local(identifier)[0]['update_locked'])

    def test_dependency_tree_and_enable_repair(self):
        identifier = self.instance()
        dialog = self.window.open_dialog(DependencyDialog(self.window, identifier))
        wait_for(lambda: dialog.table.topLevelItemCount() == 2)
        parent = dialog.table.topLevelItem(0)
        self.assertEqual(parent.childCount(), 1)
        dialog.table.setCurrentItem(parent.child(0))
        self.assertEqual(dialog.edge['status'], 'disabled')
        dialog.enable()
        self.wait_completed(dialog)
        self.assertTrue((self.service.store.game_dir(identifier) / 'mods/b.jar').exists())

    def test_java_manager_compact_and_select(self):
        with patch('launcher.java_manager.listing', return_value=[{'path': 'C:/Java/bin/java.exe', 'version': '21', 'architecture': 'x64', 'managed': False, 'uses': [], 'size': 0}]):
            self.window.resize(900, 600)
            dialog = self.window.open_dialog(JavaDialog(self.window))
            wait_for(lambda: dialog.table.topLevelItemCount() == 1)
            self.assertLessEqual(dialog.width(), self.window.width())
            self.assertEqual(dialog.major.currentData(), 21)

    def test_dependency_wrong_loader_is_not_a_repair_candidate(self):
        identifier = self.instance()
        self.service.store.update_instance(identifier, {'loader': 'forge'})
        dialog = self.window.open_dialog(DependencyDialog(self.window, identifier))
        wait_for(lambda: dialog.table.topLevelItemCount() == 2)
        items = [dialog.table.topLevelItem(i) for i in range(2)]
        self.assertEqual({item.text(0): item.text(3) for item in items}, {'a.jar': '加载器不匹配', 'b.jar.disabled': '已禁用'})
        self.assertTrue(all(item.childCount() == 0 for item in items))
        dialog.table.setCurrentItem(items[0])
        self.assertIsNone(dialog.edge)

    def test_pack_versions_and_review_buttons_fit_compact(self):
        identifier = self.instance()
        dialog = self.window.open_dialog(PackUpgradeDialog(self.window, identifier))
        dialog.project.setText('pack')
        with patch.object(self.service.content, 'versions', return_value=[{'id': 'v2', 'name': 'Pack 2', 'version_type': 'release'}]):
            dialog.fetch()
            wait_for(lambda: dialog.versions.topLevelItemCount() == 1)
        dialog.versions.setCurrentItem(dialog.versions.topLevelItem(0))
        dialog.use_current()
        self.assertEqual(dialog.current.text(), 'v2')
        review = self.window.open_dialog(PackReviewDialog(self.window, {'id': 'none', 'interaction': {'id': 'r', 'details': {'from': 'v1', 'to': 'v2', 'conflicts': 1, 'changes': [{'path': 'config/test.toml', 'action': 'replace', 'conflict': True}]}}}))
        QTest.qWait(50)
        for button in review.findChildren(Button):
            if button.isVisible(): self.assertLessEqual(button.mapTo(review, button.rect().bottomRight()).x(), review.width())

    def test_log_center_filter_and_live_append(self):
        identifier = self.instance()
        logs = self.service.store.game_dir(identifier) / 'logs'
        logs.mkdir()
        (logs / 'latest.log').write_text('INFO hello\nERROR trouble\n')
        self.window.open_instance(identifier)
        self.window.set_instance_category('logs')
        wait_for(lambda: self.window.instance_panel.findChild(LogPanel) is not None)
        panel = self.window.instance_panel.findChild(LogPanel)
        wait_for(lambda: 'trouble' in panel.text.toPlainText())
        panel.level.setCurrentIndex(panel.level.findData('error'))
        wait_for(lambda: 'hello' not in panel.text.toPlainText())
        with (logs / 'latest.log').open('a') as stream: stream.write('ERROR newer\n')
        panel.refresh()
        wait_for(lambda: 'newer' in panel.text.toPlainText())


if __name__ == '__main__': unittest.main()
