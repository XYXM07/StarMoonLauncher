import json
import threading
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
from launcher.core import LauncherError


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

    def test_dependency_ai_default_results_failure_and_mode_persistence(self):
        identifier = self.instance()
        self.service.config_update({'ai_base_url': 'http://127.0.0.1:1/v1', 'ai_model': 'fixture'})
        report = {'source': 'ai', 'status': 'risk', 'cause': '前置 b 未启用', 'suggestion': '启用 b 后重试'}
        with patch.object(self.service.diagnostics, '_request', return_value=report) as request:
            dialog = self.window.open_dialog(DependencyDialog(self.window, identifier))
            wait_for(lambda: '启用 b 后重试' in dialog.analysis.text())
            self.assertTrue(dialog.ai_check.isChecked())
            self.assertEqual(request.call_count, 1)
            self.assertTrue(dialog.rescan.isEnabled())
        with patch.object(self.service.diagnostics, '_request', side_effect=LauncherError('fixture offline')):
            dialog.reload()
            wait_for(lambda: 'fixture offline' in dialog.analysis.text())
            self.assertEqual(dialog.table.topLevelItem(0).child(0).text(3), '已禁用')
        with patch.object(self.service.diagnostics, '_request') as request:
            dialog.ai_check.setChecked(False)
            wait_for(lambda: '已使用本地元数据' in dialog.analysis.text())
            self.assertFalse(self.service.store.config()['ai_dependency_check'])
            dialog.reject()
            reopened = self.window.open_dialog(DependencyDialog(self.window, identifier))
            wait_for(lambda: '已使用本地元数据' in reopened.analysis.text())
            self.assertFalse(reopened.ai_check.isChecked())
            request.assert_not_called()

    def test_dependency_ai_cancel_does_not_apply_late_result(self):
        identifier = self.instance()
        self.service.config_update({'ai_base_url': 'http://127.0.0.1:1/v1', 'ai_model': 'fixture'})
        entered, release = threading.Event(), threading.Event()
        def slow(*args, **kwargs):
            entered.set()
            release.wait(3)
            kwargs['task'].check()
            return {'status': 'ok', 'cause': 'late result', 'suggestion': 'unused'}
        with patch.object(self.service.diagnostics, '_request', side_effect=slow) as request:
            dialog = self.window.open_dialog(DependencyDialog(self.window, identifier))
            try:
                wait_for(lambda: entered.is_set() and dialog.scan_job is not None)
                job = self.service.tasks.tasks[dialog.scan_job]
                self.assertEqual(dialog.table.topLevelItemCount(), 2)
                self.assertFalse(dialog.install_button.isEnabled())
                dialog.reload()
                self.assertEqual(request.call_count, 1)
                dialog.cancel_scan()
                release.set()
                wait_for(lambda: bool(job.finished))
                self.assertEqual(job.status, 'cancelled')
                self.assertIn('检查已取消', dialog.analysis.text())
                self.assertNotIn('late result', dialog.analysis.text())
                self.assertTrue(dialog.ai_check.isEnabled())
            finally:
                release.set()

    def test_dependency_batch_progress_and_all_results_are_visible(self):
        identifier = self.instance()
        self.service.config_update({'ai_base_url': 'http://127.0.0.1:1/v1', 'ai_model': 'fixture'})
        entered, release = threading.Event(), threading.Event()
        def review(*args, **kwargs):
            kwargs['task'].update('大模型检查：第 2 / 3 批', 50)
            entered.set()
            release.wait(4)
            return {'status': 'risk', 'cause': 'last risk', 'suggestion': 'last fix', 'source': 'ai',
                    'batch_count': 3, 'completed_batches': 3, 'batch_reports': [
                        {'batch': i, 'status': 'risk', 'cause': 'risk_' + str(i), 'suggestion': 'fix_' + str(i)} for i in (1, 2, 3)]}
        with patch.object(self.service.diagnostics, '_request', side_effect=review):
            dialog = self.window.open_dialog(DependencyDialog(self.window, identifier))
            try:
                wait_for(lambda: entered.is_set() and '2 / 3' in dialog.analysis.text())
                release.set()
                wait_for(lambda: not dialog.scanning)
                self.assertIn('已完成 3 / 3', dialog.analysis.text())
                self.assertTrue(dialog.batch_details.isVisible())
                self.assertIn('risk_1', dialog.batch_details.toPlainText())
                self.assertIn('fix_3', dialog.batch_details.toPlainText())
                dialog.ai_check.setChecked(False)
                wait_for(lambda: not dialog.scanning)
                self.assertFalse(dialog.batch_details.isVisible())
            finally:
                release.set()

    def test_dependency_ai_english_compact_and_settings_switch(self):
        set_language('en_US')
        self.addCleanup(set_language, 'zh_CN')
        self.service.config_update({'language': 'en_US'})
        identifier = self.instance()
        self.window.resize(900, 600)
        dialog = self.window.open_dialog(DependencyDialog(self.window, identifier))
        wait_for(lambda: 'AI is not configured' in dialog.analysis.text())
        self.assertEqual(dialog.ai_check.text(), 'Use AI analysis')
        for button in (dialog.rescan, dialog.stop_scan, dialog.install_button):
            self.assertLessEqual(button.mapTo(dialog, button.rect().bottomRight()).x(), dialog.width())
        self.assertLessEqual(dialog.install_button.mapTo(dialog, dialog.install_button.rect().bottomRight()).y(), dialog.height())
        dialog.reject()
        self.window.navigate('settings')
        check = self.window.settings_fields['ai_dependency_check']
        self.assertTrue(check.isChecked())
        check.setChecked(False)
        self.assertFalse(self.service.store.config()['ai_dependency_check'])

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
