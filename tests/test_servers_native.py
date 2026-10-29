import threading
import unittest
from unittest.mock import patch

import test_native as native
from test_native import APP, QTest, wait_for
from test_servers import FakeServer
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from launcher.widgets import Button
from launcher.i18n import set_language
from launcher.server_ui import ServerEditDialog, LocalCreateDialog, LocalServerDialog


class ServerNativeTests(unittest.TestCase):
    setUp = native.NativeTests.setUp

    def tearDown(self):
        for run in self.service.local_servers.runs.values():
            if run.get('process'): run['process'].terminate()
        for task in self.service.tasks.tasks.values(): task.cancel.set()
        wait_for(lambda: not self.service.local_servers.active() and all(t.finished for t in self.service.tasks.tasks.values()))
        with patch('launcher.desktop.QMessageBox.exec', return_value=0): native.NativeTests.tearDown(self)
        set_language('zh_CN')

    def local(self):
        folder = self.root/'server'; folder.mkdir()
        (folder/'server.jar').write_bytes(b'fixture')
        return self.service.local_servers.register({'folder': str(folder), 'entry': 'server.jar', 'name': 'Test server'})

    def test_server_page_save_status_join_edit_and_remove(self):
        instance = self.service.game.create('Client', '1.20.1')
        account = self.service.accounts.offline('Tester')
        self.window.refresh()
        self.window.navigate('servers')
        panel = self.window.server_panel
        dialog = self.window.open_dialog(ServerEditDialog(self.window, panel.reload))
        dialog.name.setText('Saved'); dialog.address.setText('example.invalid:25566')
        dialog.instance.setCurrentIndex(dialog.instance.findData(instance['id']))
        dialog.save()
        wait_for(lambda: panel.table.topLevelItemCount() == 1)
        panel.table.setCurrentItem(panel.table.topLevelItem(0))
        with patch('launcher.server_list.status', return_value={'online': True, 'players': 2, 'max_players': 10, 'latency': 8, 'motd': '<b>Plain text</b>', 'version': '1.20.1'}):
            panel.ping(); wait_for(lambda: '8 ms' in panel.table.topLevelItem(0).text(2))
            self.assertEqual(panel.details.textFormat(), Qt.PlainText)
        with patch.object(self.window, 'mutate') as mutate:
            panel.join()
            self.assertEqual(mutate.call_args.args[0], 'instances/launch')
            self.assertEqual(mutate.call_args.args[1], {'id': instance['id'], 'account': account['id'], 'server': 'example.invalid:25566'})
        panel.manage()
        edit = next(d for d in self.window.dialogs if isinstance(d, ServerEditDialog))
        edit.name.setText('Renamed'); edit.save()
        wait_for(lambda: panel.table.topLevelItem(0).text(0) == 'Renamed')
        with patch('launcher.server_ui.QMessageBox.question', return_value=QMessageBox.Yes): panel.remove()
        wait_for(lambda: panel.table.topLevelItemCount() == 0)

    def test_local_console_configuration_real_task_and_stop(self):
        item = self.local()
        children = []
        def spawn(*args, **kwargs):
            child = FakeServer(*args, **kwargs); children.append(child); return child
        dialog = self.window.open_dialog(LocalServerDialog(self.window, item['id']))
        wait_for(lambda: dialog.name.text() == 'Test server')
        self.assertFalse(dialog.eula.isChecked())
        dialog.eula.setChecked(True); dialog.memory.setValue(4096)
        with patch.object(self.service.game, 'ensure_java', return_value='java.exe'), patch('launcher.local_servers.subprocess.Popen', side_effect=spawn):
            dialog.start()
            wait_for(lambda: 'Done' in dialog.console.toPlainText())
            self.assertFalse(dialog.settings.isEnabled())
            self.assertIn('-Xmx4096M', children[0].args)
            dialog.command.setText('list'); dialog.send()
            wait_for(lambda: b'list\n' in children[0].writes)
            self.assertIn('本地服务端', self.window.update_block_reason())
            self.window.refresh(); self.window.navigate('downloads')
            buttons = [b for b in self.window.page.findChildren(Button) if b.text() == '控制台与配置']
            self.assertEqual(len(buttons), 1)
            self.assertEqual(self.window.server_task_signature, [(item['id'], 'running')])
            dialog.stop(False)
            wait_for(lambda: dialog.settings.isEnabled())
            self.assertIn(b'stop\n', children[0].writes)
            self.assertEqual(self.window.update_block_reason(), '')
            self.window.refresh()
            self.assertEqual(self.window.server_task_signature, [])

    def test_local_import_create_requires_eula_and_compact_english(self):
        item = self.local()
        set_language('en_US'); self.service.config_update({'language': 'en_US'}); self.window.refresh()
        self.window.resize(900, 600)
        self.window.navigate('servers')
        panel = self.window.server_panel
        QTest.mouseClick(panel.mode_switch.buttons['local'], Qt.LeftButton)
        wait_for(lambda: panel.table.topLevelItemCount() == 1)
        self.assertEqual(panel.mode_switch.buttons['local'].text(), 'Local hosting')
        self.assertTrue(panel.local)
        self.assertTrue(panel.mode_switch.buttons['local'].isChecked())
        self.assertFalse(panel.mode_switch.buttons['saved'].isChecked())
        QTest.mouseClick(panel.mode_switch.buttons['saved'], Qt.LeftButton)
        wait_for(lambda: panel.table.topLevelItemCount() == 0)
        self.assertFalse(panel.local)
        self.assertTrue(panel.join_button.isVisible())
        self.assertFalse(panel.download_button.isVisible())
        QTest.mouseClick(panel.mode_switch.buttons['local'], Qt.LeftButton)
        wait_for(lambda: panel.table.topLevelItemCount() == 1)
        dialog = self.window.open_dialog(LocalCreateDialog(self.window, False))
        dialog.versions.addItem('1.20.1', '1.20.1')
        dialog.save()
        self.assertIn('EULA', dialog.error_label.text())
        self.assertFalse(self.service.tasks.tasks)
        dialog.reject()
        console = self.window.open_dialog(LocalServerDialog(self.window, item['id']))
        wait_for(lambda: console.name.text() == 'Test server')
        QTest.qWait(250)
        for button in (console.start_button, console.stop_button, console.force_button):
            corner = button.mapTo(console, button.rect().bottomRight())
            self.assertLessEqual(corner.x(), console.width())
            self.assertLessEqual(corner.y(), console.height())

    def test_running_host_keeps_launcher_in_background(self):
        item = self.local()
        self.service.local_servers.save(item['id'], {'accept_eula': True})
        with patch.object(self.service.game, 'ensure_java', return_value='java.exe'), patch('launcher.local_servers.subprocess.Popen', side_effect=FakeServer):
            from launcher.core import Task
            self.service.local_servers.start(item['id'], Task('Start', None))
        self.window.close()
        self.assertTrue(self.window.background_mode)
        self.assertFalse(self.window.isVisible())
        with patch('launcher.desktop.QApplication.instance', return_value=APP) as _:
            self.window.refresh()
        self.assertTrue(self.window.timer.isActive() or self.window.background_mode)
        self.window.restore_window()


if __name__ == '__main__': unittest.main()
