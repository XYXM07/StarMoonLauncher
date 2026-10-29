"""Native saved-server list and local server console/configuration."""
import json
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QGridLayout, QMessageBox, QPlainTextEdit, QSpinBox, QTreeWidgetItem, QVBoxLayout, QWidget
from shiboken6 import isValid

from .desktop import NativeDialog, combo, field, label, row, text_input
from .i18n import tr
from .management_ui import ToolDialog, query, tree
from .widgets import Button, NavigationSwitch


STATES = {'starting': '正在启动', 'running': '运行中', 'stopping': '正在停止', 'stopped': '已停止'}


class ServerPanel(QWidget):
    def __init__(self, window):
        super().__init__(window.page)
        self.window = window
        self.setObjectName('server-manager-panel')
        self.local = getattr(window, 'server_mode', False)
        self.loading, self.generation, self.signature = False, 0, None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mode_switch = NavigationSwitch((('saved', tr('服务器列表')), ('local', tr('本地开服'))))
        self.mode_switch.setObjectName('server-mode-switch')
        self.mode_switch.selected.connect(lambda key: self.set_mode(key == 'local'))
        layout.addWidget(self.mode_switch)
        self.search = text_input(placeholder=tr('搜索服务器'), name='server-search')
        self.search.textChanged.connect(self.filter)
        layout.addWidget(self.search)
        self.table = tree(['名称', '地址 / 文件夹', '状态'], 'server-table')
        self.table.setMinimumHeight(170)
        self.table.currentItemChanged.connect(self.selected)
        self.table.itemDoubleClicked.connect(lambda *_: self.manage() if self.local else self.join())
        layout.addWidget(self.table, 1)
        self.details = label('', 'muted', True)
        self.details.setTextFormat(Qt.PlainText)
        layout.addWidget(self.details)
        self.add_button = Button(tr('添加服务器'), self.add)
        self.edit_button = Button(tr('编辑'), self.manage)
        self.remove_button = Button(tr('移除记录'), self.remove)
        self.refresh_button = Button(tr('查询状态'), self.ping)
        layout.addLayout(row(self.add_button, self.edit_button, self.remove_button, self.refresh_button))
        self.download_button = Button(tr('下载原版服务端'), lambda: self.create_local(False))
        layout.addWidget(self.download_button)
        self.instances = combo([(i['id'], i['name']) for i in window.service.store.instances()], window.data['config'].get('selected_instance'), 'server-game-version')
        self.copy_button = Button(tr('复制地址'), self.copy)
        self.join_button = Button(tr('加入服务器'), self.join, True)
        layout.addLayout(row(self.instances, self.copy_button, self.join_button))
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(lambda: self.reload() if self.local and self.isVisible() else None)
        self.timer.start()
        self.set_mode(self.local, False)

    def current(self):
        item = self.table.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def set_mode(self, local, animated=True):
        self.local, self.window.server_mode = local, local
        self.generation += 1
        self.loading, self.signature = False, None
        self.table.clear()
        self.mode_switch.select('local' if local else 'saved', animated and self.window.animations)
        self.add_button.setText(tr('导入服务端') if local else tr('添加服务器'))
        self.edit_button.setText(tr('控制台与配置') if local else tr('编辑'))
        self.refresh_button.setText(tr('刷新') if local else tr('查询状态'))
        self.download_button.setVisible(local)
        self.copy_button.setVisible(not local)
        self.instances.setVisible(not local)
        self.join_button.setVisible(not local)
        self.reload()

    def reload(self):
        if self.loading: return
        self.loading = True
        generation = self.generation
        def received(items):
            if not isValid(self) or generation != self.generation: return
            self.loading = False
            signature = json.dumps(items, sort_keys=True)
            if signature == self.signature: return
            self.signature = signature
            selected = (self.current() or {}).get('id')
            self.table.clear()
            for value in items:
                text = tr(STATES[value['state']]) if self.local else tr('未查询')
                item = QTreeWidgetItem([value['name'], value.get('folder', value.get('address', '')), text])
                item.setData(0, Qt.UserRole, value)
                self.table.addTopLevelItem(item)
                if value['id'] == selected: self.table.setCurrentItem(item)
            if self.table.currentItem() is None and items: self.table.setCurrentItem(self.table.topLevelItem(0))
            self.filter()
            if not items: self.details.setText(tr('尚未添加本地服务端') if self.local else tr('尚未添加服务器'))
        def failed(message):
            if isValid(self) and generation == self.generation:
                self.loading = False
                self.details.setText(tr(message))
        self.window.request('local-servers' if self.local else 'servers', success=received, error=failed)

    def filter(self):
        text = self.search.text().casefold()
        for index in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(index)
            item.setHidden(text not in (item.text(0) + item.text(1)).casefold())

    def selected(self, item, *_):
        value = item.data(0, Qt.UserRole) if item else {}
        self.details.setText(str(value.get('notes', value.get('folder', ''))))
        if value.get('instance'):
            self.instances.setCurrentIndex(self.instances.findData(value['instance']))

    def add(self):
        if self.local: return self.create_local(True)
        self.window.open_dialog(ServerEditDialog(self.window, self.reload))

    def manage(self):
        value = self.current()
        if not value: return
        dialog = LocalServerDialog(self.window, value['id']) if self.local else ServerEditDialog(self.window, self.reload, value)
        dialog.finished.connect(self.reload)
        self.window.open_dialog(dialog)

    def create_local(self, importing):
        dialog = LocalCreateDialog(self.window, importing)
        dialog.finished.connect(self.reload)
        self.window.open_dialog(dialog)

    def remove(self):
        value = self.current()
        if not value: return
        message = tr('只移除管理记录，保留服务端文件和存档。') if self.local else tr('从服务器列表中移除此记录？')
        if QMessageBox.question(self.window, tr('移除记录'), message) != QMessageBox.Yes: return
        self.window.request(('local-servers' if self.local else 'servers') + '/remove', {'id': value['id']}, success=lambda _: self.reload())

    def copy(self):
        if self.current(): QApplication.clipboard().setText(self.current()['address'])

    def ping(self):
        if self.local: return self.reload()
        value, item = self.current(), self.table.currentItem()
        if not value: return
        identifier, generation = value['id'], self.generation
        item.setText(2, tr('查询中'))
        def received(result):
            if not isValid(self) or generation != self.generation: return
            for index in range(self.table.topLevelItemCount()):
                current = self.table.topLevelItem(index)
                if current.data(0, Qt.UserRole)['id'] == identifier:
                    current.setText(2, tr('{} / {} 人 · {} ms').format(result['players'], result['max_players'], result['latency']) if result['online'] else tr('不可达'))
            if (self.current() or {}).get('id') == identifier:
                self.details.setText(result['version'] + '\n' + result['motd'] if result['online'] else tr(result['error']))
        self.window.request('servers/status?' + query(address=value['address']), success=received)

    def join(self):
        value, instance = self.current(), self.instances.currentData()
        if not value: return
        if not instance: return self.window.toast(tr('请先选择一个游戏版本'))
        account = self.window.account()
        if not account: return self.window.toast(tr('请先添加或登录一个账号'))
        self.window.mutate('instances/launch', {'id': instance, 'account': account['id'], 'server': value['address']}, show_tasks=True)


class ServerEditDialog(NativeDialog):
    def __init__(self, window, updated, value=None):
        super().__init__(window, tr('编辑服务器') if value else tr('添加服务器'), width=600)
        self.setObjectName('server-edit-dialog')
        self.value, self.updated = value or {}, updated
        self.name = text_input(self.value.get('name', ''), name='server-name')
        self.address = text_input(self.value.get('address', ''), 'play.example.com:25565', 'server-address')
        self.notes = text_input(self.value.get('notes', ''), name='server-notes')
        self.instance = combo([('', tr('每次选择'))] + [(i['id'], i['name']) for i in window.service.store.instances()], self.value.get('instance', ''), 'server-instance')
        for title, widget in [('名称', self.name), ('服务器地址', self.address), ('游戏版本', self.instance), ('备注', self.notes)]:
            self.body.addWidget(field(tr(title), widget))
        self.footer.addWidget(Button(tr('保存'), self.save, True))

    def save(self):
        data = dict(self.value, name=self.name.text(), address=self.address.text(), notes=self.notes.text(), instance=self.instance.currentData())
        self.window.request('servers/save', data, success=lambda _: (self.accept(), self.updated()), error=self.error)


class LocalCreateDialog(ToolDialog):
    def __init__(self, window, importing):
        super().__init__(window, '导入服务端' if importing else '下载原版服务端', 'local-server-create')
        self.importing = importing
        self.name = text_input(name='local-server-name')
        self.folder = text_input('' if importing else str(window.service.store.root / 'servers' / ('server-' + uuid.uuid4().hex[:8])), name='local-server-folder')
        self.entry = text_input('server.jar', name='local-server-entry')
        self.java_major = QSpinBox(); self.java_major.setRange(8, 99); self.java_major.setValue(21)
        self.versions = combo([], name='server-download-version')
        self.body.addWidget(field(tr('名称'), self.name))
        self.body.addWidget(field(tr('服务端文件夹'), self.folder))
        self.body.addWidget(Button(tr('选择文件夹'), self.choose_folder))
        if importing:
            self.body.addWidget(field(tr('启动文件'), self.entry))
            self.body.addWidget(Button(tr('选择 JAR / 参数文件'), self.choose_entry))
            self.body.addWidget(field(tr('所需 Java 主版本'), self.java_major))
            self.body.addWidget(label(tr('支持服务端 JAR，以及已安装 Forge/NeoForge 的 win_args.txt。'), 'muted', True))
        else:
            self.body.addWidget(field(tr('游戏版本'), self.versions))
            self.body.addWidget(Button(tr('获取版本列表'), self.fetch_versions))
            self.eula = QCheckBox(tr('我已阅读并同意 Minecraft EULA'))
            self.body.addWidget(self.eula)
            self.body.addWidget(Button(tr('阅读 EULA'), lambda: window.open_content_url('https://www.minecraft.net/eula')))
        self.submit = Button(tr('导入') if importing else tr('下载并添加'), self.save, True)
        self.footer.addWidget(self.submit)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, tr('服务端文件夹'), self.folder.text())
        if folder: self.folder.setText(folder)

    def choose_entry(self):
        filename, _ = QFileDialog.getOpenFileName(self, tr('启动文件'), self.folder.text(), 'Java (*.jar *.txt)')
        if filename:
            try: self.entry.setText(Path(filename).resolve().relative_to(Path(self.folder.text()).resolve()).as_posix())
            except ValueError: self.error(tr('启动文件必须位于服务端文件夹内'))

    def fetch_versions(self):
        def received(data):
            self.versions.clear()
            for version in data['versions']:
                if version['type'] == 'release': self.versions.addItem(version['id'], version['id'])
        self.get('manifest', received)

    def save(self):
        data = {'name': self.name.text(), 'folder': self.folder.text()}
        if self.importing:
            data.update(entry=self.entry.text(), java_major=self.java_major.value())
            self.window.request('local-servers/import', data, success=lambda _: self.accept(), error=self.error)
        else:
            if not self.versions.currentData(): return self.error(tr('请先获取并选择版本'))
            if not self.eula.isChecked(): return self.error(tr('请先阅读并同意 Minecraft EULA'))
            data.update(version=self.versions.currentData(), accept_eula=True)
            self.run('local-servers/create', data, lambda _: self.accept())


class LocalServerDialog(ToolDialog):
    def __init__(self, window, identifier):
        super().__init__(window, '控制台与配置', 'local-server-console')
        self.identifier, self.cursor, self.run_id, self.polling = identifier, 0, '', False
        self.loaded = False
        self.status_label = label(tr('正在读取配置'), 'muted', True)
        self.body.addWidget(self.status_label)
        self.console = QPlainTextEdit()
        self.console.setObjectName('server-console-output')
        self.console.setReadOnly(True)
        self.console.document().setMaximumBlockCount(1500)
        self.console.setMinimumHeight(160)
        self.body.addWidget(self.console)
        self.command = text_input(placeholder=tr('输入服务端命令，例如 list'), name='server-console-command')
        self.command.returnPressed.connect(self.send)
        self.body.addLayout(row(self.command, Button(tr('发送命令'), self.send)))
        self.settings = QWidget()
        grid = QGridLayout(self.settings)
        self.name, self.java, self.jvm = text_input(), text_input(), text_input()
        self.memory = QSpinBox(); self.memory.setRange(512, 65536); self.memory.setSingleStep(512)
        self.major = QSpinBox(); self.major.setRange(8, 99)
        self.fields = {}
        controls = [('名称', self.name), ('Java 路径（留空自动选择）', self.java), ('服务端内存 / MB', self.memory), ('所需 Java 主版本', self.major), ('额外 JVM 参数', self.jvm)]
        for key, title, low, high in [('server-port', '服务端端口', 1, 65535), ('max-players', '最大玩家数', 1, 10000), ('view-distance', '视距', 2, 32)]:
            widget = QSpinBox(); widget.setRange(low, high)
            self.fields[key] = widget; controls.append((title, widget))
        self.fields['motd'] = text_input(); controls.append(('服务器简介', self.fields['motd']))
        for key, title, values in [('difficulty', '难度', [('peaceful', '和平'), ('easy', '简单'), ('normal', '普通'), ('hard', '困难')]),
                                   ('gamemode', '游戏模式', [('survival', '生存'), ('creative', '创造'), ('adventure', '冒险'), ('spectator', '旁观')])]:
            self.fields[key] = combo(values); controls.append((title, self.fields[key]))
        for key, title in [('online-mode', '正版验证'), ('white-list', '启用白名单'), ('pvp', '允许 PVP')]:
            self.fields[key] = QCheckBox(tr(title)); controls.append(('', self.fields[key]))
        for index, (title, widget) in enumerate(controls): grid.addWidget(field(tr(title), widget) if title else widget, index // 2, index % 2)
        self.body.addWidget(self.settings)
        self.eula = QCheckBox(tr('我已阅读并同意 Minecraft EULA'))
        self.body.addWidget(self.eula)
        self.apply_button = Button(tr('应用配置'), self.save)
        self.body.addLayout(row(Button(tr('阅读 EULA'), lambda: window.open_content_url('https://www.minecraft.net/eula')), Button(tr('打开文件夹'), self.open_folder), self.apply_button))
        self.body.addWidget(label(tr('停止使用 stop 命令保存世界；强制结束可能丢失未保存进度。公网连接需自行配置网络。'), 'muted', True))
        self.start_button = Button(tr('启动服务端'), self.start, True)
        self.stop_button = Button(tr('停止服务端'), lambda: self.stop(False))
        self.force_button = Button(tr('强制结束'), lambda: self.stop(True), danger=True)
        self.footer.addWidget(self.start_button)
        self.footer.addWidget(self.stop_button)
        self.footer.addWidget(self.force_button)
        for widget in (self.settings, self.eula, self.apply_button, self.start_button, self.stop_button, self.force_button): widget.setEnabled(False)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(700)
        self.refresh_timer.timeout.connect(self.refresh_console)
        self.finished.connect(self.refresh_timer.stop)
        QTimer.singleShot(0, self.load)

    def load(self):
        def received(data):
            self.name.setText(data['name']); self.java.setText(data['java_path']); self.jvm.setText(data['jvm_args'])
            self.memory.setValue(data['memory']); self.major.setValue(data['java_major']); self.eula.setChecked(data['eula'])
            for key, widget in self.fields.items():
                value = data['properties'][key]
                if isinstance(widget, QCheckBox): widget.setChecked(value == 'true')
                elif isinstance(widget, QSpinBox): widget.setValue(int(value))
                elif hasattr(widget, 'findData'): widget.setCurrentIndex(widget.findData(value))
                else: widget.setText(value)
            self.loaded = True
            self.refresh_timer.start()
            self.refresh_console()
        self.get('local-servers/config?' + query(id=self.identifier), received)

    def values(self):
        props = {key: ('true' if widget.isChecked() else 'false') if isinstance(widget, QCheckBox) else str(widget.value()) if isinstance(widget, QSpinBox) else widget.currentData() if hasattr(widget, 'currentData') else widget.text() for key, widget in self.fields.items()}
        return {'id': self.identifier, 'name': self.name.text(), 'java_path': self.java.text(), 'jvm_args': self.jvm.text(),
                'memory': self.memory.value(), 'java_major': self.major.value(), 'properties': props, 'accept_eula': self.eula.isChecked()}

    def save(self, then=None):
        if not self.loaded: return
        self.window.request('local-servers/save', self.values(), success=lambda _: then() if then else self.error(tr('配置已应用')), error=self.error)

    def start(self):
        self.save(lambda: self.run('local-servers/start', {'id': self.identifier}, lambda _: self.refresh_console()))

    def stop(self, force):
        if force and QMessageBox.question(self, tr('强制结束'), tr('强制结束可能丢失未保存的世界进度，继续？')) != QMessageBox.Yes: return
        self.window.request('local-servers/stop', {'id': self.identifier, 'force': force}, success=lambda _: self.refresh_console(), error=self.error)

    def send(self):
        value = self.command.text()
        self.window.request('local-servers/command', {'id': self.identifier, 'command': value}, success=lambda _: self.command.clear(), error=self.error)

    def open_folder(self):
        folder = self.window.service.local_servers.get(self.identifier)['folder']
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def refresh_console(self):
        if self.polling or not self.isVisible(): return
        self.polling = True
        def received(data):
            if not isValid(self): return
            self.polling = False
            if data['run_id'] != self.run_id: self.console.clear()
            self.run_id, self.cursor = data['run_id'], data['cursor']
            bar = self.console.verticalScrollBar()
            bottom = bar.value() >= bar.maximum() - 3
            if data['lines']:
                cursor = QTextCursor(self.console.document()); cursor.movePosition(QTextCursor.End)
                cursor.insertText('\n'.join(data['lines']) + '\n')
                if bottom: bar.setValue(bar.maximum())
            state = data['status']
            self.status_label.setText(tr(STATES[state['state']]) + (' · PID ' + str(state['pid']) if state.get('pid') else '') + (' · ' + tr('退出代码：{}').format(state['exit_code']) if state.get('exit_code') is not None else ''))
            self.start_button.setEnabled(not state['running'] and not self.job)
            self.stop_button.setEnabled(state['state'] == 'running')
            self.force_button.setEnabled(state['state'] in ('running', 'stopping'))
            for widget in (self.settings, self.eula, self.apply_button): widget.setEnabled(self.loaded and not state['running'])
        def failed(message):
            if isValid(self): self.polling = False; self.error(message)
        self.window.request('local-servers/console?' + query(id=self.identifier, after=self.cursor, run_id=self.run_id), success=received, error=failed)
