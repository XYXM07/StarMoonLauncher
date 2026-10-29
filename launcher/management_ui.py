"""Native tools for bulk mods, dependency repair, Java, upgrades and diagnostics."""
import time
import urllib.parse
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QCheckBox, QFileDialog, QHeaderView, QPlainTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
from shiboken6 import isValid

from .desktop import NativeDialog, clear_layout, combo, field, label, row, text_input, human_bytes
from .i18n import tr
from .widgets import Button


def tree(headers, name):
    view = QTreeWidget()
    view.setObjectName(name)
    view.setHeaderLabels([tr(h) for h in headers])
    view.setMinimumHeight(190)
    view.setRootIsDecorated(False)
    view.setUniformRowHeights(True)
    view.setAlternatingRowColors(False)
    view.header().setSectionResizeMode(QHeaderView.ResizeToContents)
    view.header().setStretchLastSection(False)
    stretch = len(headers) - 1 if name == 'java-manager-table' else 1 if name == 'change-history' else 0
    view.header().setSectionResizeMode(stretch, QHeaderView.Stretch)
    view.setStyleSheet('QTreeWidget { background: rgba(255,255,255,28); border: 1px solid rgba(150,190,230,90); border-radius: 10px; } '
                      'QTreeWidget::item { min-height: 28px; padding: 2px 6px; border: none; } '
                      'QTreeWidget::item:selected { background: rgba(80,155,235,65); } '
                      'QTreeWidget::indicator { width: 15px; height: 15px; border: 1px solid rgba(86,126,171,160); border-radius: 4px; background: rgba(255,255,255,80); } '
                      'QTreeWidget::indicator:checked { background: rgba(55,140,235,220); border-color: rgba(55,140,235,240); } '
                      'QHeaderView { background: transparent; border: none; } '
                      'QHeaderView::section { background: rgba(225,240,255,90); border: none; padding: 8px 6px; }')
    return view


def query(**values): return urllib.parse.urlencode(values)


class ToolDialog(NativeDialog):
    def __init__(self, window, title, name):
        super().__init__(window, tr(title), width=820)
        self.window = window
        self.setObjectName(name)
        self.resize(min(820, window.width()), min(580, window.height()))
        self.job = None
        self.poller = QTimer(self)
        self.poller.setInterval(250)
        self.poller.timeout.connect(self.poll)
        self.finished.connect(self.poller.stop)

    def get(self, path, received):
        self.window.request(path, success=lambda value: received(value) if isValid(self) and self.isVisible() else None, error=self.error)

    def run(self, path, payload, done=None):
        if self.job: return
        self.error('')
        def started(result):
            if not isValid(self): return
            self.job, self.done = result['task_id'], done
            self.poller.start()
        self.window.request(path, payload, success=started, error=self.error)

    def poll(self):
        task = self.window.service.tasks.tasks.get(self.job)
        if not task: return
        state = task.public()
        if state['status'] in ('queued', 'running'): return
        self.job = None
        self.poller.stop()
        if state['status'] != 'completed': self.error(state.get('error') or tr('任务已取消'))
        elif self.done: self.done(state.get('result') or {})
        self.window.refresh()


class BulkModsDialog(ToolDialog):
    ACTIONS = [('enable', '启用'), ('disable', '禁用'), ('delete', '删除'), ('update', '更新'), ('lock', '锁定版本'), ('unlock', '解除锁定'), ('ignore', '忽略本次更新')]
    def __init__(self, window, identifier, update=False):
        super().__init__(window, '批量管理模组', 'bulk-mods-dialog')
        self.identifier, self.items, self.updates = identifier, [], {}
        self.search = text_input(placeholder=tr('搜索模组'))
        self.search.textChanged.connect(self.filter)
        self.body.addWidget(self.search)
        self.table = tree(['模组', '当前版本', '可用更新', '状态'], 'bulk-mods-table')
        self.body.addWidget(self.table, 1)
        self.actions = combo([(key, tr(title)) for key, title in self.ACTIONS], 'update' if update else 'enable', 'bulk-action')
        self.body.addLayout(row(Button(tr('全选可见项'), lambda: self.select(True)), Button(tr('取消选择'), lambda: self.select(False)), Button(tr('检查更新'), self.check_updates)))
        self.body.addWidget(Button(tr('按文件哈希识别更新来源'), lambda: self.run('content/identify', {'instance': self.identifier}, lambda _: self.reload(True))))
        self.footer.addWidget(self.actions)
        self.footer.addWidget(Button(tr('执行所选操作'), self.apply, True))
        self.body.addWidget(label(tr('更新前会保留原文件；可在版本概览的“变更记录”中恢复。'), 'muted', True))
        self.body.addWidget(label(tr('识别来源仅向 Modrinth 发送文件哈希，不上传模组文件。'), 'muted', True))
        QTimer.singleShot(0, lambda: self.reload(update))

    def reload(self, update=False):
        def received(items):
            self.items = [i for i in items if i['kind'] == 'mod']
            self.populate()
            if update: self.check_updates()
        self.get('content/local?' + query(instance=self.identifier), received)

    def populate(self):
        selected = set(self.selected())
        self.table.clear()
        for item in self.items:
            update = self.updates.get(item['filename'], {})
            state = tr('已启用') if item['enabled'] else tr('已禁用')
            if item.get('update_locked'): state += ' · ' + tr('锁定版本')
            node = QTreeWidgetItem([item.get('mod_name') or item['filename'], str(item.get('mod_version') or item.get('version_id') or '—'), str(update.get('new_name') or ''), state])
            node.setData(0, Qt.UserRole, item['filename'])
            node.setToolTip(0, item['filename'])
            node.setCheckState(0, Qt.Checked if item['filename'] in selected else Qt.Unchecked)
            self.table.addTopLevelItem(node)
        self.filter()

    def filter(self):
        for index in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(index)
            item.setHidden(self.search.text().casefold() not in (item.text(0) + item.toolTip(0)).casefold())

    def select(self, checked):
        for index in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(index)
            if not item.isHidden(): item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)

    def selected(self):
        return [self.table.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.table.topLevelItemCount()) if self.table.topLevelItem(i).checkState(0) == Qt.Checked]

    def check_updates(self):
        def received(values):
            self.updates = {i['filename']: i for i in values if i['kind'] == 'mod'}
            self.populate()
            self.error(tr('发现 {} 个更新').format(len(self.updates)))
        self.get('content/updates?' + query(instance=self.identifier), received)

    def apply(self):
        names, action = self.selected(), self.actions.currentData()
        if not names: return self.error(tr('请选择模组'))
        if action in ('update', 'ignore'):
            names = [name for name in names if name in self.updates]
            if not names: return self.error(tr('请先检查更新并选择可更新模组'))
        def execute():
            self.run('content/bulk', {'instance': self.identifier, 'action': action, 'files': names,
                     'versions': {name: str(self.updates[name]['new_version']) for name in names if name in self.updates}}, lambda _: self.reload(action == 'update'))
        if action in ('disable', 'delete'):
            def impact(edges):
                dialog = NativeDialog(self.window, tr('确认批量操作'), width=650)
                dialog.body.addWidget(label(tr('将对 {} 个模组执行：{}').format(len(names), tr(dict(self.ACTIONS)[action])), wrap=True))
                if edges:
                    dialog.body.addWidget(label(tr('以下模组依赖所选内容，操作后可能无法启动：'), 'muted', True))
                    text = QPlainTextEdit('\n'.join(e['source'] + ' → ' + e['target'] for e in edges))
                    text.setReadOnly(True)
                    dialog.body.addWidget(text)
                dialog.footer.addWidget(Button(tr('确认'), lambda: (dialog.accept(), execute()), danger=action == 'delete'))
                self.window.open_dialog(dialog)
            self.window.request('content/impact', {'instance': self.identifier, 'files': names}, success=impact, error=self.error)
        else: execute()


class HistoryDialog(ToolDialog):
    def __init__(self, window, identifier):
        super().__init__(window, '变更记录', 'change-history-dialog')
        self.identifier = identifier
        self.table = tree(['时间', '操作', '状态'], 'change-history')
        self.body.addWidget(self.table)
        self.body.addWidget(label(tr('恢复仅处理本次变更涉及的文件；若文件之后又被修改，会停止恢复。'), 'muted', True))
        self.body.addLayout(row(Button(tr('刷新'), self.reload), Button(tr('恢复所选变更'), self.restore)))
        QTimer.singleShot(0, self.reload)

    def reload(self):
        def received(items):
            self.table.clear()
            for value in items:
                item = QTreeWidgetItem([time.strftime('%Y-%m-%d %H:%M', time.localtime(value['time'])), tr(value['title']), tr('未完成') if value['status'] == 'applying' else tr('已完成')])
                item.setData(0, Qt.UserRole, value['id'])
                self.table.addTopLevelItem(item)
        self.get('management/history?' + query(instance=self.identifier), received)

    def restore(self):
        item = self.table.currentItem()
        if not item: return
        dialog = NativeDialog(self.window, tr('恢复所选变更'), width=520)
        dialog.body.addWidget(label(tr('将恢复到此操作之前的文件和版本配置。'), wrap=True))
        dialog.footer.addWidget(Button(tr('确认恢复'), lambda: (dialog.accept(), self.run('management/restore', {'instance': self.identifier, 'change': item.data(0, Qt.UserRole)}, lambda _: self.reload())), True))
        self.window.open_dialog(dialog)


class PackUpgradeDialog(ToolDialog):
    def __init__(self, window, identifier):
        super().__init__(window, '升级整合包', 'pack-upgrade-dialog')
        self.identifier = identifier
        instance = window.service.store.instance(identifier)
        self.provider = combo([(p, p) for p in ('modrinth', 'curseforge', 'atlauncher', 'ftb')], instance.get('pack_provider', 'modrinth'))
        self.project = text_input(str(instance.get('pack_project') or ''), tr('项目 ID'))
        self.current = text_input(str(instance.get('pack_version') or ''), tr('当前整合包版本 ID'))
        if instance.get('pack_provider'):
            self.provider.setEnabled(False)
            self.project.setReadOnly(True)
            self.current.setReadOnly(True)
        self.body.addLayout(row(field(tr('来源'), self.provider), field(tr('项目 ID'), self.project)))
        self.body.addWidget(field(tr('当前整合包版本 ID'), self.current))
        if not instance.get('pack_version'):
            self.body.addWidget(Button(tr('将列表所选设为当前版本'), self.use_current))
        self.versions = tree(['版本', '类型', '发布时间'], 'pack-upgrade-versions')
        self.body.addWidget(self.versions)
        self.body.addWidget(label(tr('下载完成后会显示文件差异；存档、截图和游戏选项保持不变。缺少原始清单时会下载旧版用于比对。'), 'muted', True))
        self.footer.addWidget(Button(tr('获取版本'), self.fetch))
        self.footer.addWidget(Button(tr('准备升级'), self.prepare, True))
        self.provider.currentIndexChanged.connect(self.versions.clear)
        self.project.textChanged.connect(self.versions.clear)
        if self.project.text(): QTimer.singleShot(0, self.fetch)

    def fetch(self):
        def received(values):
            self.versions.clear()
            for value in values:
                item = QTreeWidgetItem([value['name'], value.get('version_type', ''), value.get('date_published', '')[:10]])
                item.setData(0, Qt.UserRole, str(value['id']))
                self.versions.addTopLevelItem(item)
        self.get('content/versions?' + query(project=self.project.text().strip(), kind='modpack', provider=self.provider.currentData()), received)

    def prepare(self):
        item = self.versions.currentItem()
        if not item: return self.error(tr('请选择版本'))
        self.run('pack/upgrade', {'instance': self.identifier, 'provider': self.provider.currentData(), 'project': self.project.text().strip(),
                                 'version': item.data(0, Qt.UserRole), 'current_version': self.current.text().strip()}, lambda _: self.accept())

    def use_current(self):
        item = self.versions.currentItem()
        if item: self.current.setText(item.data(0, Qt.UserRole))


class PackReviewDialog(NativeDialog):
    def __init__(self, window, task):
        super().__init__(window, tr('确认整合包升级'), width=820)
        self.window, self.task_id, self.request_id = window, task['id'], task['interaction']['id']
        self.resolved = False
        self.resize(min(820, window.width()), min(580, window.height()))
        details = task['interaction']['details']
        self.body.addWidget(label(str(details['from']) + ' → ' + str(details['to']), 'section', True))
        if details.get('game_version'):
            self.body.addWidget(label('Minecraft ' + details['game_version'] + ' · ' + details['loader'] + ' ' + details.get('loader_version', ''), 'muted', True))
        self.body.addWidget(label(tr('本地修改冲突：{}').format(details['conflicts']), 'muted', True))
        self.table = tree(['文件', '变化', '本地修改'], 'pack-upgrade-diff')
        for value in details['changes']:
            self.table.addTopLevelItem(QTreeWidgetItem([value['path'], tr({'add': '新增', 'replace': '替换', 'remove': '删除'}[value['action']]), tr('有冲突') if value['conflict'] else '']))
        self.body.addWidget(self.table)
        self.body.addWidget(label(tr('保留本地修改会跳过冲突文件；采用整合包文件会替换或删除冲突文件。所有变更均保留恢复备份。'), 'muted', True))
        clear_layout(self.footer)
        self.footer.addWidget(Button(tr('取消'), self.reject))
        self.footer.addStretch()
        self.footer.addWidget(Button(tr('采用整合包文件'), lambda: self.respond('replace')))
        self.footer.addWidget(Button(tr('保留本地修改'), lambda: self.respond('keep'), True))

    def respond(self, choice):
        try: self.window.service.tasks.respond(self.task_id, self.request_id, choice)
        except Exception as exc: return self.error(str(exc))
        self.resolved = True
        self.accept()

    def reject(self):
        if not self.resolved:
            try: self.window.service.tasks.respond(self.task_id, self.request_id, 'cancel')
            except Exception: pass
        self.resolved = True
        super().reject()

    def expire(self):
        self.resolved = True
        super().reject()


class DependencyDialog(ToolDialog):
    STATUS = {'ok': '满足', 'missing': '缺失', 'disabled': '已禁用', 'mismatch': '版本不符', 'conflict': '不兼容', 'unknown': '无法确认'}
    def __init__(self, window, identifier):
        super().__init__(window, '依赖关系与修复', 'dependencies-dialog')
        self.identifier, self.edge = identifier, None
        self.scanning, self.scan_job, self.scan_generation = False, None, 0
        self.ai_check = QCheckBox(tr('使用大模型检查'))
        self.ai_check.setObjectName('dependency-ai-check')
        self.ai_check.setChecked(window.service.store.config().get('ai_dependency_check', True))
        self.ai_check.toggled.connect(self.change_mode)
        self.rescan = Button(tr('重新扫描'), self.reload)
        self.stop_scan = Button(tr('取消检查'), self.cancel_scan)
        self.stop_scan.setEnabled(False)
        self.body.addLayout(row(self.ai_check, self.rescan, self.stop_scan))
        self.analysis = label('', 'muted', True)
        self.analysis.setObjectName('dependency-analysis')
        self.analysis.setTextFormat(Qt.PlainText)
        self.body.addWidget(self.analysis)
        self.batch_details = QPlainTextEdit()
        self.batch_details.setObjectName('dependency-batch-results')
        self.batch_details.setReadOnly(True)
        self.batch_details.setMaximumHeight(140)
        self.batch_details.hide()
        self.body.addWidget(self.batch_details)
        self.table = tree(['模组 → 依赖', '要求', '当前版本', '状态'], 'dependency-tree')
        self.table.setRootIsDecorated(True)
        self.table.currentItemChanged.connect(self.select_edge)
        self.body.addWidget(self.table)
        self.enable_button = Button(tr('启用此前置'), self.enable)
        self.provider = combo([('modrinth', 'Modrinth'), ('curseforge', 'CurseForge')])
        self.search_button = Button(tr('查找兼容候选'), self.search)
        self.body.addLayout(row(self.provider, self.search_button, self.enable_button))
        self.projects, self.versions = combo([]), combo([])
        self.projects.currentIndexChanged.connect(self.fetch_versions)
        self.body.addLayout(row(self.projects, self.versions))
        self.install_button = Button(tr('安装并验证依赖'), self.install, True)
        self.footer.addWidget(self.install_button)
        self.body.addWidget(label(tr('候选名称不代表匹配；安装后会核对模组 ID 和版本范围，未满足时恢复原文件。'), 'muted', True))
        self.body.addWidget(label(tr('大模型共用设置中的接口，仅发送脱敏模组元数据；分析建议不会自动修改模组。'), 'muted', True))
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(150)
        self.scan_timer.timeout.connect(self.poll_scan)
        self.finished.connect(self.cancel_scan)
        QTimer.singleShot(0, self.reload)

    def change_mode(self, enabled):
        try:
            config = self.window.service.config_update({'ai_dependency_check': enabled})
            self.window.data['config'].update(config)
        except Exception as exc:
            self.ai_check.blockSignals(True)
            self.ai_check.setChecked(not enabled)
            self.ai_check.blockSignals(False)
            return self.error(str(exc))
        self.reload()

    def set_scanning(self, active):
        self.scanning = active
        for control in (self.ai_check, self.rescan, self.enable_button, self.search_button, self.install_button):
            control.setEnabled(not active)
        self.stop_scan.setEnabled(active)

    def cancel_scan(self, *_):
        self.scan_generation += 1
        if self.scan_job:
            task = self.window.service.tasks.tasks.get(self.scan_job)
            if task: task.cancel.set()
        self.scan_job = None
        self.scan_timer.stop()
        if self.scanning:
            self.analysis.setText(tr('检查已取消；保留本地依赖结果。'))
        self.set_scanning(False)

    def reload(self):
        if self.scanning or self.job or not self.isVisible(): return
        self.scan_generation += 1
        generation = self.scan_generation
        self.set_scanning(True)
        self.batch_details.hide()
        self.batch_details.clear()
        self.analysis.setText(tr('正在读取模组依赖…'))
        def active(): return isValid(self) and self.isVisible() and generation == self.scan_generation
        def failed(message):
            if not active(): return
            self.set_scanning(False)
            self.analysis.setText(tr('检查失败：{}').format(tr(str(message))))
        def started(result):
            if not active():
                task = self.window.service.tasks.tasks.get(result['task_id'])
                if task: task.cancel.set()
                return
            self.scan_job = result['task_id']
            self.scan_timer.start()
        def received(data):
            if not active(): return
            self.populate(data)
            if not self.ai_check.isChecked():
                return self.show_analysis(dict(data, ai_status='disabled'))
            if not self.window.service.diagnostics.configured():
                return self.show_analysis(dict(data, ai_status='not_configured'))
            self.analysis.setText(tr('大模型正在检查模组依赖'))
            self.window.request('dependencies/check', {'instance': self.identifier}, success=started, error=failed)
        self.window.request('dependencies?' + query(instance=self.identifier), success=received, error=failed)

    def poll_scan(self):
        task = self.window.service.tasks.tasks.get(self.scan_job)
        if not task: return
        state = task.public()
        if state['status'] in ('queued', 'running'):
            if state.get('message'): self.analysis.setText(tr(state['message']))
            return
        self.scan_job = None
        self.scan_timer.stop()
        if state['status'] == 'completed':
            self.populate(state['result'])
            self.show_analysis(state['result'])
        else:
            self.set_scanning(False)
            self.analysis.setText(tr('检查失败：{}').format(tr(state.get('error') or '任务已取消')))

    def show_analysis(self, data):
        self.set_scanning(False)
        messages = {
            'disabled': '已使用本地元数据检查。',
            'not_configured': '尚未配置大模型；已显示本地结果。请在设置 → 智能排查中填写接口地址、模型和 API Key。',
            'failed': '大模型检查失败，已保留本地结果：{}',
            'stale': '检查期间模组发生变化，已刷新本地结果；请重新运行大模型检查。',
        }
        status = data['ai_status']
        if status == 'completed':
            report = data['analysis']
            state = tr({'ok': '未发现明确问题', 'risk': '发现风险', 'unknown': '无法确认'}.get(report['status'], '无法确认'))
            self.analysis.setText(tr('大模型检查：{}\n原因：{}\n建议：{}').format(state, report['cause'], report['suggestion']))
            if report.get('batch_reports'):
                self.analysis.setText(self.analysis.text() + '\n' + tr('已完成 {} / {} 批检查').format(report['completed_batches'], report['batch_count']))
                self.batch_details.setPlainText('\n\n'.join(tr('第 {} 批\n原因：{}\n建议：{}').format(item['batch'], item['cause'], item['suggestion']) for item in report['batch_reports']))
                self.batch_details.show()
        else:
            self.analysis.setText(tr(messages[status]).format(tr(data.get('ai_error', ''))))

    def populate(self, data):
        self.table.clear()
        parents = {}
        for node in data['nodes']:
            key = (node.get('id'), node['file'])
            status = '已禁用' if node.get('enabled') is False else ('加载器不匹配' if node.get('incompatible_loader') else '')
            item = QTreeWidgetItem([node.get('id') or node['file'], '', node.get('version', ''), tr(status)])
            item.setToolTip(0, node['file'])
            self.table.addTopLevelItem(item)
            parents[key] = item
        for edge in data['edges']:
            parent = next((p for (name, file), p in parents.items() if name == edge['source'] and file.split('!')[0] == edge['file']), None)
            if parent is None: continue
            item = QTreeWidgetItem([('↔ ' if edge['kind'] == 'breaks' else '→ ') + edge['target'], str(edge['required']), str(edge['version'] or ''), tr(self.STATUS[edge['status']])])
            item.setData(0, Qt.UserRole, edge)
            parent.addChild(item)
            if edge['status'] != 'ok': parent.setExpanded(True)
        self.error('\n'.join(tr(i) for i in data['issues'][:5]))

    def select_edge(self, item, previous=None):
        self.edge = item.data(0, Qt.UserRole) if item else None
        self.projects.clear()
        self.versions.clear()

    def search(self):
        if not self.edge or self.edge['kind'] != 'depends': return self.error(tr('请选择需要修复的依赖'))
        dependency = self.edge['target']
        provider = self.provider.currentData()
        def received(items):
            if not self.edge or self.edge['target'] != dependency or self.provider.currentData() != provider: return
            self.projects.clear()
            for item in items: self.projects.addItem(item['title'], (str(item['project_id']), provider))
        self.get('dependencies/candidates?' + query(instance=self.identifier, dependency=dependency, provider=provider), received)

    def fetch_versions(self):
        self.versions.clear()
        selected = self.projects.currentData()
        if not selected: return
        def received(values):
            if self.projects.currentData() != selected: return
            for value in values: self.versions.addItem(value['name'], str(value['id']))
        self.get('content/versions?' + query(instance=self.identifier, project=selected[0], provider=selected[1], kind='mod'), received)

    def enable(self):
        if self.scanning: return
        if not self.edge or not self.edge.get('disabled_file'): return self.error(tr('请选择已禁用的依赖'))
        self.run('dependencies/repair', {'instance': self.identifier, 'dependency': self.edge['target'], 'disabled_file': self.edge['disabled_file']}, lambda _: self.reload())

    def install(self):
        if self.scanning: return
        selected = self.projects.currentData()
        if not self.edge or not selected or not self.versions.currentData(): return self.error(tr('请选择候选项目及版本'))
        self.run('dependencies/repair', {'instance': self.identifier, 'dependency': self.edge['target'], 'provider': selected[1], 'project': selected[0], 'version': self.versions.currentData()}, lambda _: self.reload())


class JavaDialog(ToolDialog):
    def __init__(self, window, identifier=None):
        super().__init__(window, 'Java 管理中心', 'java-manager-dialog')
        self.identifier = identifier
        self.table = tree(['Java', '架构', '占用空间', '使用情况', '路径'], 'java-manager-table')
        self.body.addWidget(self.table)
        self.major = combo([(v, 'Java ' + str(v)) for v in (8, 11, 17, 21, 25)], 21)
        self.arch = combo([('x64', 'x64'), ('aarch64', 'ARM64'), ('x86', 'x86')])
        self.body.addLayout(row(self.major, self.arch, Button(tr('下载 Java'), self.download)))
        self.body.addLayout(row(Button(tr('重新扫描'), self.reload), Button(tr('应用到此版本') if identifier else tr('设为全局默认'), self.choose), Button(tr('清理所选 Java'), self.remove)))
        self.body.addWidget(label(tr('仅清理未被引用的托管 Java，清理后移入回收目录。系统安装的 Java 保留。'), 'muted', True))
        QTimer.singleShot(0, self.reload)

    def reload(self):
        def received(items):
            self.table.clear()
            for value in items:
                item = QTreeWidgetItem([value['version'], value['architecture'], human_bytes(value['size']) if value['managed'] else '—', ', '.join(tr(v) for v in value['uses']), value['path']])
                item.setData(0, Qt.UserRole, value)
                item.setToolTip(4, value['path'])
                self.table.addTopLevelItem(item)
        self.get('java/manager', received)

    def download(self): self.run('java/download', {'major': self.major.currentData(), 'architecture': self.arch.currentData()}, lambda _: self.reload())

    def choose(self):
        item = self.table.currentItem()
        if not item: return
        path = item.data(0, Qt.UserRole)['path']
        def done(_):
            if self.identifier is None:
                self.window.settings_draft['java_path'] = path
                widget = self.window.settings_fields.get('java_path')
                if widget and isValid(widget): widget.setText(path)
            else:
                self.window.instance_drafts.setdefault(self.identifier, {})['java_path'] = path
                panel = getattr(self.window, 'instance_panel', None)
                if panel and isValid(panel) and panel.instance_id == self.identifier:
                    widget = getattr(panel, 'instance_fields', {}).get('java_path')
                    if widget and isValid(widget): widget.setText(path)
            self.reload()
        self.window.request('instances/update' if self.identifier else 'config', dict({'id': self.identifier} if self.identifier else {}, java_path=path), success=done, error=self.error)

    def remove(self):
        item = self.table.currentItem()
        if not item: return
        self.run('java/remove', {'path': item.data(0, Qt.UserRole)['path']}, lambda _: self.reload())


class LogPanel(QWidget):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel, self.window, self.identifier = panel, panel.window, panel.instance_id
        self.busy, self.generation = False, 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.files = combo([], name='report-log-file')
        self.search = text_input(placeholder=tr('搜索日志'), name='report-log-search')
        self.level = combo([('all', tr('全部')), ('warn', 'WARN+'), ('error', 'ERROR / FATAL')])
        self.follow = QCheckBox(tr('自动刷新'))
        self.follow.setChecked(True)
        layout.addWidget(self.files)
        layout.addLayout(row(self.search, self.level, self.follow))
        self.text = QPlainTextEdit()
        self.text.setObjectName('report-log-text')
        self.text.setReadOnly(True)
        self.text.setPlaceholderText(tr('暂无日志'))
        self.text.setMinimumHeight(220)
        layout.addWidget(self.text, 1)
        layout.addLayout(row(Button(tr('刷新列表'), self.reload), Button(tr('首个错误'), self.first_error), Button(tr('导出问题报告'), self.export)))
        layout.addWidget(label(tr('显示日志末尾最多 1 MB；报告导出到本地，分享前可检查脱敏结果。'), 'muted', True))
        self.files.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.level.currentIndexChanged.connect(self.refresh)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(lambda: (self.refresh() if self.files.currentData() else self.reload()) if self.follow.isChecked() and self.isVisible() else None)
        self.timer.start()
        QTimer.singleShot(0, self.reload)

    def reload(self):
        def received(items):
            if not isValid(self) or not self.panel.active(): return
            current = self.files.currentData()
            self.files.blockSignals(True)
            self.files.clear()
            for item in items: self.files.addItem(item['name'], item['id'])
            index = self.files.findData(current)
            if index >= 0: self.files.setCurrentIndex(index)
            self.files.blockSignals(False)
            self.refresh()
        self.window.request('reports/logs?' + query(instance=self.identifier), success=received, error=self.panel.error)

    def refresh(self):
        if not self.files.currentData() or self.busy: return
        self.busy = True
        selected = (self.files.currentData(), self.search.text(), self.level.currentData())
        def received(value):
            if not isValid(self): return
            self.busy = False
            if selected != (self.files.currentData(), self.search.text(), self.level.currentData()): return self.refresh()
            if self.text.toPlainText() != value['text']:
                bar = self.text.verticalScrollBar()
                follow = bar.value() >= bar.maximum() - 5
                position = bar.value()
                self.text.setPlainText(value['text'])
                bar.setValue(bar.maximum() if follow else position)
        def failed(message):
            if isValid(self):
                self.busy = False
                self.panel.error(message)
        self.window.request('reports/read?' + query(instance=self.identifier, log=selected[0], search=selected[1], level=selected[2]), success=received, error=failed)

    def first_error(self):
        cursor = self.text.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.text.setTextCursor(cursor)
        for line in self.text.toPlainText().splitlines():
            if 'ERROR' in line or 'FATAL' in line or 'Exception' in line:
                self.text.find(line)
                return
            cursor.movePosition(QTextCursor.NextBlock)

    def export(self):
        filename, _ = QFileDialog.getSaveFileName(self, tr('导出问题报告'), 'StarMoon-report-' + time.strftime('%Y%m%d-%H%M%S') + '.zip', 'ZIP (*.zip)')
        if filename: self.window.mutate('reports/export', {'instance': self.identifier, 'destination': filename}, show_tasks=True)
