"""Pack export options and paged, asynchronous screenshot previews."""
import time

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import QCheckBox, QFileDialog, QGridLayout, QLabel, QListWidget, QListWidgetItem, QSizePolicy, QSpinBox, QWidget, QVBoxLayout
from shiboken6 import isValid

from .desktop import NativeDialog, clear_layout, combo, field, label, row, text_input
from .i18n import tr
from .pack_export import DEFAULT_GROUPS
from .widgets import Button, card


def add_launch_options(panel, current):
    from .launch_options import DEFAULTS, RENDERERS
    frame,box=card()
    frame.setObjectName("instance-launch-options")
    box.addWidget(label(tr("高级启动设置"),"section"))
    renderer=combo([(key,tr(title)) for key,title in RENDERERS],current.get("renderer","opengl"),"instance-renderer")
    panel.instance_fields["renderer"]=renderer
    box.addWidget(field(tr("渲染器"),renderer))
    box.addWidget(label(tr("Vulkan 和 DirectX 12 通过 Mesa 转换 OpenGL；首次启动下载运行库，需要显卡驱动支持。"),"muted",True))
    switches=(
        ("no_default_jvm_args",tr("不添加默认的 JVM 参数"),tr("不注入默认内存与编码参数；保留版本必需参数和自定义 JVM 参数。")),
        ("skip_game_check",tr("不检查游戏完整性"),tr("跳过已安装游戏启动前的文件校验；首次安装和手动修复仍会检查。")),
        ("skip_java_check",tr("不检查 JVM 与游戏的兼容性"),tr("允许使用主版本不匹配的指定 Java；Java 程序仍须有效。")),
        ("skip_native_patch",tr("不尝试自动替换本地库"),tr("使用版本原有的本地库选择规则，不按所选 Java 的架构自动适配。")),
    )
    for key,title,tip in switches:
        widget=QCheckBox(title)
        widget.setObjectName("instance-"+key.replace("_","-"))
        widget.setChecked(current.get(key,DEFAULTS[key]))
        widget.setToolTip(tip)
        panel.instance_fields[key]=widget
        box.addWidget(widget)
    panel.panel.addWidget(frame)


class PackExportDialog(NativeDialog):
    def __init__(self, window, instance_id):
        super().__init__(window, tr("导出整合包"), width=720)
        self.window, self.instance_id = window, instance_id
        self.setObjectName("pack-export-dialog")
        self.resize(min(720, window.width()), min(680, window.height()))
        instance = window.service.store.instance(instance_id)
        self.name = text_input(instance["name"], name="export-name")
        self.version = text_input("1.0.0", name="export-version")
        self.author = text_input(name="export-author")
        self.summary = text_input(name="export-summary")
        self.format = combo([("starmoon", tr("星月 ZIP")), ("modrinth", "Modrinth .mrpack"), ("curseforge", "CurseForge ZIP")], "starmoon", "export-format")
        metadata = QGridLayout()
        metadata.setColumnStretch(0, 2)
        metadata.setColumnStretch(1, 1)
        for index, (title, widget) in enumerate((("整合包名称", self.name), ("整合包版本", self.version),
                                                ("作者", self.author), ("简介", self.summary))):
            metadata.addWidget(field(tr(title), widget), index // 2, index % 2)
        self.body.addLayout(metadata)
        self.compression = QSpinBox()
        self.compression.setRange(0, 9)
        self.compression.setValue(6)
        self.body.addLayout(row(field(tr("导出格式"), self.format), field(tr("压缩级别 / 0–9"), self.compression)))
        self.groups = {}
        choices = QGridLayout()
        for index, (key, title) in enumerate((("mods", "模组"), ("config", "配置"), ("resourcepacks", "资源包"),
                ("shaderpacks", "光影"), ("scripts", "脚本"), ("saves", "存档"), ("screenshots", "截图"), ("options", "游戏选项"))):
            check = QCheckBox(tr(title))
            check.setObjectName("export-" + key)
            check.setChecked(key in DEFAULT_GROUPS)
            choices.addWidget(check, index // 2, index % 2)
            self.groups[key] = check
        self.body.addWidget(label(tr("导出内容"), "section"))
        self.body.addLayout(choices)
        self.disabled = QCheckBox(tr("包含已禁用模组"))
        self.disabled.setChecked(True)
        self.body.addWidget(self.disabled)
        self.destination = text_input(str(window.service.store.root / "exports"), name="export-destination")
        self.destination.setReadOnly(True)
        self.body.addWidget(label(tr("保存文件夹"), "fieldLabel"))
        self.body.addLayout(row(self.destination, Button(tr("选择文件夹"), self.choose_folder)))
        self.body.addWidget(label(tr("星月 ZIP 保留自定义窗口标题；其他格式使用对应平台的标准清单。"), "muted", True))
        clear_layout(self.footer)
        self.footer.addStretch()
        self.footer.addWidget(Button(tr("取消"), self.reject))
        self.submit = Button(tr("开始导出"), self.export, True)
        self.submit.setObjectName("export-submit")
        self.footer.addWidget(self.submit)

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, tr("选择导出文件夹"), self.destination.text())
        if path:
            self.destination.setText(path)

    def export(self):
        include = [key for key, widget in self.groups.items() if widget.isChecked()]
        if not include:
            self.error(tr("请至少选择一项有效的导出内容"))
            return
        options = {"format": self.format.currentData(), "include": include, "include_disabled": self.disabled.isChecked(),
                   "destination": self.destination.text(), "name": self.name.text(), "version": self.version.text(),
                   "author": self.author.text(), "summary": self.summary.text(), "compression": self.compression.value()}
        self.window.mutate("pack/export", {"instance": self.instance_id, "options": options}, lambda _: self.accept(), show_tasks=True)


class ScreenshotSignals(QObject):
    ready = Signal(int, int, object)


class ScreenshotJob(QRunnable):
    def __init__(self, path, generation, index, size):
        super().__init__()
        self.path, self.generation, self.index, self.size = path, generation, index, size
        self.signals = ScreenshotSignals()

    def run(self):
        image = QImage()
        try:
            reader = QImageReader(str(self.path))
            reader.setAutoTransform(True)
            size = reader.size()
            if bytes(reader.format()).lower() in (b"png", b"jpeg", b"webp") and size.isValid() and max(size.width(), size.height()) <= 16384 and size.width() * size.height() <= 100000000:
                reader.setScaledSize(size.scaled(self.size, Qt.KeepAspectRatio))
                image = reader.read()
        except (OSError, ValueError):
            pass
        self.signals.ready.emit(self.generation, self.index, image)


class ScreenshotsPanel(QWidget):
    def __init__(self, window, instance_id):
        super().__init__()
        self.window, self.instance_id = window, instance_id
        self.items, self.jobs = [], {}
        self.offset, self.generation = 0, 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row(Button(tr("刷新"), self.load), Button(tr("截图文件夹"), self.open_folder)))
        self.info = label("", "muted")
        layout.addWidget(self.info)
        self.list = QListWidget()
        self.list.setObjectName("screenshot-gallery")
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setIconSize(QSize(200, 120))
        self.list.setGridSize(QSize(224, 158))
        self.list.setMinimumHeight(280)
        self.list.itemDoubleClicked.connect(self.preview)
        layout.addWidget(self.list)
        self.previous = Button(tr("上一页"), lambda: self.page(-1))
        self.next = Button(tr("下一页"), lambda: self.page(1))
        layout.addLayout(row(self.previous, self.next, Button(tr("查看截图"), self.preview)))
        self.load()

    def open_folder(self):
        self.window.mutate("folder", {"instance": self.instance_id, "target": "screenshots"})

    def load(self):
        from .screenshots import listing
        self.generation += 1
        generation = self.generation
        self.info.setText(tr("正在读取…"))
        def received(items):
            if isValid(self) and self.generation == generation:
                self.items, self.offset = items, 0
                self.show_page()
        self.window.async_call(lambda: listing(self.window.service.store, self.instance_id), success=received)

    def page(self, direction):
        self.offset = max(0, min(max(0, (len(self.items)-1)//20*20), self.offset + direction*20))
        self.show_page()

    def show_page(self):
        self.generation += 1
        generation = self.generation
        self.list.clear()
        self.info.setText(tr("共 {} 张截图").format(len(self.items)) if self.items else tr("暂无截图，游戏中按 F2 拍摄"))
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + 20 < len(self.items))
        for index, item in enumerate(self.items[self.offset:self.offset+20]):
            node = QListWidgetItem(item["name"])
            node.setToolTip(item["name"] + "\n" + time.strftime("%Y-%m-%d %H:%M", time.localtime(item["modified"])))
            node.setData(Qt.UserRole, item["name"])
            self.list.addItem(node)
            job = ScreenshotJob(item["path"], generation, index, QSize(200, 120))
            job.signals.ready.connect(self.received)
            self.jobs[(generation, index)] = job
            self.window.images.pool.start(job)
        if self.list.count():
            self.list.setCurrentRow(0)

    def received(self, generation, index, image):
        self.jobs.pop((generation, index), None)
        if generation == self.generation and index < self.list.count() and not image.isNull():
            self.list.item(index).setIcon(QIcon(QPixmap.fromImage(image)))

    def preview(self, *unused):
        from .screenshots import resolve
        node = self.list.currentItem()
        if node is None:
            return
        try:
            path = resolve(self.window.service.store, self.instance_id, node.data(Qt.UserRole))
        except Exception as error:
            self.window.toast(str(error), True)
            return
        dialog = NativeDialog(self.window, path.name, width=800)
        dialog.setObjectName("screenshot-preview")
        dialog.resize(min(800, self.window.width()), min(600, self.window.height()))
        picture = QLabel()
        picture.setObjectName("screenshot-image")
        picture.setAlignment(Qt.AlignCenter)
        picture.setMinimumHeight(260)
        dialog.body.addWidget(picture)
        def open_original():
            try:
                original = resolve(self.window.service.store, self.instance_id, path.name)
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(original))):
                    dialog.error(tr("无法打开图片查看器"))
            except Exception as error:
                dialog.error(str(error))
        dialog.body.addWidget(Button(tr("使用图片查看器打开"), open_original))
        self.window.open_dialog(dialog)
        job = ScreenshotJob(path, 0, 0, QSize(1400, 900))
        dialog.preview_job = job
        def received(generation, index, image):
            if isValid(picture):
                if image.isNull():
                    picture.setText(tr("无法读取截图"))
                else:
                    picture.setPixmap(QPixmap.fromImage(image).scaled(QSize(max(1,picture.width()), max(260,picture.height())), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        job.signals.ready.connect(received)
        self.window.images.pool.start(job)
