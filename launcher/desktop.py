"""Native white Qt desktop interface. All operations call Python services directly."""
import json
import os
import shutil
import sys
import time
import urllib.parse
import uuid
from pathlib import Path
from shiboken6 import isValid

from PySide6.QtCore import (QEasingCurve, QObject, QPoint, QRunnable, Qt, QThreadPool, QLocale,
                            QTimer, QUrl, Signal, QPropertyAnimation)
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QImage, QImageReader, QPainter, QPalette, QPixmap, QPolygonF
from PySide6.QtWidgets import (QApplication, QButtonGroup, QRadioButton, QCheckBox, QComboBox, QDialog, QFileDialog,
                               QFrame, QGridLayout, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QSystemTrayIcon,
                               QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from .i18n import LANGUAGES, language, set_language, tr
from .core import LauncherError, read_json, safe_path
from .images import ImageLoader, ProjectPreview
from .glass import (GlassSurface, MoonScene, TitleBar, clear_window, enable_backdrop,
                    moon_icon, resize_hit, update_window_mask)
from .motion import entrance
from .progress_ui import GlassProgress
from .slider_ui import GlassSlider
from .themes import SCHEMES, PalettePreview, apply as apply_theme, palette as theme_palette, color as theme_color
from .combobox import GlassComboBox
from .skin_ui import AccountAvatar, SkinPreview
from .memory import automatic_memory, effective_memory, physical_memory
from .service import Application
from .live_settings import LiveSettings
from .appearance import BACKGROUND_PRESETS, preset_background, wallpaper_mode, wallpaper_path, wallpaper_label
from .wallpaper_ui import WallpaperTile
from .folder_scan import MinecraftFolderScanner
from .local_list import VirtualCardList
from .providers import sources as content_sources, https_url
from . import __version__
from .widgets import Button, ElidedLabel, GlassFrame, NavigationSwitch, PageHost, SmoothScroll, STYLE, SummaryLabel, VersionRow, WheelRouter, card, glass_style, label, remove_focus_rings

PAGES = [("home", "", "启动"), ("library", "", "版本选择"),
         ("install", "", "自动安装"), ("discover", "", "模组"),
         ("downloads", "", "正在运行"),
         ("settings", "", "设置"), ("servers", "", "服务器管理"), ("about", "", "关于")]
LOADERS = {"vanilla": "原版", "fabric": "Fabric", "quilt": "Quilt", "forge": "Forge", "neoforge": "NeoForge", "optifine": "OptiFine", "cleanroom": "Cleanroom", "liteloader": "LiteLoader"}
KINDS = {"mod": "模组", "modpack": "整合包", "resourcepack": "资源包", "shader": "光影", "datapack": "数据包", "map": "地图"}
STATUS = {"queued": "等待中", "running": "进行中", "completed": "已完成", "failed": "失败", "cancelled": "已取消"}


def human_bytes(value):
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return "{:.1f} {}".format(value, unit)
        value /= 1024


def short_count(value):
    if language() == "en_US":
        return "{:.1f}M".format(value/1000000) if value >= 1000000 else "{:.1f}K".format(value/1000) if value >= 1000 else str(value)
    return tr("{:.1f} 万").format(value/10000) if value >= 10000 else str(value)


def row(*widgets):
    layout = QHBoxLayout()
    layout.setSpacing(9)
    flexible = any(isinstance(widget, ElidedLabel) for widget in widgets)
    for widget in widgets:
        if widget is None:
            if not flexible:
                layout.addStretch()
        elif isinstance(widget, QHBoxLayout):
            layout.addLayout(widget)
        else:
            layout.addWidget(widget, 1 if isinstance(widget, ElidedLabel) else 0)
    return layout


def field(title, widget):
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(7)
    layout.addWidget(label(tr(title), "fieldLabel"))
    layout.addWidget(widget)
    return container


def faithful_links(window, credit=False):
    from .faithful import AUTHOR, LICENSE, SITE
    website = Button(tr("Faithful 官网"), lambda: window.open_content_url(SITE))
    website.setObjectName("faithful-website")
    license_button = Button(tr("许可"), lambda: window.open_content_url(LICENSE))
    license_button.setObjectName("faithful-license")
    return row(ElidedLabel(AUTHOR,"muted") if credit else None, website, license_button)


def text_input(value="", placeholder="", name=""):
    widget = QLineEdit(str(value))
    widget.setPlaceholderText(placeholder)
    if name:
        widget.setObjectName(name)
    return widget


def combo(items, selected=None, name=""):
    widget = GlassComboBox()
    for value, text in items:
        widget.addItem(tr(text), value)
    if selected is not None:
        index = widget.findData(selected)
        if index >= 0:
            widget.setCurrentIndex(index)
    if name:
        widget.setObjectName(name)
    return widget


def notice(text):
    frame = QFrame()
    frame.setObjectName("notice")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(16, 12, 16, 12)
    layout.addWidget(label("ⓘ"))
    content = label(tr(text), "muted", True)
    layout.addWidget(content, 1)
    return frame


def empty(title, description, button=None):
    frame, layout = card()
    frame.setMinimumHeight(175)
    title_label = label(tr(title), "section")
    title_label.setAlignment(Qt.AlignCenter)
    detail = label(tr(description), "muted", True)
    detail.setAlignment(Qt.AlignCenter)
    layout.addStretch()
    layout.addWidget(title_label)
    layout.addWidget(detail)
    if button:
        layout.addWidget(button, 0, Qt.AlignHCenter)
    layout.addStretch()
    return frame


class Signals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = Signals()

    def run(self):
        try:
            self.signals.result.emit(self.operation())
        except Exception as exc:
            self.signals.error.emit(str(exc)[:700])
        finally:
            self.signals.finished.emit()


class NativeDialog(QDialog):
    def __init__(self, window, title, subtitle="", width=650):
        super().__init__(window)
        self.window = window
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(title)
        self.resize(width, 610)
        self.setMinimumWidth(460)
        self.setWindowModality(Qt.WindowModal)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        surface = GlassSurface()
        self.glass_surface = surface
        surface.set_glass_opacity(window.data["config"].get("glass_opacity", 72))
        root.addWidget(surface)
        outer = QVBoxLayout(surface)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)
        titlebar = TitleBar(self, title)
        self.titlebar = titlebar
        titlebar.layout.addStretch()
        titlebar.add_controls()
        outer.addWidget(titlebar)
        self.scroll = SmoothScroll()
        self.scroll.layout.setContentsMargins(26, 24, 26, 24)
        self.body = self.scroll.layout
        outer.addWidget(self.scroll, 1)
        self.footer = QHBoxLayout()
        self.footer.setContentsMargins(26, 12, 26, 18)
        self.footer.addStretch()
        self.footer.addWidget(Button(tr("关闭"), self.reject))
        outer.addLayout(self.footer)
        self.error_label = label("", "muted", True)
        self.error_label.setStyleSheet("color:" + theme_color("danger").name())
        self.error_label.hide()
        self.body.addWidget(self.error_label)
        self.animation = QPropertyAnimation(self, b"windowOpacity", self)

    def paintEvent(self, event):
        clear_window(self)
        super().paintEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        update_window_mask(self)

    def showEvent(self, event):
        super().showEvent(event)
        enable_backdrop(self)
        if self.window.animations:
            self.animation.stop()
            self.animation.setDuration(180)
            self.animation.setStartValue(0.0)
            self.animation.setEndValue(1.0)
            self.animation.setEasingCurve(QEasingCurve.OutCubic)
            self.animation.start()
            if hasattr(self, "content_animation"):
                self.content_animation.stop()
                self.content_animation.deleteLater()
            self.content_animation = entrance(self.scroll, offset=QPoint(0, 16), duration=320)
            self.content_animation.start()

    def nativeEvent(self, event_type, message):
        hit = resize_hit(self, message)
        return (True, hit) if hit is not None else super().nativeEvent(event_type, message)

    def error(self, text):
        self.error_label.setText(tr(text))
        self.error_label.setVisible(bool(text))
        self.scroll.ensureWidgetVisible(self.error_label)


class MinecraftScanDialog(NativeDialog):
    def __init__(self, window):
        super().__init__(window, tr("扫描 .minecraft 文件夹"), width=680)
        self.setObjectName("minecraft-folder-scan")
        self.resize(min(680, window.width()), min(540, window.height()))
        self.body.setContentsMargins(22, 12, 22, 10)
        self.body.setSpacing(10)
        self.scanner = None
        self.scan_result = None
        self.importing = False
        self.closed = False
        self.scope = ElidedLabel(tr("本机磁盘"), "muted")
        self.scope.setObjectName("scan-scope")
        self.body.addWidget(self.scope)
        self.all_disks = Button(tr("扫描本机"), lambda: self.start())
        self.choose_scope = Button(tr("指定目录"), self.choose_directory)
        self.stop = Button(tr("停止扫描"), self.stop_scan)
        self.stop.setObjectName("stop-folder-scan")
        self.body.addLayout(row(self.all_disks, self.choose_scope, None, self.stop))
        self.status = label(tr("准备扫描"), "muted", True)
        self.status.setObjectName("folder-scan-status")
        self.current = ElidedLabel("", "muted")
        self.progress = GlassProgress()
        self.body.addWidget(self.status)
        self.body.addWidget(self.current)
        self.body.addWidget(self.progress)
        self.results = QListWidget()
        self.results.setObjectName("folder-scan-results")
        self.results.setMinimumHeight(140)
        self.results.setTextElideMode(Qt.ElideMiddle)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.results.itemChanged.connect(self.update_selection)
        self.body.addWidget(self.results)
        self.select_all = QCheckBox(tr("全选"))
        self.select_all.setObjectName("select-all-folders")
        self.select_all.toggled.connect(self.toggle_all)
        self.footer.insertWidget(0, self.select_all)
        self.add = Button(tr("添加所选"), self.add_selected, True)
        self.add.setObjectName("add-scanned-folders")
        self.add.setEnabled(False)
        self.footer.addWidget(self.add)
        self.poll = QTimer(self)
        self.poll.setInterval(150)
        self.poll.timeout.connect(self.update_progress)
        self.finished.connect(self.shutdown)

    def start(self, roots=None):
        if self.closed or self.importing or (self.scanner and not self.scanner.snapshot()["complete"]):
            return
        self.scanner = MinecraftFolderScanner(roots)
        self.scan_result = None
        self.scope.setText(" · ".join(str(root) for root in self.scanner.roots) or tr("没有可扫描的本机磁盘"))
        self.results.clear()
        self.error("")
        self.select_all.setChecked(False)
        self.select_all.setEnabled(False)
        self.add.setEnabled(False)
        self.all_disks.setEnabled(False)
        self.choose_scope.setEnabled(False)
        self.stop.setEnabled(True)
        self.progress.setRange(0, 0)
        self.update_progress()
        self.poll.start()
        self.window.async_call(self.scanner.run, self.scan_finished, self.scan_failed)

    def choose_directory(self):
        path = QFileDialog.getExistingDirectory(self, tr("选择扫描目录"))
        if path:
            self.start([path])

    def stop_scan(self):
        if self.scanner:
            self.scanner.cancel()
            self.stop.setEnabled(False)
            self.status.setText(tr("正在停止扫描…"))

    def update_progress(self):
        if not self.scanner or self.closed:
            return
        state = self.scanner.snapshot()
        self.status.setText(tr("{} · 已检查 {} 个目录 · 找到 {} 个文件夹").format(
            tr("正在停止") if state["cancelled"] else tr("正在扫描"), state["visited"], state["found"]))
        self.current.setText(state["current"])
        self.current.show()

    def scan_failed(self, message):
        if self.closed:
            return
        self.poll.stop()
        self.scanner = None
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.stop.setEnabled(False)
        self.all_disks.setEnabled(True)
        self.choose_scope.setEnabled(True)
        self.status.setText(tr("扫描未完成，可重新扫描"))
        self.error(message)

    def scan_finished(self, result):
        if self.closed:
            return
        from .folders import folder_key
        self.scan_result = result
        self.poll.stop()
        self.stop.setEnabled(False)
        self.all_disks.setEnabled(True)
        self.choose_scope.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0 if result["cancelled"] else 100)
        self.results.clear()
        registered = {folder_key(item["path"]) for item in self.window.service.store.folders()}
        for folder in result["folders"]:
            added = folder_key(folder["path"]) in registered
            detail = tr("已添加") if added else tr("{} 个版本").format(folder["versions"]) if folder["available"] else tr(folder["issue"])
            item = QListWidgetItem(folder["path"] + "\n" + detail)
            item.setToolTip(folder["path"])
            item.setData(Qt.UserRole, folder)
            selectable = folder["available"] and not added
            item.setFlags((Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable) if selectable else Qt.NoItemFlags)
            if selectable:
                item.setCheckState(Qt.Checked)
            self.results.addItem(item)
        self.status.setText(tr("{} · 找到 {} 个文件夹 · 跳过 {} 个不可访问目录").format(
            tr("扫描已停止") if result["cancelled"] else tr("扫描完成"), len(result["folders"]), result["skipped"]))
        self.current.setText("" if result["folders"] else tr("未找到 .minecraft 文件夹，可以指定其他目录重新扫描"))
        self.current.setVisible(not result["folders"])
        self.scroll.verticalScrollBar().setValue(0)
        self.update_selection()

    def selected_paths(self):
        return [self.results.item(index).data(Qt.UserRole)["path"] for index in range(self.results.count())
                if self.results.item(index).checkState() == Qt.Checked]

    def update_selection(self, *_):
        eligible = [self.results.item(index) for index in range(self.results.count())
                    if self.results.item(index).flags() & Qt.ItemIsUserCheckable]
        selected = self.selected_paths()
        self.select_all.blockSignals(True)
        self.select_all.setChecked(bool(eligible) and len(selected) == len(eligible))
        self.select_all.blockSignals(False)
        self.select_all.setEnabled(bool(eligible) and not self.importing)
        self.add.setText(tr("添加所选 ({})").format(len(selected)))
        self.add.setEnabled(bool(selected) and not self.importing)

    def toggle_all(self, checked):
        self.results.blockSignals(True)
        for index in range(self.results.count()):
            item = self.results.item(index)
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.results.blockSignals(False)
        self.update_selection()

    def add_selected(self):
        paths = self.selected_paths()
        if not paths or self.importing:
            return
        self.importing = True
        self.update_selection()
        self.results.setEnabled(False)
        self.all_disks.setEnabled(False)
        self.choose_scope.setEnabled(False)
        self.status.setText(tr("正在添加 {} 个文件夹…").format(len(paths)))

        def operation():
            from .folders import add_folder
            added, errors = [], []
            for path in paths:
                try:
                    added.append(add_folder(self.window.service.game, path, select=False))
                except (LauncherError, OSError, ValueError) as exc:
                    errors.append(path + "：" + tr(str(exc)))
            if added:
                self.window.service.store.save({"library_root": added[0]["path"]})
            return {"added": added, "errors": errors}

        def complete(result):
            self.importing = False
            self.window.data = self.window.service.state()
            if not self.closed:
                self.window.navigate("library", False)
                if result["errors"]:
                    self.results.setEnabled(True)
                    self.scan_finished(self.scan_result)
                    self.error("\n".join(result["errors"]))
                else:
                    self.accept()
            message = tr("已添加 {} 个文件夹 · {} 个版本").format(len(result["added"]), sum(len(item["instances"]) for item in result["added"]))
            skipped = sum(len(item.get("skipped", [])) for item in result["added"])
            if skipped:
                message += tr(" · {} 个版本需修复").format(skipped)
            self.window.toast(message, bool(result["errors"]))

        def failed(message):
            self.importing = False
            if not self.closed:
                self.results.setEnabled(True)
                self.all_disks.setEnabled(True)
                self.choose_scope.setEnabled(True)
                self.update_selection()
                self.error(message)

        self.window.async_call(operation, complete, failed)

    def shutdown(self, *_):
        self.closed = True
        self.poll.stop()
        if self.scanner:
            self.scanner.cancel()


class SkinDialog(NativeDialog):
    def __init__(self,window,account_id):
        self.account_id = account_id
        account = window.service.skins.account(account_id)
        super().__init__(window,account["name"]+tr(" · 离线皮肤"),width=570)
        self.body.setSpacing(10)
        self.pending_path = str(window.service.skins.path(account) or "")
        self.preview = SkinPreview()
        self.preview.setObjectName("skin-preview")
        self.body.addWidget(self.preview)
        self.model = combo([("default",tr("宽臂 · Steve")),("slim",tr("细臂 · Alex"))],account.get("skin_model","default"),"skin-model")
        self.body.addWidget(field(tr("模型"),self.model))
        self.body.addWidget(label(tr("本地离线皮肤 · 重新启动游戏生效"),"muted"))
        self.body.addLayout(row(Button(tr("选择 PNG"),self.choose),Button(tr("恢复默认"),self.reset)))
        player_skin = Button(tr("下载玩家皮肤"),self.download_player)
        player_skin.setObjectName("offline-download-player-skin")
        self.body.addWidget(player_skin)
        self.submit = Button(tr("保存"),self.save,True)
        self.submit.setObjectName("skin-save")
        self.footer.addWidget(self.submit)
        self.model.currentIndexChanged.connect(self.update_preview)
        self.update_preview()

    def update_preview(self):
        self.preview.set_skin(self.pending_path,self.model.currentData())

    def choose(self):
        path,_ = QFileDialog.getOpenFileName(self,tr("选择皮肤"),"",tr("Minecraft 皮肤 (*.png)"))
        if not path: return
        reader = QImageReader(path)
        if bytes(reader.format()).lower()!=b"png" or (reader.size().width(),reader.size().height()) not in ((64,64),(64,32)):
            self.error(tr("请选择 64×64 或 64×32 PNG 皮肤"))
            return
        self.pending_path = path
        self.error("")
        self.update_preview()

    def reset(self):
        self.pending_path = ""
        self.update_preview()

    def download_player(self):
        self.accept()
        self.window.open_player_skin(self.account_id)

    def save(self):
        self.submit.setEnabled(False)
        path = "accounts/skin" if self.pending_path else "accounts/skin/reset"
        data = {"id":self.account_id,"path":self.pending_path,"model":self.model.currentData()}
        def failed(message):
            self.submit.setEnabled(True)
            self.error(message)
        def saved(_):
            self.accept()
            self.window.refresh()
            self.window.toast(tr("皮肤已保存"))
        self.window.request(path,data,saved,failed)


class PlayerSkinDialog(NativeDialog):
    def __init__(self,window,account_id=None,player=""):
        super().__init__(window,tr("下载玩家皮肤"),width=620)
        self.setObjectName("player-skin-dialog")
        self.resize(620,min(610,window.height()))
        self.account_id,self.skin_result = account_id,None
        self.player = text_input(player,tr("正版玩家名或 UUID"),"skin-player")
        self.fetch_button = Button(tr("获取皮肤"),self.fetch,True)
        self.fetch_button.setObjectName("fetch-player-skin")
        self.body.addLayout(row(self.player,self.fetch_button))
        self.preview = SkinPreview()
        self.preview.setObjectName("player-skin-preview")
        self.body.addWidget(self.preview)
        self.info = label("","muted",True)
        self.body.addWidget(self.info)
        self.export_button = Button(tr("保存 PNG"),self.export)
        self.export_button.setObjectName("export-player-skin")
        self.export_button.setEnabled(False)
        self.footer.addWidget(self.export_button)
        self.apply_button = None
        if account_id:
            self.apply_button = Button(tr("应用到离线账号"),self.apply_skin,True)
            self.apply_button.setObjectName("apply-player-skin")
            self.apply_button.setEnabled(False)
            self.footer.addWidget(self.apply_button)
        self.player.returnPressed.connect(self.fetch)

    def fetch(self):
        if not self.fetch_button.isEnabled(): return
        self.skin_result = None
        self.fetch_button.setEnabled(False)
        self.export_button.setEnabled(False)
        if self.apply_button: self.apply_button.setEnabled(False)
        self.error("")
        self.info.setText(tr("正在获取官方玩家档案和皮肤…"))
        def failed(message):
            if not self.isVisible(): return
            self.fetch_button.setEnabled(True)
            self.info.clear()
            self.error(message)
        def received(result):
            if not self.isVisible(): return
            self.skin_result = result
            self.preview.set_skin(result["path"],result["model"])
            self.info.setText(result["name"]+" · "+(tr("细臂") if result["model"]=="slim" else tr("宽臂")))
            self.fetch_button.setEnabled(True)
            self.export_button.setEnabled(True)
            if self.apply_button: self.apply_button.setEnabled(True)
        self.window.request("skins/player",{"player":self.player.text().strip()},received,failed)

    def export(self):
        if not self.skin_result: return
        destination,_ = QFileDialog.getSaveFileName(self,tr("保存玩家皮肤"),self.skin_result["name"]+".png",tr("PNG 皮肤 (*.png)"))
        if destination:
            self.window.request("skins/export",{"path":self.skin_result["path"],"destination":destination},
                                lambda result:self.window.toast(tr("皮肤已保存至 ")+result["path"]),self.error)

    def apply_skin(self):
        if not self.skin_result or not self.account_id: return
        self.apply_button.setEnabled(False)
        def failed(message):
            self.apply_button.setEnabled(True)
            self.error(message)
        def saved(_):
            self.accept()
            self.window.refresh()
            self.window.toast(tr("离线皮肤已应用，重新启动游戏生效"))
        self.window.request("accounts/skin",{"id":self.account_id,"path":self.skin_result["path"],"model":self.skin_result["model"]},saved,failed)


class LauncherWindow(LiveSettings, QMainWindow):
    def __init__(self, data_root, service=None, minecraft_root=None):
        super().__init__()
        self.service = service or Application(data_root, minecraft_root=minecraft_root)
        self.images = ImageLoader(self.service.store.root, self)
        self.service.store.save({"theme": "glass"})
        self.data = self.service.state()
        configured_language = self.data["config"].get("language", "zh_CN")
        set_language(configured_language if configured_language in dict(LANGUAGES) else "zh_CN")
        QLocale.setDefault(QLocale(language()))
        self.animations = self.data["config"].get("animations", True)
        QApplication.instance().setProperty("animations", self.animations)
        self.setWindowTitle(tr("星月启动器"))
        QApplication.instance().setApplicationDisplayName(self.windowTitle())
        self.setObjectName("StarMoonNativeWindow")
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(900, 600)
        self.apply_window_size()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(8)
        self.workers = {}
        self.dialogs = []
        self.recovery_dialog = None
        self.pack_failure_dialog = None
        self.pack_failure_queue = []
        self.notified_recovery = set()
        self.setAcceptDrops(True)
        from .pack_ui import PackDropFilter
        self.pack_drop_filter = PackDropFilter(self)
        QApplication.instance().installEventFilter(self.pack_drop_filter)
        self.notified = {t["id"] for t in self.data["tasks"]}
        self.notified_processes = set()
        self.notified_diagnostics = {t["id"] for t in self.data["tasks"]}
        self.page_id = "home"
        self.instance_signature = ""
        self.project_search_generation = 0
        self.search_state = {"kind": "mod", "provider": "modrinth", "query": "", "offset": 0, "instance": ""}
        self.toast_label = None
        self.background_mode = False
        self.navigation_key = ("home", "")
        self.sidebar_animation = None
        self.settings_draft = dict(self.data["config"])
        self.setting_changes = {}
        self.dragging_settings = set()
        self.setting_errors = {}
        self.settings_timer = QTimer(self)
        self.settings_timer.setSingleShot(True)
        self.settings_timer.timeout.connect(self.flush_settings)
        self.instance_settings_timer = QTimer(self)
        self.instance_settings_timer.setSingleShot(True)
        self.instance_settings_timer.timeout.connect(self.flush_instance_settings)
        self._update_checking = self._installing_update = False
        self.background_request = ""
        self.setWindowIcon(application_icon())
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip(tr("星月启动器 · 游戏运行中"))
        tray_menu = QMenu(self)
        show_action = QAction(tr("打开启动器"), self)
        self.show_launcher_action = show_action
        show_action.triggered.connect(self.restore_window)
        tray_menu.addAction(show_action)
        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(lambda reason: self.restore_window() if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.build_shell()
        self.apply_appearance()
        self.navigate("home")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(800)

    def apply_window_size(self):
        config = self.service.store.config()
        screen = self.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry()
        width = min(max(900, int(config.get("launcher_width", 900))), max(900, area.width()))
        height = min(max(600, int(config.get("launcher_height", 600))), max(600, area.height()))
        if self.isMaximized():
            self.showNormal()
        self.resize(width, height)
        if self.isVisible():
            self.move(max(area.left(), min(self.x(), area.right() - width + 1)), max(area.top(), min(self.y(), area.bottom() - height + 1)))

    def build_shell(self):
        central = GlassSurface()
        self.glass_surface = central
        self.setCentralWidget(central)
        self.central_layout = QVBoxLayout(central)
        self.central_layout.setContentsMargins(10, 8, 10, 6)
        self.central_layout.setSpacing(0)
        top = TitleBar(self, full=True)
        self.titlebar = top
        header = top.layout
        brand_icon = QLabel()
        brand_icon.setPixmap(self.windowIcon().pixmap(29, 29))
        header.addWidget(brand_icon)
        self.brand_label = label(tr("星月启动器"), "brand")
        header.addWidget(self.brand_label)
        header.addSpacing(15)
        self.navigation_switch = NavigationSwitch((("launch",tr("启动")),("download",tr("下载")),("settings",tr("设置")),("more",tr("更多"))))
        self.navigation_switch.selected.connect(lambda key:self.navigate({"launch":"home","download":"install","settings":"settings","more":"downloads"}[key]))
        self.top_buttons = self.navigation_switch.buttons
        header.addWidget(self.navigation_switch)
        header.addStretch()
        self.download_button = Button("◷", lambda: self.navigate("downloads"))
        self.download_button.setMinimumWidth(42)
        self.download_button.setFixedWidth(48)
        self.download_button.setObjectName("task-entry")
        self.download_button.setToolTip(tr("正在运行"))
        header.addWidget(self.download_button)
        top.add_controls()
        self.central_layout.addWidget(top)
        content = QHBoxLayout()
        content.setContentsMargins(0, 8, 0, 0)
        content.setSpacing(0)
        self.sidebar = GlassFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(285)
        self.side_layout = QVBoxLayout(self.sidebar)
        self.side_layout.setContentsMargins(20, 23, 20, 23)
        self.side_layout.setSpacing(12)
        content.addWidget(self.sidebar)
        self.host = PageHost()
        content.addWidget(self.host, 1)
        self.central_layout.addLayout(content, 1)
        self.task_bar = Button("", lambda: self.navigate("downloads"))
        self.task_bar.setMinimumHeight(32)
        self.task_bar.hide()
        self.central_layout.addWidget(self.task_bar)
        self.nav_buttons = {}
        self.login_mode = self.data["config"].get("login_mode") or (self.account() or {}).get("type", "offline")
        self.settings_category = "game"
        self.library_category = ""
        self.account_signature = None
        self.instance_id = ""
        self.instance_category = "overview"
        self.instance_drafts = {}

    def section_for(self, page):
        return "launch" if page in ("home", "library", "instance") else "download" if page in ("install", "discover") else "settings" if page in ("settings", "accounts") else "more"

    def build_sidebar(self):
        clear_layout(self.side_layout)
        self.side_layout.setSpacing(8 if self.page_id == "instance" else 12)
        self.nav_buttons = {}
        self.account_signature = None
        if self.page_id == "home":
            self.build_launch_panel()
            return
        if self.page_id == "instance":
            instance = self.service.store.instance(self.instance_id)
            self.side_layout.addWidget(Button(tr("← 返回启动"), lambda: self.navigate("home")))
            self.side_layout.addWidget(ElidedLabel(instance["name"], "section"))
            for key, text in (("overview", tr("概览")), ("settings", tr("设置")), ("mods", tr("模组管理")), ("resourcepacks", tr("资源包")), ("shaders", tr("光影")), ("saves", tr("存档管理")), ("screenshots", tr("截图")), ("logs", tr("日志"))):
                button = Button(text, lambda checked=False, value=key: self.set_instance_category(value), self.instance_category == key)
                button.setFixedHeight(34)
                button.setObjectName("instance-" + key)
                self.side_layout.addWidget(button)
            self.side_layout.addStretch()
            return
        if self.page_id == "library":
            self.side_layout.addWidget(Button(tr("← 返回启动"), lambda: self.navigate("home")))
            self.side_layout.addWidget(label(tr("游戏文件夹"), "fieldLabel"))
            self.library_folders = combo([(item["path"], item["name"] + (tr(" · 待创建") if item["default"] and not item["available"] else tr(" · 不可用") if not item["available"] else "")) for item in self.data["minecraft_folders"]], self.data["minecraft_path"])
            self.library_folders.setObjectName("minecraft-folders")
            self.library_folders.currentIndexChanged.connect(self.select_minecraft_folder)
            self.side_layout.addWidget(self.library_folders)
            self.side_layout.addWidget(ElidedLabel(self.data["minecraft_path"], "muted"))
            open_folder = Button(tr("打开文件夹"), lambda: self.mutate("folder", {"target": "minecraft"}))
            open_folder.setEnabled(self.service.store.selected_minecraft().is_dir())
            self.side_layout.addWidget(open_folder)
            if not self.service.store.minecraft_root.is_dir():
                create = Button(tr("创建 .minecraft 文件夹"), self.create_minecraft_folder, True)
                create.setObjectName("create-minecraft")
                self.side_layout.addWidget(create)
            self.side_layout.addWidget(Button(tr("添加 .minecraft 文件夹"), self.import_directory))
            scan = Button(tr("扫描 .minecraft 文件夹"), self.scan_minecraft_folders)
            scan.setObjectName("scan-minecraft-folders")
            self.side_layout.addWidget(scan)
            self.side_layout.addStretch()
            self.side_layout.addWidget(Button(tr("下载版本"), lambda: self.navigate("install"), True))
            return
        if self.section_for(self.page_id) == "download":
            self.side_layout.addWidget(Button(tr("自动安装"), lambda: self.navigate("install"), self.page_id == "install"))
            for key, text in (("modpack", tr("整合包")), ("mod", tr("模组")), ("resourcepack", tr("资源包")), ("shader", tr("光影")), ("datapack", tr("数据包")), ("map", tr("地图"))):
                self.side_layout.addWidget(Button(text, lambda checked=False, kind=key: self.explore(kind), self.page_id == "discover" and self.search_state["kind"] == key))
            self.side_layout.addStretch()
            self.side_layout.addWidget(Button(tr("导入整合包"), self.import_pack))
            return
        if self.page_id == "settings":
            for key, text in (("game", tr("游戏")), ("download", tr("下载")), ("account", tr("账号管理")), ("appearance", tr("外观")), ("ai", tr("智能排查")), ("advanced", tr("高级"))):
                button = Button(text, lambda checked=False, value=key: self.set_settings_category(value), self.settings_category == key)
                button.setObjectName("settings-" + key)
                self.side_layout.addWidget(button)
            self.side_layout.addStretch()
            return
        for key, text in (("servers", tr("服务器管理")), ("downloads", tr("正在运行")), ("about", tr("关于"))):
            button = Button(text, lambda checked=False, page=key: self.navigate(page), self.page_id == key)
            button.setObjectName("side-" + key)
            self.nav_buttons[key] = button
            self.side_layout.addWidget(button)
        self.side_layout.addStretch()

    def build_launch_panel(self):
        types = QHBoxLayout()
        self.microsoft_mode = Button(tr("正版"), lambda: self.set_login_mode("microsoft"), self.login_mode == "microsoft")
        self.microsoft_mode.setObjectName("mode-microsoft")
        self.offline_mode = Button(tr("离线"), lambda: self.set_login_mode("offline"), self.login_mode == "offline")
        self.offline_mode.setObjectName("mode-offline")
        types.addWidget(self.microsoft_mode)
        types.addWidget(self.offline_mode)
        self.side_layout.addLayout(types)
        self.side_layout.addStretch(1)
        avatar = AccountAvatar(self.windowIcon())
        self.avatar_label = avatar
        avatar.setPixmap(self.windowIcon().pixmap(56, 56))
        avatar.setAlignment(Qt.AlignCenter)
        self.side_layout.addWidget(avatar)
        self.login_accounts = combo([], name="launch-accounts")
        self.side_layout.addWidget(self.login_accounts)
        self.offline_name = text_input(placeholder=tr("玩家名"), name="launch-player-name")
        self.offline_name.setMaxLength(16)
        self.offline_name.editingFinished.connect(self.remember_offline_name)
        self.offline_name.returnPressed.connect(self.launch_current)
        self.side_layout.addWidget(self.offline_name)
        self.wam_login_button = Button(tr("登录"), self.microsoft_login, True)
        self.wam_login_button.setObjectName("wam-login")
        self.side_layout.addWidget(self.wam_login_button)
        self.skin_button = Button(tr("离线皮肤"),self.edit_current_skin)
        self.skin_button.setObjectName("offline-skin")
        self.side_layout.addLayout(row(Button(tr("账号管理"), lambda: self.set_settings_category("account")),self.skin_button))
        self.side_layout.addStretch(2)
        self.selected_label = label("", "muted", True)
        self.selected_label.setAlignment(Qt.AlignCenter)
        self.side_layout.addWidget(self.selected_label)
        self.launch_button = Button(tr("启动游戏"), self.launch_current, True)
        self.launch_button.setMinimumHeight(62)
        self.launch_button.setObjectName("launch-game")
        self.side_layout.addWidget(self.launch_button)
        self.side_layout.addLayout(row(Button(tr("版本选择"), lambda: self.navigate("library")), Button(tr("版本设置"), self.selected_settings)))
        self.login_accounts.currentIndexChanged.connect(self.launch_account_selected)
        self.update_launch_panel()

    def set_login_mode(self, mode):
        if self.page_id == "home" and hasattr(self, "offline_name") and isValid(self.offline_name):
            self.remember_offline_name()
        self.login_mode = mode
        self.service.config_update({"login_mode": mode})
        self.navigate("home")

    def remember_offline_name(self):
        if self.login_mode == "offline" and hasattr(self, "offline_name") and isValid(self.offline_name):
            value = self.offline_name.text().strip()
            self.service.config_update({"offline_name": value})
            self.data["config"]["offline_name"] = value

    def update_launch_panel(self):
        accounts = [a for a in self.data["config"]["accounts"] if a["type"] == self.login_mode]
        selected_id = self.data["config"]["selected_account"]
        signature = json.dumps([accounts, selected_id, self.login_mode])
        if signature != self.account_signature:
            self.login_accounts.blockSignals(True)
            self.login_accounts.clear()
            for account in accounts:
                self.login_accounts.addItem(account["name"], account["id"])
            if not accounts:
                self.login_accounts.addItem(tr("未登录") if self.login_mode == "microsoft" else tr("新玩家"), "")
            index = self.login_accounts.findData(selected_id)
            if index >= 0:
                self.login_accounts.setCurrentIndex(index)
            self.login_accounts.blockSignals(False)
            chosen = next((a for a in accounts if a["id"] == self.login_accounts.currentData()), None)
            self.bind_account_avatar(self.avatar_label, chosen)
            if not self.offline_name.hasFocus():
                self.offline_name.setText(self.data["config"].get("offline_name", "") or (chosen or {}).get("name", ""))
            self.account_signature = signature
        self.offline_name.setVisible(self.login_mode == "offline")
        self.skin_button.setVisible(self.login_mode == "offline")
        self.wam_login_button.setVisible(self.login_mode == "microsoft")
        self.wam_login_button.setText(tr("切换账户") if accounts else tr("登录"))
        selected = self.selected_instance()
        self.selected_label.setText(selected["name"] if selected else tr("未选择版本"))
        self.launch_button.setText(tr("再次启动") if selected and self.is_running(selected["id"]) else tr("启动游戏"))

    def bind_account_avatar(self, widget, account):
        widget.reset()
        if not account:
            return
        if account.get("type") == "microsoft":
            self.images.load_avatar(self.service.player_skins, account, widget)
        elif account.get("skin_path"):
            widget.set_image(QImage(str(self.service.skins.path(account))))

    def edit_current_skin(self):
        name = self.offline_name.text().strip()
        account_id = self.login_accounts.currentData()
        chosen = next((a for a in self.data["config"]["accounts"] if a["id"]==account_id),None)
        if name and (not chosen or name!=chosen["name"]):
            def created(account):
                self.refresh()
                self.edit_skin(account["id"])
            self.request("accounts/offline",{"name":name},created)
        elif chosen:
            self.edit_skin(chosen["id"])
        else:
            self.toast(tr("请先填写玩家名"))
            self.offline_name.setFocus()

    def edit_skin(self,account_id):
        self.open_dialog(SkinDialog(self,account_id))

    def open_player_skin(self,account_id=None,player=""):
        self.open_dialog(PlayerSkinDialog(self,account_id,player))

    def launch_account_selected(self):
        account_id = self.login_accounts.currentData()
        if account_id:
            self.service.config_update({"selected_account": account_id})
            self.data = self.service.state()
            if self.login_mode == "offline":
                account = next(a for a in self.data["config"]["accounts"] if a["id"] == account_id)
                self.offline_name.setText(account["name"])
                self.remember_offline_name()

    def launch_current(self):
        instance = self.selected_instance()
        if not instance:
            self.navigate("library")
            return
        if self.login_mode == "offline":
            name = self.offline_name.text().strip()
            if not name:
                self.offline_name.setFocus()
                self.toast(tr("请输入玩家名"))
                return
            def created(_):
                self.service.config_update({"offline_name": name})
                self.refresh()
                self.launch(instance["id"])
            self.request("accounts/offline", {"name": name}, created)
        else:
            account_id = self.login_accounts.currentData()
            if not account_id:
                self.microsoft_login()
                return
            self.service.config_update({"selected_account": account_id})
            self.data = self.service.state()
            self.launch(instance["id"])

    def selected_settings(self):
        selected = self.selected_instance()
        if selected:
            self.open_instance(selected["id"])
        else:
            self.navigate("library")

    def set_library_category(self, category):
        self.library_category = category
        self.navigate("library")

    def set_settings_category(self, category):
        self.settings_category = category
        self.navigate("settings")

    def apply_ui_language(self):
        set_language(self.service.store.config().get("language", "zh_CN"))
        QLocale.setDefault(QLocale(language()))
        self.setWindowTitle(tr("星月启动器"))
        QApplication.instance().setApplicationDisplayName(self.windowTitle())
        self.brand_label.setText(tr("星月启动器"))
        self.tray.setToolTip(tr("星月启动器 · 游戏运行中"))
        self.show_launcher_action.setText(tr("打开启动器"))
        self.download_button.setToolTip(tr("正在运行"))
        for key, title in (("launch", "启动"), ("download", "下载"), ("settings", "设置"), ("more", "更多")):
            self.top_buttons[key].setText(tr(title))
        for control, title in zip(self.titlebar.controls, ("最小化", "最大化 / 还原", "关闭")):
            control.setToolTip(tr(title))
            control.setAccessibleName(tr(title))
        self.data = self.service.state()
        self.navigate(self.page_id, False)

    def select_version(self, instance_id):
        self.service.config_update({"selected_instance": instance_id})
        self.data = self.service.state()
        self.navigate("home")

    def account(self):
        config = self.data["config"]
        return next((a for a in config["accounts"] if a["id"] == config["selected_account"]), next(iter(config["accounts"]), None))

    def selected_instance(self):
        return next((i for i in self.data["instances"] if i["id"] == self.data["config"]["selected_instance"]), next(iter(self.data["instances"]), None))

    def is_running(self, instance_id):
        return any(p["running"] and p["instance_id"] == instance_id for p in self.data["processes"])

    def async_call(self, operation, success=None, error=None):
        worker = Worker(operation)
        key = uuid.uuid4().hex
        self.workers[key] = worker
        if success:
            worker.signals.result.connect(success)
        worker.signals.error.connect(error or (lambda text: self.toast(text, True)))
        worker.signals.finished.connect(lambda: self.workers.pop(key, None))
        self.pool.start(worker)

    def request(self, path, data=None, success=None, error=None):
        parsed = urllib.parse.urlparse(path)
        return self.async_call(lambda: self.service.route("GET" if data is None else "POST", "/api/" + parsed.path,
                                urllib.parse.parse_qs(parsed.query), data or {}), success, error)

    def mutate(self, path, data, success=None, show_tasks=False):
        def done(result):
            self.refresh()
            if result and isinstance(result, dict) and result.get("task_id"):
                self.toast(tr("已加入任务列表"))
                if show_tasks:
                    self.navigate("downloads")
            if success:
                success(result)
        self.request(path, data, done)

    def toast(self, text, error=False):
        if self.toast_label:
            self.toast_label.hide()
            self.toast_label.deleteLater()
        self.toast_label = label(tr(text), wrap=True)
        self.toast_label.setParent(self)
        tones = theme_palette()
        self.toast_label.setStyleSheet("QLabel {background:%s;color:%s;border:1px solid %s;border-radius:10px;padding:15px;}" %
                                      (tones["surface"].name(),tones["danger" if error else "text"].name(),tones["bg2"].name()))
        self.toast_label.setFixedWidth(min(460, self.width()-50))
        self.toast_label.adjustSize()
        self.toast_label.move(self.width()-self.toast_label.width()-27, self.height()-self.toast_label.height()-25)
        self.toast_label.show()
        self.toast_label.raise_()
        if self.animations:
            effect = QGraphicsOpacityEffect(self.toast_label)
            self.toast_label.setGraphicsEffect(effect)
            self.toast_animation = QPropertyAnimation(effect, b"opacity", self)
            self.toast_animation.setDuration(190)
            self.toast_animation.setStartValue(0.0)
            self.toast_animation.setEndValue(1.0)
            self.toast_animation.start()
        current = self.toast_label
        QTimer.singleShot(8000 if error else 4000, lambda: current.hide() if isValid(current) and current.isVisible() else None)

    def navigate(self, page_id, animated=True):
        if page_id == "accounts":
            page_id = "settings"
            self.settings_category = "account"
        for dropdown in self.findChildren(GlassComboBox):
            if dropdown.popup.isVisible():
                dropdown.hidePopup()
        self.flush_settings()
        self.flush_instance_settings()
        if self.page_id == "home" and hasattr(self, "offline_name") and isValid(self.offline_name):
            self.remember_offline_name()
        if page_id == "settings":
            self.settings_draft = dict(self.data["config"],**self.setting_changes)
        previous, previous_category = self.navigation_key
        category = self.settings_category if page_id == "settings" else self.instance_category if page_id == "instance" else self.search_state["kind"] if page_id == "discover" else ""
        vertical = previous == page_id and previous_category != category
        order = [key for key, _, _ in PAGES] + ["instance"]
        direction = -1 if order.index(page_id) < order.index(previous) else 1
        if vertical:
            categories = ["game", "download", "account", "appearance", "ai", "advanced"] if page_id == "settings" else ["overview", "settings", "mods", "resourcepacks", "shaders", "saves", "screenshots", "logs"] if page_id == "instance" else list(KINDS)
            direction = -1 if previous_category in categories and categories.index(category) < categories.index(previous_category) else 1
        self.navigation_key = (page_id, category)
        self.page_id = page_id
        self.update_page_background()
        self.navigation_switch.select(self.section_for(page_id),animated)
        self.build_sidebar()
        if self.sidebar_animation:
            self.sidebar_animation.stop()
            self.sidebar_animation.deleteLater()
            self.sidebar_animation = None
        if self.animations and animated:
            self.sidebar_animation = entrance(self.sidebar, duration=290, delay=35)
            self.sidebar_animation.start()
        else:
            self.sidebar.setGraphicsEffect(None)
        # Keep page construction inside the main window and reveal it atomically.
        page = SmoothScroll(self.host)
        page.hide()
        page.setObjectName("page-" + page_id)
        self.page = page
        getattr(self, "page_" + page_id)(page.layout)
        self.host.switch(page, self.animations and animated, direction=direction, vertical=vertical)
        self.update_shell()
        return True

    def intro(self, layout, title, subtitle="", actions=()):
        header = QHBoxLayout()
        header.addWidget(label(tr(title), "section"))
        header.addStretch()
        for button in actions:
            header.addWidget(button)
        layout.addLayout(header)

    def update_shell(self):
        if self.page_id == "home":
            self.update_launch_panel()
        active = [t for t in self.data["tasks"] if t["status"] in ("running", "queued")]
        count = len(active) + sum(1 for p in self.data["processes"] if p["running"]) + len(self.data.get('server_processes', []))
        self.download_button.setText("◷ " + str(count) if count else "◷")
        if self.page_id == "settings" and self.settings_category == "game":
            self.update_memory_preview()
        if active:
            task = next((t for t in active if t["status"] == "running"), active[0])
            speed = sum(t.get("speed", 0) for t in active)
            download = task.get("download")
            percent = tr("下载 {:.1f}%").format(download["percent"]) if download and download.get("active") and download.get("percent") is not None else tr("下载中") if download and download.get("active") else tr("处理中")
            self.task_bar.setText("{}   {}   ↓ {}/s".format(tr(task["message"])[:55], percent, human_bytes(speed)))
            self.task_bar.show()
        else:
            self.task_bar.hide()

    def refresh(self):
        try:
            self.data = self.service.state()
            self.sync_recovery_dialog()
            if self.background_mode and any(t["status"] == "failed" and t.get("pack_install") and t["id"] not in self.notified for t in self.data["tasks"]):
                self.restore_window()
            for task in self.data["tasks"]:
                result = task.get("result")
                if task["status"] == "completed" and isinstance(result,dict) and result.get("diagnostic_report") and task["id"] not in self.notified_diagnostics:
                    self.notified_diagnostics.add(task["id"])
                    if self.background_mode:
                        self.restore_window()
                    self.show_diagnostic(result["diagnostic_report"],result.get("resume_account") if result.get("launch_blocked") else None,result.get('resume_server'))
            if self.background_mode and not self.data.get('server_processes') and not any(t["status"] in ("running", "queued") for t in self.data["tasks"]) and not any(p["running"] or not p.get("finalized",True) for p in self.data["processes"]):
                self.tray.hide()
                self.timer.stop()
                QApplication.instance().quit()
                return
            self.update_shell()
            signature = json.dumps([self.data["instances"], self.data["processes"], self.data["config"]["accounts"], self.data["config"]["selected_account"]], sort_keys=True)
            changed = signature != self.instance_signature
            self.instance_signature = signature
            if self.page_id == "downloads" and hasattr(self, "task_rows"):
                self.update_tasks()
            elif changed and (self.page_id == "library" or (self.page_id == "settings" and self.settings_category == "account")):
                self.navigate(self.page_id, False)
            for task in self.data["tasks"]:
                for result in task.get("recovery_results", []):
                    key = (task["id"], result["id"])
                    if key not in self.notified_recovery:
                        self.notified_recovery.add(key)
                        message = tr("AI 补下载成功，已通过校验：{}") if result["success"] else tr("AI 补下载失败：{}")
                        self.toast(message.format(result["filename"]), not result["success"])
                if task["status"] in ("failed", "completed", "cancelled") and task["id"] not in self.notified:
                    self.notified.add(task["id"])
                    report = task.get("pack_install")
                    if report and (task["status"] == "failed" or report.get("cleanup_errors")):
                        self.pack_failure_queue.append(task)
                    elif not (task["status"] == "completed" and any(item["success"] for item in task.get("recovery_results", []))) and (not isinstance(task.get("result"),dict) or not task["result"].get("diagnostic_report")):
                        self.toast(task["title"] + ("：" + task["error"] if task["status"] == "failed" else " · " + tr(STATUS[task["status"]])), task["status"] == "failed")
                    if self.page_id == "instance" and hasattr(self, "instance_panel"):
                        self.instance_panel.refresh_content()
            self.sync_pack_failure_dialog()
            for process in self.data["processes"]:
                key = (process.get("id",process["instance_id"]), process.get("started"))
                if not process["running"] and process.get("exit_code") not in (None, 0) and not process.get("stop_requested") and key not in self.notified_processes:
                    self.notified_processes.add(key)
                    self.toast(tr("游戏异常退出（代码 {}），可在“正在运行”中查看日志。").format(process["exit_code"]), True)
        except Exception as exc:
            self.timer.stop()
            self.toast(tr("读取本地数据失败：") + str(exc), True)

    def page_home(self, layout):
        check = Button(tr("一键排查"),self.check_current)
        check.setObjectName("preflight-check")
        layout.addLayout(row(None,check))
        layout.addStretch(1)
        illustration = MoonScene(self.page.body)
        illustration.setMinimumHeight(270)
        illustration.setMaximumHeight(390)
        if not self.animations:
            illustration.float_animation.stop()
        layout.addWidget(illustration)
        illustration.setVisible(self.glass_surface.image.isNull())
        layout.addStretch(1)

    def instance_grid(self, layout, instances):
        for instance in instances:
            instance_id = instance["id"]
            chosen = instance_id == self.data["config"]["selected_instance"]
            frame = VersionRow(chosen)
            frame.setObjectName("version-row")
            frame.setProperty("instanceId", instance_id)
            frame.selected.connect(lambda value=instance_id: self.select_version(value))
            frame.setAccessibleName(instance["name"])
            box = QVBoxLayout(frame)
            box.setContentsMargins(17, 13, 17, 13)
            name = ElidedLabel(instance["name"], "section")
            detail = label(instance["game_version"] + " · " + tr(LOADERS[instance["loader"]]), "muted")
            text = QVBoxLayout()
            text.addWidget(name)
            text.addWidget(detail)
            line = QHBoxLayout()
            icon = QLabel()
            icon.setPixmap(self.windowIcon().pixmap(42, 42))
            line.addWidget(icon)
            line.addSpacing(8)
            line.addLayout(text, 1)
            if chosen:
                line.addWidget(label(tr("当前"), "badge"))
            favorite = Button("★" if instance.get("favorite") else "☆", lambda checked=False, value=instance_id: self.favorite_version(value))
            favorite.setMinimumWidth(36)
            favorite.setFixedWidth(38)
            favorite.setToolTip(tr("收藏 / 取消收藏"))
            line.addWidget(favorite)
            manage = Button("⚙\ufe0e", lambda checked=False, value=instance_id: self.open_instance(value))
            manage.setMinimumWidth(36)
            manage.setFixedWidth(38)
            manage.setToolTip(tr("版本设置"))
            line.addWidget(manage)
            box.addLayout(line)
            layout.addWidget(frame)

    def favorite_version(self, instance_id):
        instance = self.service.store.instance(instance_id)
        self.service.store.update_instance(instance_id, {"favorite": not instance.get("favorite", False)})
        self.data = self.service.state()
        self.filter_library()

    def page_library(self, layout):
        self.intro(layout, tr("版本选择"), actions=[Button(tr("刷新"), lambda: self.refresh_versions())])
        self.library_query = text_input(placeholder=tr("搜索版本"), name="library-search")
        self.library_loader = combo([("", tr("所有加载器"))] + list(LOADERS.items()), self.library_category)
        layout.addLayout(row(self.library_query, self.library_loader))
        self.library_container = QWidget()
        self.library_layout = QVBoxLayout(self.library_container)
        self.library_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.library_container)
        self.library_query.textChanged.connect(self.filter_library)
        self.library_loader.currentIndexChanged.connect(self.filter_library)
        self.filter_library()

    def page_install(self, layout):
        # The list owns scrolling; keep the page actions within the viewport.
        layout.setAlignment(Qt.AlignmentFlag(0))
        self.page.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        if getattr(self, "install_version", None):
            version = self.install_version
            self.install_form = InstallForm(self, version, parent=self.page.body)
            self.install_reselect = Button(tr("← 重新选择原版"), self.reset_install_version)
            self.install_reselect.setObjectName("install-reselect")
            layout.addLayout(row(ElidedLabel(tr("安装 ") + version, "section"),
                                 self.install_reselect, self.install_form.submit))
            layout.addWidget(self.install_form, 1)
            return
        self.intro(layout, tr("自动安装"))
        self.install_query = text_input(placeholder=tr("搜索版本"), name="download-version-search")
        self.install_type = combo([("release", tr("正式版")), ("snapshot", tr("快照版")), ("all", tr("全部"))])
        layout.addLayout(row(self.install_query, self.install_type))
        self.install_list = QListWidget()
        self.install_list.setObjectName("download-versions")
        self.install_list.setMinimumHeight(160)
        self.install_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.install_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        layout.addWidget(self.install_list, 1)
        self.install_next = Button(tr("继续"), self.install_selected, True)
        self.install_next.setEnabled(False)
        layout.addLayout(row(None, self.install_next))
        self.install_list.itemActivated.connect(lambda _: self.install_selected())
        self.install_list.currentItemChanged.connect(lambda current, previous: self.install_next.setEnabled(current is not None))
        self.install_query.textChanged.connect(self.filter_install_versions)
        self.install_type.currentIndexChanged.connect(self.filter_install_versions)
        self.install_status = label(tr("加载中…"), "muted", True)
        self.install_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.install_status.setMaximumHeight(48)
        layout.addWidget(self.install_status)
        page, status = self.page, self.install_status
        def received(data):
            self.manifest_cache = data
            if self.page is page and isValid(status):
                status.hide()
                self.filter_install_versions()
        self.request("manifest", success=received, error=lambda message: status.setText(tr(message)) if self.page is page and isValid(status) else None)

    def filter_install_versions(self):
        if self.page_id != "install" or getattr(self, "install_version", None) or not getattr(self, "manifest_cache", None):
            return
        self.install_list.clear()
        for version in self.manifest_cache["versions"]:
            if self.install_query.text().lower() not in version["id"].lower() or (self.install_type.currentData() != "all" and version["type"] != self.install_type.currentData()):
                continue
            item = QListWidgetItem(version["id"] + "    " + version.get("releaseTime", "")[:10])
            item.setData(Qt.UserRole, version["id"])
            self.install_list.addItem(item)
        if self.install_list.count():
            self.install_list.setCurrentRow(0)

    def reset_install_version(self):
        self.install_version = None
        self.navigate("install", False)

    def install_selected(self):
        item = self.install_list.currentItem()
        if item:
            self.install_version = item.data(Qt.UserRole)
            self.navigate("install", False)

    def page_servers(self, layout):
        from .server_ui import ServerPanel
        self.intro(layout, tr('服务器管理'))
        self.server_panel = ServerPanel(self)
        layout.addWidget(self.server_panel, 1)

    def page_about(self, layout):
        self.intro(layout, tr("星月启动器 {}").format(__version__))
        frame, box = card()
        box.addWidget(label(tr("作者：星月晓梦"), "fieldLabel"))
        check = Button(tr("检查启动器更新"), self.open_launcher_updates, True)
        check.setObjectName("check-launcher-updates")
        box.addWidget(check)
        box.addLayout(row(Button(tr("许可证"), self.show_licenses), Button(tr("数据目录"), lambda: self.mutate("folder", {"target": "data"})), None))
        layout.addWidget(frame)

    def filter_library(self):
        clear_layout(self.library_layout)
        query, loader = self.library_query.text().lower(), self.library_loader.currentData()
        items = [i for i in self.data["instances"] if os.path.normcase(i.get("minecraft_path", str(self.service.store.minecraft_root))) == os.path.normcase(self.data["minecraft_path"]) and query in (i["name"]+" "+i["game_version"]).lower() and (not loader or i["loader"] == loader)]
        if items:
            groups = [(tr("收藏的版本"), [i for i in items if i.get("favorite")]),
                      (tr("常规版本"), [i for i in items if not i.get("favorite")])]
            for title, members in groups:
                if members:
                    self.library_layout.addWidget(label(title + "  ·  " + str(len(members)), "fieldLabel"))
                    self.instance_grid(self.library_layout, members)
        else:
            missing = not self.service.store.selected_minecraft().is_dir()
            self.library_layout.addWidget(empty(tr("尚未创建游戏文件夹") if missing else tr("没有匹配的版本"), tr("点击左侧创建，或添加已有 .minecraft 文件夹") if missing else "", None if missing else Button(tr("下载版本"), self.new_instance, True)))

    def create_minecraft_folder(self):
        def created(_):
            self.data = self.service.state()
            self.navigate("library", False)
            self.toast(tr("已创建 .minecraft 文件夹"))
        self.request("folders/create", {}, created)

    def refresh_versions(self):
        self.select_minecraft_folder(path=self.data["minecraft_path"])

    def select_minecraft_folder(self, *_args, path=None):
        selected = path or self.library_folders.currentData()
        def selected_folder(_):
            self.data = self.service.state()
            if self.page_id == "library":
                self.navigate("library", False)
        self.request("folders/select", {"path": selected}, selected_folder)

    def account_management(self, layout):
        download = Button(tr("下载玩家皮肤"),lambda:self.open_player_skin())
        download.setObjectName("download-player-skin")
        self.intro(layout, tr("账号管理"), actions=[Button(tr("添加离线账号"), self.add_offline), Button(tr("正版登录"), self.microsoft_login, True)])
        layout.addLayout(row(None,download))
        accounts = self.data["config"]["accounts"]
        if not accounts:
            layout.addWidget(empty(tr("暂无账号"), "", Button(tr("添加离线账号"), self.add_offline, True)))
        for account in accounts:
            frame, box = card()
            avatar = AccountAvatar(self.windowIcon(), 44)
            avatar.setFixedSize(44, 44)
            self.bind_account_avatar(avatar, account)
            box.addLayout(row(avatar, label(account["name"], "section"), None, label(tr("离线账号") if account["type"] == "offline" else "Microsoft", "badge")))
            selected = self.account() and account["id"] == self.account()["id"]
            account_id = account["id"]
            controls = [label(tr("✓ 当前使用") if selected else "", "muted"), None]
            if account["type"] == "offline":
                controls.append(Button(tr("离线皮肤"),lambda checked=False,value=account_id:self.edit_skin(value)))
            else:
                controls.append(Button(tr("下载皮肤"),lambda checked=False,name=account["name"]:self.open_player_skin(player=name)))
            box.addLayout(row(*controls,
                              Button(tr("切换账号"), lambda checked=False, value=account_id: self.select_account(value)),
                              Button(tr("移除"), lambda checked=False, value=account_id: self.delete_account(value), danger=True)))
            layout.addWidget(frame)

    def select_account(self, account_id):
        account = next(a for a in self.data["config"]["accounts"] if a["id"] == account_id)
        self.login_mode = account["type"]
        changes = {"selected_account": account_id, "login_mode": self.login_mode}
        if self.login_mode == "offline":
            changes["offline_name"] = account["name"]
        self.service.config_update(changes)
        self.data = self.service.state()
        self.navigate("home")

    def page_downloads(self, layout):
        self.clear_finished_button = Button(tr("全部关闭"), lambda: self.mutate("tasks/dismiss", {}))
        self.clear_finished_button.setObjectName("clear-finished")
        self.clear_finished_button.setToolTip(tr("关闭所有已结束的项目"))
        self.intro(layout, tr("正在运行"), actions=[self.clear_finished_button])
        self.task_rows = {}
        self.process_rows = {}
        self.server_task_signature = None
        self.tasks_layout = QVBoxLayout()
        self.tasks_layout.setSpacing(14)
        layout.addLayout(self.tasks_layout)
        self.update_tasks()

    def sync_recovery_dialog(self):
        if self.pack_failure_dialog:
            return
        pending = next((task for task in self.data["tasks"] if task["status"] == "running" and task.get("interaction")), None)
        current = self.recovery_dialog
        if current and (not pending or current.task_id != pending["id"] or current.request_id != pending["interaction"]["id"]):
            current.expire()
            self.recovery_dialog = None
        if pending and self.recovery_dialog is None:
            from .recovery_ui import RecoveryDialog
            from .management_ui import PackReviewDialog
            if self.background_mode:
                self.restore_window()
            dialog = PackReviewDialog(self, pending) if pending['interaction']['kind'] == 'pack_upgrade' else RecoveryDialog(self, pending)
            self.recovery_dialog = dialog
            dialog.finished.connect(lambda _: setattr(self, "recovery_dialog", None) if self.recovery_dialog is dialog else None)
            self.open_dialog(dialog)

    def sync_pack_failure_dialog(self):
        if self.pack_failure_dialog or self.recovery_dialog or not self.pack_failure_queue:
            return
        from .pack_ui import PackFailureDialog
        dialog = PackFailureDialog(self, self.pack_failure_queue.pop(0))
        self.pack_failure_dialog = dialog
        dialog.finished.connect(lambda _: setattr(self, "pack_failure_dialog", None) if self.pack_failure_dialog is dialog else None)
        self.open_dialog(dialog)

    def update_tasks(self):
        tasks = self.data["tasks"]
        processes = self.data["processes"]
        servers = self.data.get('server_processes', [])
        server_signature = [(item['id'], item['state']) for item in servers]
        self.clear_finished_button.setEnabled(any(t["status"] not in ("running", "queued") for t in tasks) or any(not p["running"] for p in processes))
        if self.server_task_signature != server_signature or set(self.task_rows) != {t["id"] for t in tasks} or set(self.process_rows) != {p.get("id",p["instance_id"]) for p in processes}:
            self.server_task_signature = server_signature
            clear_layout(self.tasks_layout)
            self.task_rows = {}
            self.process_rows = {}
            if not tasks and not processes and not servers:
                self.tasks_layout.addWidget(empty(tr("暂无任务"), ""))
            for server in servers:
                from .server_ui import LocalServerDialog, STATES
                frame, box = card()
                box.addLayout(row(ElidedLabel(server['name'], 'section'), label(tr(STATES[server['state']]), 'badge')))
                box.addWidget(Button(tr('控制台与配置'), lambda checked=False, identifier=server['id']: self.open_dialog(LocalServerDialog(self, identifier))))
                self.tasks_layout.addWidget(frame)
            for process in processes:
                frame, box = card()
                instance_id = process["instance_id"]
                process_id = process.get("id")
                key = process_id or instance_id
                instance = next((i for i in self.data["instances"] if i["id"] == instance_id), {})
                state = label("", "badge")
                box.addLayout(row(ElidedLabel(instance.get("name", "Minecraft"), "section"), None, state))
                action = Button(tr("结束游戏"), lambda checked=False, value=instance_id,run=process_id: self.stop_game(value,run))
                close = Button(tr("关闭"), lambda checked=False, value=key: self.mutate("processes/dismiss", {"id": value}))
                logs = Button(tr("查看日志"), lambda checked=False, value=instance_id,run=process_id: self.show_game_log(value,run))
                action.setObjectName("stop-process-"+key)
                logs.setObjectName("log-process-"+key)
                box.addLayout(row(label("PID  " + str(process["pid"]), "muted"), None, logs, action, close))
                self.tasks_layout.addWidget(frame)
                self.process_rows[key] = (state, action, close)
            for task in tasks:
                frame, box = card()
                title, status = ElidedLabel(tr(task["title"]), "section"), label("", "badge")
                cancel = Button(tr("取消"), lambda checked=False, task_id=task["id"]: self.mutate("tasks/cancel", {"id": task_id}))
                close = Button(tr("关闭"), lambda checked=False, task_id=task["id"]: self.mutate("tasks/dismiss", {"id": task_id}))
                close.setObjectName("close-task-" + task["id"])
                box.addLayout(row(title, None, status, cancel, close))
                message = label("", "muted", True)
                box.addWidget(message)
                progress = GlassProgress()
                progress.setRange(0, 1000)
                box.addWidget(progress)
                info = label("", "muted", True)
                box.addWidget(info)
                self.tasks_layout.addWidget(frame)
                self.task_rows[task["id"]] = (message, status, progress, info, cancel, close)
        for process in processes:
            state, action, close = self.process_rows[process.get("id",process["instance_id"])]
            abnormal = process.get("exit_code") not in (None, 0) and not process.get("stop_requested")
            state.setText(tr("游戏运行中") if process["running"] else tr("异常退出 · {}").format(process["exit_code"]) if abnormal else tr("游戏已结束"))
            action.setVisible(process["running"])
            close.setVisible(not process["running"])
            close.setEnabled(process.get("finalized", True))
        for task in tasks:
            message, status, progress, info, cancel, close = self.task_rows[task["id"]]
            progress.setActivity(task["status"] == "running")
            message.setText(tr(task["message"]))
            status.setText(tr(STATUS[task["status"]]))
            download = task.get("download")
            active_download = download and download.get("active") and task["status"] == "running"
            if active_download and download.get("percent") is not None:
                status.setText(tr("下载 · {:.1f}%").format(download["percent"]))
            if active_download and download.get("percent") is None:
                progress.setRange(0, 0)
            elif active_download:
                progress.setRange(0, 1000)
                progress.setValue(round(download["percent"]*10))
                progress.setFormat(tr("当前下载 %p%"))
            elif task["status"] == "running":
                progress.setRange(0, 0)
            else:
                progress.setRange(0, 1000)
                progress.setValue(1000 if task["status"] == "completed" else round(task["progress"]*10))
                progress.setFormat("%p%")
            path = task.get("result") or {}
            speed = "  ·  ↓ {}/s".format(human_bytes(task.get("speed", 0))) if task["status"] == "running" else ""
            if download:
                amount = human_bytes(download["bytes"]) + (" / " + human_bytes(download["total"]) if download["total"] is not None else tr(" / 大小未知"))
                detail = tr("{}  ·  文件 {} / {}").format(amount, download["files"], download["files_total"])
            else:
                detail = human_bytes(task["bytes"])
            info.setText(detail + speed + (tr("\n保存至 ") + path["path"] if isinstance(path, dict) and path.get("path") else ""))
            cancel.setVisible(task["status"] in ("running", "queued"))
            close.setVisible(task["status"] not in ("running", "queued"))

    def page_discover(self, layout):
        # Keep the viewport width stable when replacing a long list with the
        # loading indicator, a short list or an empty result.
        self.page.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        bar = self.page.verticalScrollBar()
        bar.setStyleSheet('QScrollBar::handle:disabled { background: transparent; }')
        bar.rangeChanged.connect(lambda minimum, maximum: bar.setEnabled(maximum > minimum))
        bar.setEnabled(bar.maximum() > bar.minimum())
        state = self.search_state
        choices = content_sources(state["kind"])
        if state["provider"] not in dict(choices):
            state["provider"] = "modrinth"
        self.intro(layout, tr(KINDS[state["kind"]]))
        self.content_kind = combo(list(KINDS.items()), state["kind"], "content-kind")
        self.content_kind.setParent(self.page)
        self.content_kind.hide()
        self.content_provider = combo([(key, tr(name)) for key, name in choices], state["provider"])
        self.content_provider.setFixedWidth(145)
        self.content_instance = combo([("", tr("不限版本 / 加载器"))] + [(i["id"], i["name"] + " · " + i["game_version"]) for i in self.data["instances"]], state["instance"])
        self.content_instance.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.content_instance.setMinimumContentsLength(12)
        self.content_instance.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.content_query = text_input(state["query"], tr("搜索{kind}").format(kind=tr(KINDS[state["kind"]])), "content-search")
        self.content_query.setMinimumWidth(0)
        self.content_query.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addLayout(row(self.content_query, self.content_provider, Button(tr("搜索"), self.search_content, True)))
        if state["kind"] != "modpack":
            layout.addWidget(self.content_instance)
        else:
            self.content_instance.setParent(self.page)
            self.content_instance.hide()
        self.content_kind.currentIndexChanged.connect(self.content_filter_changed)
        self.content_provider.currentIndexChanged.connect(self.content_filter_changed)
        self.content_instance.currentIndexChanged.connect(self.content_filter_changed)
        self.content_query.returnPressed.connect(self.search_content)
        self.content_result = QWidget()
        self.content_result.setMinimumWidth(0)
        self.content_result.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.content_result_layout = QVBoxLayout(self.content_result)
        self.content_result_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.content_result)
        self.search_content(reset=False)

    def content_filter_changed(self):
        self.search_state["offset"] = 0
        self.search_content()

    def search_content(self, checked=False, reset=True):
        if self.page_id != "discover":
            return
        state = self.search_state
        state.update(kind=self.content_kind.currentData(), provider=self.content_provider.currentData(), instance=self.content_instance.currentData(), query=self.content_query.text().strip())
        self.content_query.setPlaceholderText(tr("输入模组名称或 GitHub 仓库地址") if state["provider"] == "github" else tr("搜索{kind}").format(kind=tr(KINDS[state["kind"]])))
        if reset:
            state["offset"] = 0
        self.content_instance.setEnabled(state["kind"] != "modpack")
        self.project_search_generation += 1
        generation = self.project_search_generation
        params = {"query": state["query"], "kind": state["kind"], "provider": state["provider"], "offset": state["offset"]}
        instance = next((i for i in self.data["instances"] if i["id"] == state["instance"]), None)
        if instance and state["kind"] != "modpack":
            params.update(version=instance["game_version"], loader=instance["loader"])
        clear_layout(self.content_result_layout)
        self.content_result_layout.addWidget(label(tr("正在连接内容平台…"), "muted"))
        def received(data):
            if self.page_id != "discover" or generation != self.project_search_generation:
                return
            clear_layout(self.content_result_layout)
            if state["provider"] == "faithful":
                self.content_result_layout.addLayout(faithful_links(self,credit=True))
            if data.get("notice"):
                self.content_result_layout.addWidget(label(tr(data["notice"]), "muted", True))
            if data.get("external_search_url"):
                self.content_result_layout.addWidget(Button(tr("在MC百科搜索"),
                    lambda: self.open_content_url(data["external_search_url"])))
            if data.get("order_label"):
                summary = tr("{} 个结果 · {}").format(short_count(data["total_hits"]), tr(data["order_label"]))
            else:
                summary = short_count(data["total_hits"]) + tr(" 个结果 · 按下载量排序")
            if not data.get("waiting_query") and not data.get("unavailable"):
                self.content_result_layout.addWidget(label(summary, "muted"))
            for index, project in enumerate(data["hits"]):
                frame, box = card()
                frame.setObjectName("project-card")
                frame.setMinimumWidth(0)
                frame.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
                box.setContentsMargins(16, 16, 16, 16)
                line = QHBoxLayout()
                line.setSpacing(14)
                line.addWidget(ProjectPreview(self.images, project.get("icon_url", "")), 0, Qt.AlignTop)
                text = QVBoxLayout()
                text.setSpacing(5)
                text.addWidget(ElidedLabel(project["title"], "section"))
                text.addWidget(ElidedLabel(project.get("author", ""), "muted"))
                description = SummaryLabel(project["description"])
                text.addWidget(description)
                line.addLayout(text, 1)
                actions = QVBoxLayout()
                if project.get("downloads") is not None:
                    actions.addWidget(label("↓ " + short_count(project["downloads"]), "muted"), 0, Qt.AlignRight)
                actions.addStretch()
                button = Button(tr("查看百科") if project.get("action") == "article" else tr("选择版本"),
                                lambda checked=False, item=project: self.project_versions(item), True)
                button.setFixedWidth(138 if language() == "en_US" else 104)
                actions.addWidget(button)
                line.addLayout(actions)
                box.addLayout(line)
                self.content_result_layout.addWidget(frame)
            if not data["hits"] and not data.get("waiting_query") and not data.get("unavailable"):
                hint = tr("试试其他关键词，或在原站继续搜索。") if state["provider"] == "mcmod" else tr("试试英文关键词，或取消目标实例筛选。")
                self.content_result_layout.addWidget(empty(tr("没有找到相关内容"), hint))
            if data.get("waiting_query") or data.get("unavailable"):
                return
            previous = Button(tr("上一页"), lambda: self.content_page(-1))
            following = Button(tr("下一页"), lambda: self.content_page(1))
            previous.setEnabled(state["offset"] > 0)
            following.setEnabled(state["offset"] + 18 < data["total_hits"])
            self.content_result_layout.addLayout(row(None, previous, label(tr("第 {} 页").format(state["offset"]//18+1), "muted"), following, None))
            self.content_result_layout.addStretch(1)
        def failed(message):
            if self.page_id == "discover" and generation == self.project_search_generation:
                clear_layout(self.content_result_layout)
                self.content_result_layout.addWidget(empty(tr("暂时无法连接内容平台"), message, Button(tr("重试"), self.search_content)))
        self.request("content/search?" + urllib.parse.urlencode(params), success=received, error=failed)

    def content_page(self, direction):
        self.search_state["offset"] = max(0, self.search_state["offset"] + direction*18)
        self.search_content(reset=False)
        self.page.verticalScrollBar().setValue(0)

    def page_settings(self, layout):
        config = self.settings_draft
        category = self.settings_category
        if category != "account":
            self.intro(layout, {"game": tr("游戏"), "download": tr("下载"), "appearance": tr("外观"), "ai":tr("智能排查"), "advanced": tr("高级")}[category])
        self.settings_fields = {}
        section, box = card()
        if category != "game":
            section.setParent(self.page)
            section.hide()
        else:
            layout.addWidget(section)
        self.memory_auto_check = QCheckBox(tr("自动分配"))
        self.memory_auto_check.setObjectName("memory-auto")
        self.memory_auto_check.setChecked(config.get("memory_auto", True))
        box.addWidget(self.memory_auto_check)
        self.memory_slider = GlassSlider(Qt.Horizontal)
        self.memory_slider.setRange(512, 65536)
        self.memory_slider.setSingleStep(256)
        self.memory_slider.setValue(config["memory"])
        self.memory_slider.setObjectName("memory-slider")
        self.memory_label = label("{:.1f} GB".format(config["memory"]/1024), "section")
        self.memory_slider.valueChanged.connect(self.update_memory_preview)
        box.addLayout(row(label(tr("游戏内存"), "fieldLabel"), None, self.memory_label))
        box.addWidget(self.memory_slider)
        self.memory_status = label("", "muted")
        box.addWidget(self.memory_status)
        self.memory_auto_check.toggled.connect(self.update_memory_preview)
        self.update_memory_preview()
        self.settings_fields["java_path"] = text_input(config["java_path"], tr("留空自动匹配，或指定 java.exe"), "java-path")
        java_container = QWidget()
        java_layout = QVBoxLayout(java_container)
        java_layout.setContentsMargins(0, 0, 0, 0)
        java_layout.addLayout(row(self.settings_fields["java_path"], Button(tr("浏览"), self.choose_java), Button(tr("检测"), self.detect_java)))
        self.java_detected_layout = java_layout
        box.addWidget(field(tr("Java 可执行文件"), java_container))
        self.java_auto = QCheckBox(tr("自动匹配与下载 Java（Eclipse Temurin）"))
        self.java_auto.setChecked(config["java_auto"])
        box.addWidget(self.java_auto)
        box.addWidget(Button(tr('Java 管理中心'), lambda: self.open_management('java')))
        dimensions = QHBoxLayout()
        for key, title, low, high in (("width", tr("游戏窗口宽度"), 640, 7680), ("height", tr("游戏窗口高度"), 480, 4320)):
            widget = QSpinBox()
            widget.setRange(low, high)
            widget.setValue(config[key])
            widget.setObjectName("setting-" + key)
            self.settings_fields[key] = widget
            dimensions.addWidget(field(title, widget))
        box.addLayout(dimensions)
        section, box = card()
        if category != "download":
            section.setParent(self.page)
            section.hide()
        else:
            layout.addWidget(section)
        self.settings_fields["source"] = combo([("bmclapi", tr("BMCLAPI 镜像")), ("official", tr("Mojang 官方源"))], config["source"])
        box.addWidget(field(tr("游戏下载源"), self.settings_fields["source"]))
        concurrency = QSpinBox()
        concurrency.setRange(1, 16)
        concurrency.setValue(config["concurrency"])
        self.settings_fields["concurrency"] = concurrency
        box.addWidget(field(tr("总下载连接数"), concurrency))
        self.download_parallel = QCheckBox(tr("多线程分段下载"))
        self.download_parallel.setChecked(config.get("download_parallel",True))
        box.addWidget(self.download_parallel)
        segments = QSpinBox()
        segments.setRange(1,16)
        segments.setValue(config.get("download_segments",8))
        segments.setEnabled(self.download_parallel.isChecked())
        self.download_parallel.toggled.connect(segments.setEnabled)
        self.settings_fields["download_segments"] = segments
        box.addWidget(field(tr("每个大文件分段数"),segments))
        placeholder = ("已保存；留空保留" if config.get("curseforge_custom_configured") else
                       "已内置；可填写自定义密钥" if config.get("curseforge_builtin") else "输入 CurseForge API Key")
        key = text_input(placeholder=tr(placeholder))
        key.setEchoMode(QLineEdit.Password)
        key.setText(config.get("curseforge_key", ""))
        self.settings_fields["curseforge_key"] = key
        box.addWidget(field("CurseForge API Key", key))
        if category == "account":
            self.account_management(layout)
        section, box = card()
        if category != "appearance":
            section.setParent(self.page)
            section.hide()
        else:
            layout.addWidget(section)
        self.settings_fields["language"] = combo(LANGUAGES, config.get("language", "zh_CN"), "language")
        box.addWidget(field(tr("语言 / Language"), self.settings_fields["language"]))
        box.addWidget(label(tr("界面配色"), "section"))
        self.settings_fields["color_scheme"] = combo([(key,item["name"]) for key,item in SCHEMES.items()],config.get("color_scheme","starmoon"),"color-scheme")
        box.addWidget(self.settings_fields["color_scheme"])
        colors_preview = PalettePreview(config.get("color_scheme","starmoon"))
        box.addWidget(colors_preview)
        self.settings_fields["color_scheme"].currentIndexChanged.connect(lambda: colors_preview.set_scheme(self.settings_fields["color_scheme"].currentData()))
        box.addWidget(label(tr("启动器窗口"), "section"))
        current_size = "{}x{}".format(config.get("launcher_width", 900), config.get("launcher_height", 600))
        presets = combo([("custom", tr("自定义尺寸")), ("900x600", tr("紧凑 · 900 × 600")), ("1100x720", tr("标准 · 1100 × 720")), ("1280x800", tr("宽敞 · 1280 × 800")), ("1440x900", tr("大窗口 · 1440 × 900"))], current_size if current_size in ("900x600", "1100x720", "1280x800", "1440x900") else "custom", "launcher-size-preset")
        box.addWidget(presets)
        dimensions = QHBoxLayout()
        for key, title, low, high in (("launcher_width", tr("宽度"), 900, 7680), ("launcher_height", tr("高度"), 600, 4320)):
            widget = QSpinBox()
            widget.setRange(low, high)
            widget.setValue(config.get(key, 900 if key.endswith("width") else 600))
            widget.setSuffix(" px")
            widget.setObjectName("setting-" + key)
            self.settings_fields[key] = widget
            dimensions.addWidget(field(title, widget))
        box.addLayout(dimensions)
        def preset_changed():
            if presets.currentData() != "custom":
                width, height = map(int, presets.currentData().split("x"))
                self.settings_fields["launcher_width"].setValue(width)
                self.settings_fields["launcher_height"].setValue(height)
        presets.currentIndexChanged.connect(preset_changed)
        def size_edited():
            size = "{}x{}".format(self.settings_fields["launcher_width"].value(), self.settings_fields["launcher_height"].value())
            presets.blockSignals(True)
            presets.setCurrentIndex(max(0, presets.findData(size)))
            presets.blockSignals(False)
        self.settings_fields["launcher_width"].valueChanged.connect(size_edited)
        self.settings_fields["launcher_height"].valueChanged.connect(size_edited)
        box.addWidget(label(tr("实时应用；超出屏幕时自动适配。"), "muted"))
        self.animations_check = QCheckBox(tr("界面动效"))
        self.animations_check.setChecked(config.get("animations", True))
        box.addWidget(self.animations_check)
        box.addWidget(label(tr("壁纸"), "section"))
        self.background_file_label = label(wallpaper_label(config), "muted")
        preset_button = Button(tr("更改预设"),self.choose_preset_background)
        preset_button.setObjectName("choose-preset-wallpaper")
        box.addLayout(row(self.background_file_label,None,preset_button))
        choose = Button(tr("选择壁纸"),self.choose_background)
        choose.setObjectName("choose-wallpaper")
        restore = Button(tr("恢复默认"),self.reset_background)
        restore.setObjectName("reset-wallpaper")
        clear = Button(tr("清除壁纸"),self.clear_background)
        clear.setObjectName("clear-wallpaper")
        box.addLayout(row(choose,restore,clear))
        self.background_opacity = GlassSlider(Qt.Horizontal)
        self.background_opacity.setRange(0, 100)
        self.background_opacity.setValue(config.get("background_opacity", 55))
        box.addWidget(field(tr("主窗口壁纸强度"), self.background_opacity))
        self.glass_opacity = GlassSlider(Qt.Horizontal)
        self.glass_opacity.setRange(0, 120)
        self.glass_opacity.setValue(config.get("glass_opacity", 72))
        box.addWidget(field(tr("玻璃不透明度"), self.glass_opacity))
        self.glass_refraction = GlassSlider(Qt.Horizontal)
        self.glass_refraction.setRange(0, 200)
        self.glass_refraction.setValue(config.get("glass_refraction", 65))
        box.addWidget(field(tr("玻璃折射"), self.glass_refraction))
        section, box = card()
        if category != "ai":
            section.setParent(self.page)
            section.hide()
        else:
            layout.addWidget(section)
        self.settings_fields["ai_base_url"] = text_input(config.get("ai_base_url",""),tr("https://你的服务地址/v1"),"ai-base-url")
        self.settings_fields["ai_model"] = text_input(config.get("ai_model",""),tr("服务商提供的模型名称"),"ai-model")
        self.settings_fields["ai_api_key"] = text_input(config.get("ai_api_key",""),tr("已保存；留空保留") if config.get("ai_key_configured") else "API Key","ai-api-key")
        self.settings_fields["ai_api_key"].setEchoMode(QLineEdit.Password)
        for key,title in (("ai_base_url",tr("接口地址（兼容 Chat Completions）")),("ai_model",tr("模型")),("ai_api_key","API Key")):
            box.addWidget(field(title,self.settings_fields[key]))
        self.ai_clear_key = QCheckBox(tr("清除已保存的 API Key"))
        self.ai_clear_key.setChecked(config.get("ai_clear_key",False))
        box.addWidget(self.ai_clear_key)
        self.ai_crash_check = QCheckBox(tr("崩溃后自动分析"))
        self.ai_crash_check.setObjectName("ai-crash-analysis")
        self.ai_crash_check.setChecked(config.get("ai_crash_analysis",True))
        box.addWidget(self.ai_crash_check)
        self.ai_preflight_check = QCheckBox(tr("启动前自动排查"))
        self.ai_preflight_check.setObjectName("ai-auto-preflight")
        self.ai_preflight_check.setChecked(config.get("ai_auto_preflight",False))
        box.addWidget(self.ai_preflight_check)
        dependency_check = QCheckBox(tr("模组依赖默认使用大模型检查"))
        dependency_check.setObjectName("ai-dependency-check")
        dependency_check.setChecked(config.get("ai_dependency_check", True))
        self.settings_fields["ai_dependency_check"] = dependency_check
        box.addWidget(dependency_check)
        for key, title in (("ai_github_search", tr("AI 搜索 GitHub 模组")),
                           ("ai_pack_download_fallback", tr("整合包下载失败时使用 AI 补下载"))):
            widget = QCheckBox(title)
            widget.setObjectName(key.replace("_", "-"))
            widget.setChecked(config.get(key, False))
            self.settings_fields[key] = widget
            box.addWidget(widget)
        box.addWidget(label(tr("共用以上模型和 API。优先检索 MC百科及其项目链接，补下载保留原文件哈希校验。"), "muted", True))
        box.addWidget(label(tr("发送脱敏模组元数据；崩溃分析还会发送相关错误日志。过滤令牌、账号和常见个人路径。静态排查无法保证发现所有运行期冲突。"),"muted",True))
        box.addLayout(row(Button(tr("测试连接"),self.test_ai_connection),Button(tr("查看上次结果"),self.show_latest_diagnostic),None))
        section, box = card()
        if category != "advanced":
            section.setParent(self.page)
            section.hide()
        else:
            layout.addWidget(section)
        box.addWidget(label(tr("启动器更新"), "section"))
        automatic = QCheckBox(tr("启动时自动检查更新"))
        automatic.setObjectName("auto-check-launcher-updates")
        automatic.setChecked(config.get("auto_check_updates", True))
        self.settings_fields["auto_check_updates"] = automatic
        box.addWidget(automatic)
        box.addWidget(label(tr("从 GitHub Releases 获取正式版本，安装前需要关闭游戏和其他任务。"), "muted", True))
        box.addWidget(Button(tr("检查启动器更新"), self.open_launcher_updates))
        for key, title in (("jvm_args", tr("额外 JVM 参数")), ("game_args", tr("额外游戏参数"))):
            widget = QPlainTextEdit(config[key])
            widget.setFixedHeight(90)
            self.settings_fields[key] = widget
            box.addWidget(field(title, widget))
        box.addWidget(Button(tr("数据目录"), lambda: self.mutate("folder", {"target": "data"})))
        self.connect_live_settings()

    def collect_settings(self):
        data = {"memory": self.memory_slider.value(), "memory_auto": self.memory_auto_check.isChecked(), "java_auto": self.java_auto.isChecked(), "animations": self.animations_check.isChecked(), "theme": "glass",
                "download_parallel":self.download_parallel.isChecked(),"ai_crash_analysis":self.ai_crash_check.isChecked(),"ai_auto_preflight":self.ai_preflight_check.isChecked(),"ai_clear_key":self.ai_clear_key.isChecked(),
                "background_opacity": self.background_opacity.value(), "glass_opacity": self.glass_opacity.value(), "glass_refraction": self.glass_refraction.value(), "background_path": self.settings_draft.get("background_path", "")}
        for key, widget in self.settings_fields.items():
            data[key] = (widget.currentData() if isinstance(widget, QComboBox) else widget.value() if isinstance(widget, QSpinBox)
                         else widget.isChecked() if isinstance(widget, QCheckBox) else widget.toPlainText() if isinstance(widget, QPlainTextEdit) else widget.text().strip())
        if not data["curseforge_key"]:
            del data["curseforge_key"]
            self.settings_draft.pop("curseforge_key", None)
        if not data["ai_api_key"]:
            del data["ai_api_key"]
            self.settings_draft.pop("ai_api_key",None)
        self.settings_draft.update(data)
        return data


    def update_memory_preview(self, *_):
        if not hasattr(self, "memory_status"):
            return
        info = physical_memory()
        auto = self.memory_auto_check.isChecked()
        self.memory_slider.setEnabled(not auto)
        instance = self.selected_instance()
        try:
            count = sum(1 for p in (self.service.store.game_dir(instance["id"]) / "mods").glob("*.jar") if p.is_file()) if instance else 0
        except LauncherError:
            count = 0
        value = automatic_memory(info["total"], info["available"], count) if auto else self.memory_slider.value()
        self.memory_label.setText((tr("自动 · ") if auto else "") + "{:.1f} GB".format(value/1024))
        self.memory_status.setText(tr("可用 {:.1f} GB / 总计 {:.1f} GB").format(info["available"]/1024, info["total"]/1024))

    def apply_appearance(self):
        config = self.data["config"]
        remove_focus_rings()
        apply_theme(config.get("color_scheme","starmoon"))
        QApplication.instance().setStyleSheet(glass_style(config.get("glass_opacity", 72)))
        QApplication.instance().setProperty("glassRefraction", config.get("glass_refraction", 65))
        self.glass_surface.set_glass_opacity(config.get("glass_opacity", 72))
        self.update_page_background()
        self.glass_surface.update()

    def update_page_background(self):
        config = self.data["config"]
        path = wallpaper_path(config)
        self.glass_surface.set_background(path, config.get("background_opacity", 55))
        if self.page_id == "home" and hasattr(self,"page"):
            for illustration in self.page.findChildren(MoonScene):
                illustration.setVisible(self.glass_surface.image.isNull())

    def choose_background(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("选择背景"), "", tr("背景图片 (*.png *.jpg *.jpeg *.webp *.bmp)"))
        if not path:
            return
        request_id = uuid.uuid4().hex
        self.background_request = request_id
        def prepare():
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            size = reader.size()
            if not size.isValid() or size.width()*size.height() > 100000000:
                raise LauncherError(tr("无法读取背景图片，或图片尺寸过大"))
            reader.setScaledSize(size.scaled(2560, 1600, Qt.KeepAspectRatio))
            image = reader.read()
            destination = self.service.store.root / "appearance" / ("background-" + uuid.uuid4().hex + ".png")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if image.isNull() or not image.save(str(destination), "PNG"):
                raise LauncherError(tr("保存背景失败"))
            return str(destination)
        def chosen(destination):
            if self.background_request == request_id:
                self.set_wallpaper("custom",destination)
        self.async_call(prepare, chosen)

    def choose_preset_background(self):
        dialog = NativeDialog(self,tr("预设壁纸"),width=620)
        dialog.setObjectName("preset-wallpaper-dialog")
        dialog.resize(620,min(600,self.height()))
        grid = QGridLayout()
        grid.setSpacing(10)
        config = self.service.store.config()
        current = "default" if wallpaper_mode(config) == "default" else config.get("background_preset") if wallpaper_mode(config) == "preset" else None
        def select(preset):
            self.background_request = uuid.uuid4().hex
            if self.set_wallpaper("default" if preset == "default" else "preset",preset=preset):
                dialog.accept()
        for index,(preset,name,_) in enumerate(BACKGROUND_PRESETS):
            tile = WallpaperTile(tr(name),preset_background(preset),current == preset,lambda checked=False,key=preset:select(key))
            tile.setObjectName("wallpaper-preset-"+preset)
            grid.addWidget(tile,index//2,index%2)
        grid.setColumnStretch(0,1)
        grid.setColumnStretch(1,1)
        dialog.body.addLayout(grid)
        return self.open_dialog(dialog)

    def reset_background(self):
        self.background_request = uuid.uuid4().hex
        self.set_wallpaper("default")

    def clear_background(self):
        self.background_request = uuid.uuid4().hex
        self.set_wallpaper("none")

    def set_wallpaper(self,mode,path="",preset=None):
        try:
            changes = {"background_mode":mode,"background_path":path}
            if preset is not None or mode == "default": changes["background_preset"] = preset or "default"
            result = self.service.config_update(changes)
            self.data["config"].update(result)
            self.settings_draft.update({key:result[key] for key in ("background_mode","background_path","background_preset")})
            self.apply_setting_effects("background_mode")
            if self.page_id == "settings": self.background_file_label.setText(wallpaper_label(result))
            return True
        except Exception as exc:
            self.toast(tr("壁纸未应用：")+str(exc),True)
            return False

    def choose_java(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("选择 Java 可执行文件"), "", tr("Java (java.exe);;所有文件 (*)"))
        if path:
            self.settings_fields["java_path"].setText(path)

    def detect_java(self):
        def found(items):
            if self.page_id != "settings":
                return
            detected = combo([("", tr("选择检测到的 Java…"))] + [(i["path"], "Java " + i["version"] + " · " + i["path"]) for i in items])
            detected.setObjectName("detected-java")
            detected.currentIndexChanged.connect(lambda: self.settings_fields["java_path"].setText(detected.currentData() or ""))
            self.java_detected_layout.addWidget(detected)
            if not items:
                self.toast(tr("未检测到 Java，可启用自动下载"))
        self.request("java", success=found)

    def open_management(self, kind, identifier=None):
        from .management_ui import BulkModsDialog, DependencyDialog, JavaDialog, HistoryDialog, PackUpgradeDialog
        dialog_type = {'bulk': BulkModsDialog, 'dependencies': DependencyDialog, 'java': JavaDialog, 'history': HistoryDialog, 'upgrade': PackUpgradeDialog}[kind]
        return self.open_dialog(dialog_type(self, identifier))

    def open_dialog(self, dialog):
        self.dialogs.append(dialog)
        dialog.finished.connect(lambda _: self.dialogs.remove(dialog) if dialog in self.dialogs else None)
        dialog.show()
        return dialog

    def add_offline(self):
        dialog = NativeDialog(self, tr("添加离线账号"), tr("3–16 位英文、数字或下划线。"), 500)
        dialog.resize(500, 300)
        name = text_input(placeholder="Steve", name="offline-name")
        name.setMaxLength(16)
        dialog.body.addWidget(field(tr("玩家名称"), name))
        submit = Button(tr("添加账号"), primary=True)
        submit.setObjectName("offline-submit")
        def save():
            submit.setEnabled(False)
            def failed(message):
                submit.setEnabled(True)
                dialog.error(message)
            def done(_):
                dialog.accept()
                self.refresh()
                self.toast(tr("离线账号已添加"))
            self.request("accounts/offline", {"name": name.text().strip()}, done, failed)
        submit.clicked.connect(save)
        name.returnPressed.connect(save)
        dialog.footer.addWidget(submit)
        self.open_dialog(dialog)
        name.setFocus()

    def delete_account(self, account_id):
        if QMessageBox.question(self, tr("移除账号"), tr("移除此账号？以后仍可重新登录。")) == QMessageBox.Yes:
            self.mutate("accounts/delete", {"id": account_id}, lambda _: self.set_settings_category("account"))

    def microsoft_login(self):
        self.open_dialog(MicrosoftDialog(self))

    def new_instance(self, version=None):
        if not version or isinstance(version, bool):
            self.navigate("install")
        else:
            self.open_dialog(InstallDialog(self, version))

    def open_instance(self, instance_id):
        self.flush_settings()
        self.flush_instance_settings()
        self.service.config_update({"selected_instance": instance_id})
        self.data = self.service.state()
        self.instance_id = instance_id
        self.instance_category = "overview"
        self.navigate("instance")
        return True

    def show_game_log(self, instance_id,process_id=None):
        if process_id:
            instance = self.service.store.instance(instance_id)
            process = next((p for p in self.data["processes"] if p.get("id")==process_id),{})
            dialog = NativeDialog(self,instance["name"]+" · PID "+str(process.get("pid","")),width=760)
            dialog.resize(min(760,self.width()),min(550,self.height()))
            dialog.setObjectName("process-log-"+process_id)
            editor = QPlainTextEdit()
            editor.setReadOnly(True)
            editor.setFont(QFont("Consolas",10))
            dialog.body.addWidget(editor)
            def load():
                self.request("log?"+urllib.parse.urlencode({"instance":instance_id,"process":process_id}),success=lambda data:editor.setPlainText(data["text"]) if isValid(editor) and dialog.isVisible() else None)
            dialog.footer.addWidget(Button(tr("刷新"),load))
            dialog.footer.addWidget(Button(tr("分析崩溃"),lambda:(dialog.accept(),self.run_diagnostic(instance_id,"crash",process_id))))
            self.open_dialog(dialog)
            load()
            return
        if self.open_instance(instance_id):
            self.set_instance_category("logs")

    def set_instance_category(self, category):
        self.flush_instance_settings()
        self.instance_category = category
        self.navigate("instance")

    def page_instance(self, layout):
        if self.instance_category == "mods":
            # Reserve the scrollbar gutter even when a filtered list fits.
            self.page.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
            self.page.verticalScrollBar().setStyleSheet("QScrollBar::handle:vertical:disabled { background:transparent; }")
        self.instance_panel = InstancePanel(self, self.instance_id, self.instance_category)
        layout.addWidget(self.instance_panel)

    def check_current(self):
        instance = self.selected_instance()
        if not instance:
            self.toast(tr("请先选择一个版本"))
            return
        self.run_diagnostic(instance["id"],"preflight")

    def run_diagnostic(self,instance_id,kind="preflight",process_id=None):
        if not self.service.diagnostics.configured():
            self.set_settings_category("ai")
            self.toast(tr("请先填写接口地址、模型和 API Key"))
            return
        body = {"instance":instance_id,"kind":kind}
        if process_id: body["process"] = process_id
        self.mutate("diagnostics/run",body,show_tasks=True)

    def show_diagnostic(self,report,resume_account=None,resume_server=None):
        instance_id = report.get("instance_id","")
        try:
            name = self.service.store.instance(instance_id)["name"]
        except LauncherError:
            name = tr("游戏版本")
        dialog = NativeDialog(self,tr("崩溃分析") if report.get("kind") == "crash" else tr("启动前排查"),name,width=640)
        dialog.setObjectName("diagnostic-result")
        dialog.resize(min(640,self.width()),min(420,self.height()))
        dialog.body.addWidget(label(tr("原因"),"fieldLabel"))
        cause = label(tr(report.get("cause",tr("暂时无法确定"))),wrap=True)
        cause.setObjectName("diagnostic-cause")
        dialog.body.addWidget(cause)
        dialog.body.addWidget(label(tr("建议解决方法"),"fieldLabel"))
        suggestion = label(tr(report.get("suggestion",tr("请查看日志。"))),wrap=True)
        suggestion.setObjectName("diagnostic-suggestion")
        dialog.body.addWidget(suggestion)
        if report.get("source") == "error":
            dialog.body.addWidget(label(tr("分析未完成"),"muted"))
        if resume_account:
            def proceed():
                dialog.accept()
                payload = {"id":instance_id,"account":resume_account,"skip_preflight":True}
                if resume_server: payload['server'] = resume_server
                self.mutate("instances/launch",payload,show_tasks=True)
            resume = Button(tr("仍然启动"),proceed,True)
            resume.setObjectName("diagnostic-launch-anyway")
            dialog.footer.addWidget(resume)
        self.open_dialog(dialog)

    def test_ai_connection(self):
        self.flush_settings()
        values = self.collect_settings()
        body = {key:values.get(key,"") for key in ("ai_base_url","ai_model","ai_api_key","ai_clear_key")}
        self.toast(tr("正在测试模型接口…"))
        self.request("diagnostics/test",body,lambda _:self.toast(tr("接口连接成功")))

    def show_latest_diagnostic(self):
        instance = self.selected_instance()
        if not instance:
            self.toast(tr("请先选择一个版本"))
            return
        def latest():
            reports = [self.service.diagnostics.latest(instance["id"],kind) for kind in ("crash","preflight")]
            return max(reports,key=lambda item:item.get("time",0))
        self.async_call(latest,lambda report:self.show_diagnostic(report) if report else self.toast(tr("还没有排查结果")))

    def launch(self, instance_id):
        if not self.account():
            self.toast(tr("请先添加或登录一个账号"))
            self.navigate("accounts")
            return
        self.mutate("instances/launch", {"id": instance_id, "account": self.account()["id"]}, show_tasks=True)

    def stop_game(self, instance_id,process_id=None):
        if QMessageBox.question(self, tr("结束游戏"), tr("请先在游戏中保存世界。现在结束游戏？")) == QMessageBox.Yes:
            self.mutate("processes/stop" if process_id else "instances/stop", {"id": process_id or instance_id})

    def explore(self, kind="mod", instance=""):
        self.search_state.update(kind=kind, instance=instance, offset=0)
        self.navigate("discover")

    def project_versions(self, project):
        if project.get("action") == "article":
            self.open_content_url(project.get("project_url", ""))
            return
        self.open_dialog(ProjectDialog(self, project, dict(self.search_state)))

    def open_content_url(self, url):
        try:
            url = https_url(url)
            if not QDesktopServices.openUrl(QUrl(url)):
                self.toast(tr("无法打开浏览器，请检查系统默认浏览器设置。"), True)
        except LauncherError as exc:
            self.toast(str(exc), True)

    def open_mod_website(self, url):
        from .mod_sites import website_url
        url = website_url(url)
        if not url:
            self.toast(tr("无效的模组网站地址"), True)
            return
        if not QDesktopServices.openUrl(QUrl(url)):
            self.toast(tr("无法打开浏览器，请检查系统默认浏览器设置。"), True)

    def import_menu(self):
        dialog = NativeDialog(self, tr("添加游戏内容"), width=540)
        dialog.resize(540, 290)
        dialog.body.addWidget(Button(tr("导入整合包（.mrpack / .zip）"), lambda: (dialog.accept(), self.import_pack()), True))
        dialog.body.addWidget(Button(tr("添加 .minecraft 文件夹"), lambda: (dialog.accept(), self.import_directory())))
        self.open_dialog(dialog)

    def import_pack(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("选择整合包"), "", tr("Minecraft 整合包 (*.mrpack *.zip)"))
        if not path:
            return
        self.import_pack_paths([path])

    def import_pack_paths(self, paths):
        seen = set()
        for path in paths:
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            self.service.tasks.submit(tr("导入整合包 · ") + Path(path).name,
                lambda task, source=path: self.service.content.import_file(source, task))
        self.refresh()
        self.navigate("downloads")

    def import_directory(self):
        path = QFileDialog.getExistingDirectory(self, tr("添加 .minecraft 文件夹"))
        if path:
            def added(result):
                self.data = self.service.state()
                self.navigate("library")
                message = tr("已添加文件夹 · {} 个版本").format(len(result["instances"]))
                if result.get("skipped"):
                    message += tr(" · {} 个版本需修复").format(len(result["skipped"]))
                self.toast(message)
            self.request("folders/add", {"path": path}, added)

    def scan_minecraft_folders(self):
        dialog = MinecraftScanDialog(self)
        self.open_dialog(dialog)
        dialog.start()
        return dialog

    def about(self):
        self.navigate("about")

    def open_launcher_updates(self, info=None):
        from .update_ui import UpdateDialog
        for dialog in self.dialogs:
            if isinstance(dialog, UpdateDialog):
                dialog.raise_()
                dialog.activateWindow()
                return dialog
        return self.open_dialog(UpdateDialog(self, info if isinstance(info, dict) else None))

    def startup_updates(self):
        outcome = self.service.updater.take_outcome()
        if outcome and outcome.get("status") == "failed":
            self.toast(tr(outcome.get("message", "更新未完成，可重试或打开发布页")), True)
        elif outcome and outcome.get("status") == "updated" and outcome.get("version") == __version__:
            self.toast(tr("启动器已更新至 {}").format(__version__))
        if self._update_checking or not self.service.updater.startup_due():
            return
        if any(dialog.objectName() == "launcher-update-dialog" for dialog in self.dialogs):
            return
        self._update_checking = True

        def checked(info):
            self._update_checking = False
            if info.get("available") and not self._installing_update:
                if self.dialogs or self.background_mode:
                    self.toast(tr("发现启动器新版本 {}，可在关于页面更新").format(info["version"]))
                else:
                    self.open_launcher_updates(info)

        def failed(_message):
            # A failed background check must not interrupt launching or login.
            self._update_checking = False

        self.async_call(self.service.updater.check, checked, failed)

    def update_block_reason(self):
        if self.service.local_servers.active(): return '请先停止所有本地服务端，再安装启动器更新'
        if any(process["running"] or not process.get("finalized", True) for process in self.service.game.process_status()):
            return "请先关闭所有游戏，再安装启动器更新"
        if any(task["status"] in ("queued", "running") for task in self.service.tasks.list()):
            return "请先等待或取消其他任务，再安装启动器更新"
        with self.service.accounts.lock:
            if any(session.get("status") == "pending" for session in self.service.accounts.sessions.values()):
                return "请先完成或取消登录，再安装启动器更新"
        return ""

    def install_launcher_update(self, failed):
        reason = self.update_block_reason()
        if reason:
            failed(reason)
            return
        if not self.flush_settings() or not self.flush_instance_settings():
            failed(tr("设置未能保存，请处理设置错误后重试更新"))
            return

        def prepared(result):
            reason = self.update_block_reason()
            if reason:
                failed(reason)
                return
            from .update_install import launch_executable
            path, helper = result
            try:
                launch_executable(helper, ["--apply-update", str(path)])
            except OSError:
                failed(tr("无法启动更新辅助程序，原启动器未关闭"))
                return
            self._installing_update = True
            self.close()

        self.async_call(self.service.updater.prepare, prepared, failed)

    def show_licenses(self):
        dialog = NativeDialog(self, tr("第三方组件与许可证"), "Python · Qt · PySide · Shiboken", 780)
        directory = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "licenses"
        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        viewer.setMinimumHeight(390)
        text = []
        for name in ("NOTICE.txt", "LGPL-3.0.txt", "GPL-3.0.txt", "Python.txt", "OpenSSL.txt", "PyInstaller.txt", "MSAL-and-dependencies.txt", "authlib-injector.txt", "authlib-injector-COPYING.md", "tomli.txt", "dnspython.txt"):
            if (directory / name).is_file():
                text.append("\n=== " + name + " ===\n\n" + (directory / name).read_text(encoding="utf-8", errors="replace"))
        viewer.setPlainText("\n".join(text))
        dialog.body.addWidget(viewer)
        self.open_dialog(dialog)

    def closeEvent(self, event):
        if self._installing_update:
            for dialog in list(self.dialogs):
                dialog.reject()
            self.timer.stop()
            self.tray.hide()
            event.accept()
            QTimer.singleShot(0, QApplication.instance().quit)
            return
        self.flush_settings()
        self.flush_instance_settings()
        self.data = self.service.state()
        active = [t for t in self.data["tasks"] if t["status"] in ("queued", "running")]
        if active:
            question = QMessageBox(self)
            question.setWindowTitle(tr("仍有任务正在执行"))
            question.setText(tr("可以隐藏到后台继续执行，或取消任务后退出。"))
            background = question.addButton(tr("后台继续"), QMessageBox.AcceptRole)
            cancel = question.addButton(tr("取消任务并退出"), QMessageBox.DestructiveRole)
            question.addButton(tr("返回"), QMessageBox.RejectRole)
            question.exec()
            if question.clickedButton() == background:
                self.hide_to_background()
                event.ignore()
                return
            if question.clickedButton() != cancel:
                event.ignore()
                return
            for task in active:
                self.service.tasks.cancel_task(task["id"])
        if self.data.get('server_processes') or any(p["running"] for p in self.data["processes"]):
            self.hide_to_background()
            event.ignore()
            return
        for dialog in list(self.dialogs):
            dialog.reject()
        self.timer.stop()
        self.tray.hide()
        event.accept()

    def hide_to_background(self):
        self.background_mode = True
        for dialog in list(self.dialogs):
            dialog.reject()
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.hide()

    def restore_window(self):
        self.background_mode = False
        self.tray.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def showEvent(self, event):
        self.background_mode = False
        self.tray.hide()
        super().showEvent(event)
        self.backdrop_enabled = enable_backdrop(self)

    def paintEvent(self, event):
        clear_window(self)
        super().paintEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        update_window_mask(self)

    def nativeEvent(self, event_type, message):
        hit = resize_hit(self, message)
        return (True, hit) if hit is not None else super().nativeEvent(event_type, message)


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def application_icon():
    path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets" / "starmoon.png"
    return QIcon(str(path)) if path.is_file() else moon_icon()


class InstallForm(QWidget):
    """All compatible loaders are visible together after choosing Minecraft."""
    def __init__(self, window, version, parent=None, installed=None):
        super().__init__(parent)
        self.window, self.version = window, version
        self.installed = installed or self.finished
        self.selected_loader = "vanilla"
        self.busy = False
        self.rows = {}
        self.setObjectName("install-options")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(16)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.options_scroll = SmoothScroll(self)
        self.options_scroll.setObjectName("loader-options-scroll")
        self.options_scroll.layout.setContentsMargins(0, 0, 10, 0)
        self.options_scroll.layout.setSpacing(12)
        box.addWidget(self.options_scroll, 1)
        self.name = text_input(placeholder=version, name="instance-name")
        self.options_scroll.layout.addWidget(field(tr("版本名称"), self.name))
        self.group = QButtonGroup(self)
        choices = self.choices = QGridLayout()
        self.loader_frames = []
        self.grid_columns = 0
        choices.setHorizontalSpacing(12)
        choices.setVerticalSpacing(12)
        self.options_scroll.layout.addLayout(choices)
        for loader in ("vanilla", "forge", "neoforge", "fabric", "quilt", "optifine", "cleanroom", "liteloader"):
            frame, content = card()
            content.setContentsMargins(16, 12, 16, 12)
            content.setSpacing(6)
            radio = QRadioButton(tr(LOADERS[loader]))
            radio.setObjectName("loader-" + loader)
            self.group.addButton(radio)
            radio.setChecked(loader == "vanilla")
            radio.setEnabled(loader == "vanilla")
            status = ElidedLabel(tr("不安装加载器") if loader == "vanilla" else tr("正在获取兼容版本…"), "muted")
            versions = combo([], name="loader-versions-" + loader)
            versions.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            versions.setMinimumWidth(0)
            versions.setEnabled(False)
            versions.setParent(frame)
            if loader == "vanilla":
                versions.hide()
            retry = Button(tr("重试"), lambda checked=False, key=loader: self.fetch_loader(key))
            retry.setFixedWidth(65)
            retry_policy = retry.sizePolicy()
            retry_policy.setRetainSizeWhenHidden(loader != "vanilla")
            retry.setSizePolicy(retry_policy)
            retry.hide()
            content.addLayout(row(radio, None, retry))
            content.addWidget(status)
            if loader != "vanilla":
                content.addWidget(versions)
            self.loader_frames.append(frame)
            self.rows[loader] = {"radio": radio, "versions": versions, "status": status, "retry": retry}
            radio.toggled.connect(lambda checked, key=loader: self.choose_loader(key) if checked else None)
            versions.activated.connect(lambda index, key=loader: self.rows[key]["radio"].setChecked(True))
        # The containing page places this button in its fixed header.
        self.submit = Button(tr("↓ 安装游戏"), self.install, True, parent=self)
        self.submit.setObjectName("install-submit")
        self.destination = ElidedLabel(str(window.service.store.minecraft_root), "muted")
        self.options_scroll.layout.addWidget(self.destination)
        self.error_label = label("", "muted", True)
        self.error_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.error_label.setStyleSheet("color:" + theme_color("danger").name())
        self.error_label.hide()
        self.options_scroll.layout.insertWidget(0, self.error_label)
        if not window.service.store.minecraft_root.is_dir():
            self.error(tr("请先在版本选择中创建 .minecraft 文件夹"))
            self.options_scroll.layout.insertWidget(1, Button(tr("前往版本选择"), lambda: window.navigate("library")))
        self.reflow_loaders()
        self.update_submit()
        for loader in ("forge", "neoforge", "fabric", "quilt", "optifine", "cleanroom", "liteloader"):
            self.fetch_loader(loader)

    def choose_loader(self, loader):
        self.selected_loader = loader
        self.update_submit()

    def reflow_loaders(self):
        columns = 2 if self.width() >= 470 else 1
        if columns == self.grid_columns:
            return
        self.grid_columns = columns
        while self.choices.count():
            self.choices.takeAt(0)
        self.choices.setColumnStretch(0, 1)
        self.choices.setColumnStretch(1, 1 if columns == 2 else 0)
        for index, frame in enumerate(self.loader_frames):
            if index == 0:
                self.choices.addWidget(frame, 0, 0, 1, columns)
            else:
                self.choices.addWidget(frame, 1 + (index-1)//columns, (index-1)%columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow_loaders()

    def update_submit(self):
        available = self.selected_loader == "vanilla" or self.rows[self.selected_loader]["versions"].count() > 0
        self.submit.setEnabled(not self.busy and available and self.window.service.store.minecraft_root.is_dir())

    def error(self, message):
        self.error_label.setText(tr(message))
        self.error_label.setVisible(bool(message))
        if message:
            self.options_scroll.ensureWidgetVisible(self.error_label)

    def fetch_loader(self, loader):
        entry = self.rows[loader]
        # Hiding a focused button otherwise advances focus to another card,
        # which QScrollArea automatically scrolls into view.
        if entry["retry"].hasFocus():
            self.options_scroll.setFocus(Qt.OtherFocusReason)
        entry["retry"].hide()
        entry["versions"].clear()
        self.update_submit()
        entry["radio"].setEnabled(False)
        entry["versions"].setEnabled(False)
        entry["status"].setText(tr("正在获取兼容版本…"))
        def received(items):
            if not isValid(self):
                return
            entry["versions"].clear()
            for item in items:
                entry["versions"].addItem(item["version"] + (tr(" · 稳定版") if item.get("stable") else tr(" · 测试版")), item["version"])
            recommended = next((index for index, item in enumerate(items) if item.get("stable")), 0)
            entry["versions"].setCurrentIndex(recommended)
            entry["radio"].setEnabled(bool(items))
            entry["versions"].setEnabled(bool(items))
            entry["status"].setText(tr("{} 个兼容版本").format(len(items)) if items else tr("暂无兼容版本"))
            if not items and self.selected_loader == loader:
                self.rows["vanilla"]["radio"].setChecked(True)
            self.update_submit()
        def failed(message):
            if not isValid(self):
                return
            entry["status"].setText(tr("获取失败"))
            entry["status"].setToolTip(message)
            entry["retry"].show()
            if self.selected_loader == loader:
                self.rows["vanilla"]["radio"].setChecked(True)
            self.update_submit()
        self.window.request("loaders?" + urllib.parse.urlencode({"version": self.version, "loader": loader}), success=received, error=failed)

    def install(self):
        if not self.submit.isEnabled():
            return
        self.busy = True
        self.update_submit()
        loader = self.selected_loader
        name = self.name.text().strip() or (self.version + (" · " + tr(LOADERS[loader]) if loader != "vanilla" else ""))
        def failed(message):
            if isValid(self):
                self.busy = False
                self.update_submit()
                self.error(message)
        self.window.request("instances/create", {"name": name, "version": self.version, "loader": loader, "loader_version": self.rows[loader]["versions"].currentData() or ""}, self.installed, failed)

    def finished(self, _):
        self.window.install_version = None
        self.window.refresh()
        self.window.navigate("downloads")


class InstallDialog(NativeDialog):
    def __init__(self, window, version=None):
        super().__init__(window, tr("安装 ") + version if version else tr("安装版本"), width=740)
        if not version:
            QTimer.singleShot(0, lambda: (self.reject(), window.reset_install_version()))
            return
        self.preselected = version
        self.form = InstallForm(window, version, self, lambda result: (self.accept(), self.form.finished(result)))
        self.body.setAlignment(Qt.AlignmentFlag(0))
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body.addLayout(row(None, self.form.submit))
        self.body.addWidget(self.form, 1)
        self.submit = self.form.submit
        area = window.screen().availableGeometry()
        self.resize(min(740, area.width()), min(760, area.height()-30))


class MicrosoftDialog(NativeDialog):
    def __init__(self, window):
        super().__init__(window, tr("正版登录"), width=520)
        self.resize(520, 280)
        self.session_id = None
        self.description = label(tr("请在 Windows 账户窗口完成登录"), "muted", True)
        self.description.setObjectName("wam-status")
        self.body.addWidget(self.description)
        self.progress = GlassProgress()
        self.progress.setRange(0, 0)
        self.body.addWidget(self.progress)
        self.poller = QTimer(self)
        self.poller.timeout.connect(self.poll)
        self.polling = False
        self.finished.connect(self.cancel)
        self.window.request("auth/begin", {"parent_window_handle": int(self.winId())}, self.begun, self.failed)

    def begun(self, result):
        self.session_id = result["id"]
        if not self.isVisible():
            self.cancel()
            return
        self.poller.start(800)
        self.poll()

    def poll(self):
        if not self.session_id or self.polling:
            return
        self.polling = True
        def received(result):
            self.polling = False
            if not self.isVisible():
                return
            if result["status"] == "completed":
                self.session_id = None
                self.poller.stop()
                self.window.login_mode = "microsoft"
                self.window.service.config_update({"login_mode": "microsoft"})
                self.accept()
                self.window.refresh()
                self.window.navigate("home")
                self.window.toast(tr("已登录 ") + result["account"]["name"])
            elif result["status"] in ("failed", "cancelled"):
                self.failed(result.get("error") or tr("登录已取消"))
        self.window.request("auth/status?id=" + self.session_id, success=received, error=self.failed)

    def failed(self, message):
        self.polling = False
        self.poller.stop()
        self.progress.hide()
        self.description.setText(tr("登录未完成"))
        self.error(message)
        self.resize(self.width(), min(420, self.window.height()))

    def cancel(self, *_):
        self.poller.stop()
        if self.session_id:
            self.window.request("auth/cancel", {"id": self.session_id})
            self.session_id = None


class ProjectDialog(NativeDialog):
    def __init__(self, window, project, state):
        super().__init__(window, project["title"], tr("选择与你的世界兼容的版本。"), 750)
        self.project, self.state = project, state
        header = QHBoxLayout()
        header.addWidget(ProjectPreview(window.images, project.get("icon_url", ""), 96), 0, Qt.AlignTop)
        details = QVBoxLayout()
        details.addWidget(ElidedLabel(project.get("author", ""), "muted"))
        description = SummaryLabel(project.get("description", ""), 3)
        details.addWidget(description)
        if state["provider"] == "faithful":
            details.addLayout(faithful_links(window))
        if state["provider"] == "github":
            details.addWidget(label(tr("安装前检查模组元数据；前置模组需要另外安装。"), "muted", True))
        if state["provider"] in ("atlauncher", "ftb", "github") and project.get("project_url"):
            details.addWidget(Button(tr("查看平台页面"), lambda: window.open_content_url(project["project_url"])))
        details.addStretch(1)
        header.addLayout(details, 1)
        self.body.addLayout(header)
        self.container = QVBoxLayout()
        self.body.addLayout(self.container)
        self.target = combo([(i["id"], i["name"] + " · " + i["game_version"] + " / " + tr(LOADERS[i["loader"]])) for i in window.data["instances"]], state["instance"])
        self.target.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.target.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        if state["kind"] != "modpack":
            self.body.insertWidget(2, field(tr("目标版本"), self.target))
            self.target.currentIndexChanged.connect(self.load_versions)
            if state["provider"] == "github":
                self.github_allow_unknown = QCheckBox(tr("允许安装兼容性未确认的模组"))
                self.github_allow_unknown.setToolTip(tr("仅允许未知的版本要求；明确不兼容或缺少前置时仍会停止安装。"))
                self.body.insertWidget(3, self.github_allow_unknown)
        else:
            self.target.setParent(self)
            self.target.hide()
        self.generation = 0
        self.world = None
        if state["kind"] == "datapack":
            self.world = combo([], name="datapack-world")
            self.body.insertWidget(3, field(tr("目标存档"), self.world))
        self.load_versions()

    def load_versions(self):
        self.generation += 1
        generation = self.generation
        clear_layout(self.container)
        if self.world is not None:
            self.world.clear()
            def received_worlds(items):
                if self.isVisible() and generation == self.generation:
                    for item in items:
                        self.world.addItem(item["name"], item["id"])
                    if not items:
                        self.error(tr("请先在游戏中创建存档，再安装数据包"))
            if self.target.currentData():
                self.window.request("worlds?" + urllib.parse.urlencode({"instance": self.target.currentData()}), success=received_worlds, error=self.error)
        self.container.addWidget(label(tr("正在获取版本列表…"), "muted"))
        params = {"project": str(self.project["project_id"]), "instance": self.target.currentData() or "" if self.state["kind"] != "modpack" else "", "kind": self.state["kind"], "provider": self.state["provider"]}
        def received(versions):
            if generation != self.generation or not self.isVisible():
                return
            clear_layout(self.container)
            if not versions:
                self.container.addWidget(empty(tr("没有兼容版本"), tr("检查游戏版本和加载器，或更换目标实例。")))
            for version in versions[:60]:
                frame, box = card()
                box.addWidget(ElidedLabel(version["name"], "section"))
                detail = version.get("release_name", "") if self.state["provider"] == "github" else " / ".join(version.get("game_versions", [])[:7])
                box.addWidget(ElidedLabel(detail + " · " + version.get("date_published", "")[:10], "muted"))
                box.addLayout(row(label(version.get("version_type", "release"), "badge"), None,
                                  Button(tr("↓ 安装"), lambda checked=False, selected=version: self.install(selected), True)))
                self.container.addWidget(frame)
        self.window.request("content/versions?" + urllib.parse.urlencode(params), success=received, error=self.error)

    def install(self, version):
        instance = "" if self.state["kind"] == "modpack" else self.target.currentData() or ""
        if self.state["kind"] != "modpack" and not instance:
            self.error(tr("请先创建一个游戏实例。"))
            return
        payload = {"project": str(self.project["project_id"]), "version": str(version["id"]), "instance": instance,
                   "kind": self.state["kind"], "provider": self.state["provider"]}
        if self.world is not None:
            if not self.world.currentData():
                self.error(tr("请选择目标存档"))
                return
            payload["world"] = self.world.currentData()
        if self.state["provider"] == "github":
            payload["allow_unknown"] = self.github_allow_unknown.isChecked()
        self.window.mutate("content/install", payload, lambda _: self.accept(), show_tasks=True)


class InstancePanel(QWidget):
    def __init__(self, window, instance_id, category):
        super().__init__()
        self.window = window
        self.instance_id = instance_id
        self.instance = window.service.store.instance(instance_id)
        self.category = category
        self.panel = QVBoxLayout(self)
        self.panel.setContentsMargins(0, 0, 0, 0)
        self.panel.setSpacing(18)
        title = {"overview": tr("版本概览"), "settings": tr("版本设置"), "mods": tr("模组管理"), "resourcepacks": tr("资源包"), "shaders": tr("光影"), "saves": tr("存档管理"), "screenshots": tr("截图"), "logs": tr("日志")}[category]
        self.panel.addWidget(label(title, "section"))
        self.error_label = label("", "muted", True)
        self.error_label.setStyleSheet("color:" + theme_color("danger").name())
        self.error_label.hide()
        self.panel.addWidget(self.error_label)
        if category in ("mods", "resourcepacks", "shaders"):
            self.tab_content()
        else:
            getattr(self, "tab_" + category)()

    def error(self, text):
        if isValid(self):
            self.error_label.setText(tr(text))
            self.error_label.setVisible(bool(text))

    def active(self):
        return isValid(self) and self.window.page_id == "instance" and getattr(self.window, "instance_panel", None) is self

    def folder(self, target):
        self.window.mutate("folder", {"instance": self.instance_id, "target": target})

    def task(self, path, payload=None):
        self.window.mutate(path, payload or {"id": self.instance_id}, show_tasks=True)

    def tab_overview(self):
        i = self.window.service.store.instance(self.instance_id)
        self.panel.addWidget(label(i["version"], "section", True))
        self.panel.addWidget(label(tr("准备就绪") if i["ready"] else tr("需要安装或修复"), "muted"))
        actions = [Button(tr("游戏目录"), lambda: self.folder("game")), Button(tr("修复文件"), lambda: self.task("instances/repair"))]
        if not i.get("external_minecraft"):
            actions.append(Button(tr("复制版本"), lambda: self.task("instances/duplicate")))
        self.panel.addLayout(row(*actions))
        self.panel.addLayout(row(Button(tr("导出整合包"), self.export_pack), None, Button(tr("移除列表记录") if i.get("external_minecraft") else tr("删除版本"), self.delete, danger=True)))
        self.panel.addLayout(row(Button(tr('升级整合包'), lambda: self.window.open_management('upgrade', self.instance_id)),
                                 Button(tr('变更记录'), lambda: self.window.open_management('history', self.instance_id))))

    def export_pack(self):
        from .instance_ui import PackExportDialog
        self.window.open_dialog(PackExportDialog(self.window, self.instance_id))

    def delete(self):
        instance = self.window.service.store.instance(self.instance_id)
        external = bool(instance.get("external_minecraft"))
        title = tr("移除列表记录") if external else tr("删除版本")
        dialog = NativeDialog(self.window, title, width=560)
        dialog.setObjectName("delete-version-dialog")
        dialog.resize(min(560, self.window.width()), 330)
        dialog.body.addWidget(label(instance["name"], "section", True))
        message = tr("从列表移除此版本记录？原文件夹内容会保留。") if external else tr("确定删除此版本？此版本及其存档将移入回收目录，可手动恢复。")
        dialog.body.addWidget(label(message, "muted", True))
        clear_layout(dialog.footer)
        dialog.footer.addStretch()
        dialog.footer.addWidget(Button(tr("取消"), dialog.reject))
        def confirmed():
            submit.setEnabled(False)
            self.task("instances/delete")
            dialog.accept()
        submit = Button(title, confirmed, danger=True)
        submit.setObjectName("confirm-delete-version")
        dialog.footer.addWidget(submit)
        self.window.open_dialog(dialog)

    def tab_content(self):
        self.content_kind = {"mods": "mod", "resourcepacks": "resourcepack", "shaders": "shader"}[self.category]
        self.local_query = text_input(placeholder=tr("搜索文件"), name="local-content-search")
        self.local_query.textChanged.connect(self.filter_local)
        self.panel.addWidget(self.local_query)
        self.mod_status = "all"
        if self.content_kind == "mod":
            self.mod_status_switch = NavigationSwitch((("all", tr("全部")), ("enabled", tr("已启用")), ("disabled", tr("已禁用"))))
            self.mod_status_switch.setObjectName("mod-status-switch")
            self.mod_status_switch.setMaximumWidth(360)
            self.mod_status_switch.selected.connect(self.set_mod_status)
            self.mod_status_switch.select("all", False)
            self.panel.addLayout(row(self.mod_status_switch, None))
            self.panel.addLayout(row(Button(tr('批量管理'), lambda: self.window.open_management('bulk', self.instance_id)),
                                     Button(tr('依赖关系与修复'), lambda: self.window.open_management('dependencies', self.instance_id))))
        self.panel.addLayout(row(Button(tr("导入"), self.import_local), Button(tr("检查更新"), self.check_updates), Button(tr("下载") + tr(KINDS[self.content_kind]), lambda: self.window.explore(self.content_kind, self.instance_id), True)))
        self.local_items = []
        self.local_cards = None
        self.local_layout = QVBoxLayout()
        self.panel.addLayout(self.local_layout)
        self.local_layout.addWidget(label(tr("正在读取…"), "muted"))
        QTimer.singleShot(0, self.refresh_content)

    def refresh_content(self):
        if self.category in ("mods", "resourcepacks", "shaders"):
            def received(items):
                if not self.active():
                    return
                self.local_items = items
                self.filter_local()
            self.window.request("content/local?instance=" + self.instance_id, success=received, error=self.error)
        elif self.category == "saves":
            self.load_backups()

    def set_mod_status(self, status):
        self.mod_status = status
        self.mod_status_switch.select(status, self.window.animations)
        self.filter_local()

    def filter_local(self):
        query = self.local_query.text().strip().lower()
        items = [item for item in self.local_items if item["kind"] == self.content_kind and
                 (self.mod_status == "all" or bool(item.get("enabled", True)) == (self.mod_status == "enabled")) and query in
                 (item["filename"] + " " + item.get("mod_name", "") + " " + item.get("description", "")).lower()]
        if self.content_kind == "mod":
            if self.local_cards is None:
                clear_layout(self.local_layout)
                self.local_cards = VirtualCardList(self.window.page, self.create_local_card)
                self.local_empty = empty("", "")
                self.local_layout.addWidget(self.local_cards)
                self.local_layout.addWidget(self.local_empty)
            self.local_empty.findChild(QLabel, "section").setText(
                tr("没有匹配的文件") if query or self.mod_status != "all" else tr("暂无") + tr(KINDS[self.content_kind]))
            self.local_empty.setVisible(not items)
            self.local_cards.setVisible(bool(items))
            self.local_cards.set_items(items)
            return
        clear_layout(self.local_layout)
        if not items:
            self.local_layout.addWidget(empty(tr("没有匹配的文件") if query or self.mod_status != "all" else tr("暂无") + tr(KINDS[self.content_kind]), ""))
        for item in items:
            self.local_layout.addWidget(self.create_local_card(item))
        self.local_layout.addStretch()

    def create_local_card(self, item):
        frame, box = card()
        frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        if self.content_kind == "mod":
            line = QHBoxLayout()
            preview = ProjectPreview(self.window.images, "", 72)
            preview.setObjectName("local-mod-preview")
            if item.get("image_path"):
                self.window.images.load_local(item["image_path"], preview)
            elif item.get("icon_url"):
                self.window.images.load(item["icon_url"], preview)
            elif item.get("provider") in ("modrinth", "curseforge") and item.get("project_id"):
                provider, project = item["provider"], str(item["project_id"])
                self.window.images.load_project(provider, project,
                    lambda p=provider, key=project: self.window.service.content.previews.project_image(p, key), preview)
            line.addWidget(preview, 0, Qt.AlignTop)
            text = QVBoxLayout()
            text.addWidget(ElidedLabel(item.get("mod_name") or item["filename"], "section"))
            if item.get("mod_name"):
                text.addWidget(ElidedLabel(item["filename"], "muted"))
            if item.get("description"):
                text.addWidget(SummaryLabel(item["description"], 2))
            text.addStretch()
            line.addLayout(text, 1)
            box.addLayout(line)
        else:
            box.addWidget(ElidedLabel(item["filename"], "section"))
        actions = [label(human_bytes(item["size"]), "muted"), None]
        if self.content_kind == "mod":
            actions.append(Button(tr("查看网站"), lambda checked=False, value=item: self.show_mod_websites(value)))
        actions.extend((Button(tr("禁用") if item["enabled"] else tr("启用"), lambda checked=False, value=item: self.toggle(value)),
                        Button(tr("移除"), lambda checked=False, value=item: self.toggle(value, True), danger=True)))
        box.addLayout(row(*actions))
        return frame

    def show_mod_websites(self, item):
        from .mod_sites import mod_websites
        dialog = NativeDialog(self.window, tr("模组网站"), width=680)
        dialog.setObjectName("mod-websites-dialog")
        dialog.resize(min(680, self.window.width()), min(470, self.window.height()))
        dialog.body.addWidget(label(item.get("mod_name") or item["filename"], "section", True))
        for link in mod_websites(item):
            button = Button(tr(link["title"]), lambda checked=False, url=link["url"]: self.window.open_mod_website(url))
            button.setToolTip(link["url"])
            address = ElidedLabel(link["url"], "muted")
            address.setToolTip(link["url"])
            dialog.body.addLayout(row(button, address))
        self.window.open_dialog(dialog)

    def toggle(self, item, remove=False):
        if remove and QMessageBox.question(self, tr("移除内容"), tr("将此文件移到实例回收目录？")) != QMessageBox.Yes:
            return
        self.window.mutate("content/toggle", {"instance": self.instance_id, "kind": item["kind"], "filename": item["filename"], "remove": remove})

    def import_local(self):
        kind = self.content_kind
        path, _ = QFileDialog.getOpenFileName(self, tr("导入 ") + tr(KINDS[kind]), "", tr("模组 (*.jar *.litemod)") if kind == "mod" else tr("资源文件 (*.zip)"))
        if not path:
            return
        def copy(task):
            self.window.service.content._check_mod_instance(self.instance_id, kind)
            destination = safe_path(self.window.service.content._folder(self.instance_id, kind), Path(path).name)
            if destination.exists():
                raise LauncherError(tr("目标实例中已有同名文件"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, str(destination))
        self.window.service.tasks.submit(tr("导入本地内容"), lambda task: self.window.service.management.run(self.instance_id, copy, task))
        self.window.refresh()

    def check_updates(self):
        if self.content_kind == 'mod':
            from .management_ui import BulkModsDialog
            self.window.open_dialog(BulkModsDialog(self.window, self.instance_id, update=True))
            return
        self.error(tr("正在检查更新…"))
        def received(items):
            if not self.active():
                return
            items = [item for item in items if item["kind"] == self.content_kind]
            self.error(tr("发现 {} 个更新").format(len(items)) if items else tr("暂无更新"))
            if not items:
                return
            self.local_cards = None
            clear_layout(self.local_layout)
            for item in items:
                frame, box = card()
                box.addWidget(label(item["filename"], wrap=True))
                box.addWidget(label(tr("可更新至 ") + item["new_name"], "muted", True))
                box.addWidget(Button(tr("更新"), lambda checked=False, value=item: self.window.mutate("content/install", {"project": value["project_id"], "version": value["new_version"], "instance": self.instance_id, "kind": value["kind"], "provider": value["provider"]}), True))
                self.local_layout.addWidget(frame)
        self.window.request("content/updates?instance=" + self.instance_id, success=received, error=self.error)

    def tab_logs(self):
        from .management_ui import LogPanel
        self.panel.addWidget(LogPanel(self))
        self.panel.addWidget(Button(tr('分析崩溃'), lambda: self.window.run_diagnostic(self.instance_id, 'crash')))
    def tab_saves(self):
        self.panel.addLayout(row(Button(tr("存档目录"), lambda: self.folder("saves")), Button(tr("备份目录"), lambda: self.folder("backups")), Button(tr("备份世界"), lambda: self.window.mutate("backup", {"instance": self.instance_id}), True)))
        try:
            directory = self.window.service.store.game_dir(self.instance_id) / "saves"
        except LauncherError as exc:
            self.panel.addWidget(label(str(exc), "muted", True))
            return
        worlds = sorted([p for p in directory.iterdir() if p.is_dir() and not p.is_symlink()], key=lambda p: p.stat().st_mtime, reverse=True) if directory.is_dir() else []
        for world in worlds:
            frame, box = card()
            box.addWidget(ElidedLabel(world.name, "section"))
            box.addLayout(row(label(time.strftime("%Y-%m-%d %H:%M", time.localtime(world.stat().st_mtime)), "muted"), None, Button(tr("打开"), lambda checked=False, name=world.name: self.window.mutate("folder", {"instance": self.instance_id, "target": "world", "world": name}))))
            self.panel.addWidget(frame)
        if not worlds:
            self.panel.addWidget(empty(tr("暂无存档"), ""))
        self.panel.addWidget(label(tr("备份"), "section"))
        self.backups = QVBoxLayout()
        self.panel.addLayout(self.backups)
        QTimer.singleShot(0, self.load_backups)

    def load_backups(self):
        def received(items):
            if not self.active() or self.category != "saves":
                return
            clear_layout(self.backups)
            for item in items:
                self.backups.addWidget(label(item["name"] + " · " + human_bytes(item["size"]), "muted"))
            if not items:
                self.backups.addWidget(label(tr("暂无备份"), "muted"))
        self.window.request("backups?instance=" + self.instance_id, success=received, error=self.error)

    def tab_screenshots(self):
        from .instance_ui import ScreenshotsPanel
        self.panel.addWidget(ScreenshotsPanel(self.window, self.instance_id))

    def tab_settings(self):
        self.panel.addWidget(Button(tr("查看截图"), lambda: self.window.set_instance_category("screenshots")))
        self.panel.addWidget(Button(tr('Java 管理中心'), lambda: self.window.open_management('java', self.instance_id)))
        current = self.window.service.store.instance(self.instance_id)
        current.update(self.window.instance_drafts.get(self.instance_id, {}))
        self.instance_fields = {}
        for key, text in (("name", tr("实例名称")), ("window_title", tr("游戏窗口标题")), ("java_path", tr("Java 路径（留空继承全局）")), ("jvm_args", tr("额外 JVM 参数")), ("game_args", tr("额外游戏参数"))):
            widget = text_input(current.get(key, ""))
            self.instance_fields[key] = widget
            self.panel.addWidget(field(text, widget))
            if key == "window_title":
                widget.setMaxLength(200)
                widget.setPlaceholderText(tr("留空使用默认标题；支持 {name}、{version}、{player}"))
        memory = QSpinBox()
        memory.setRange(512, 65536)
        memory.setValue(current.get("memory") or 4096)
        mode = combo([("inherit", tr("继承全局")), ("auto", tr("自动分配")), ("manual", tr("手动分配"))], current.get("memory_mode") or ("manual" if current.get("memory", 0) else "inherit"))
        self.instance_fields["memory_mode"] = mode
        self.instance_fields["memory"] = memory
        mode.currentIndexChanged.connect(lambda: memory.setEnabled(mode.currentData() == "manual"))
        memory.setEnabled(mode.currentData() == "manual")
        self.panel.addWidget(field(tr("内存分配"), mode))
        self.panel.addWidget(field(tr("手动内存 / MB"), memory))
        from .instance_ui import add_launch_options
        add_launch_options(self,current)
        for widget in self.instance_fields.values():
            if isinstance(widget,QLineEdit):
                widget.textChanged.connect(lambda _=None:self.changed(350))
                widget.editingFinished.connect(self.window.flush_instance_settings)
            elif isinstance(widget,QSpinBox):
                widget.setKeyboardTracking(False)
                widget.valueChanged.connect(lambda _=None:self.changed())
            elif isinstance(widget,QCheckBox):
                widget.toggled.connect(lambda _=None:self.changed())
            else:
                widget.currentIndexChanged.connect(lambda _=None:self.changed())

    def settings_values(self):
        values = {key: value.currentData() if isinstance(value, QComboBox) else value.value() if isinstance(value, QSpinBox) else value.isChecked() if isinstance(value,QCheckBox) else value.text() for key, value in self.instance_fields.items()}
        if values["memory_mode"] != "manual":
            values["memory"] = 0
        return values

    def changed(self,delay=0):
        self.window.queue_instance_settings(self.instance_id,self.settings_values(),delay)


def run_desktop(data_root, *, minecraft_root=None, quit_after=0, screenshot=""):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName(tr("星月启动器"))
    app.setOrganizationName("StarMoon")
    app.setStyle("Fusion")
    palette = QPalette()
    for role, value in ((QPalette.Window, "#e7f2ff"), (QPalette.Base, "#f4f9ff"), (QPalette.AlternateBase, "#dfedff"),
                        (QPalette.WindowText, "#294768"), (QPalette.Text, "#294768"), (QPalette.Button, "#edf6ff"),
                        (QPalette.ButtonText, "#386595"), (QPalette.Highlight, "#badbff"), (QPalette.HighlightedText, "#294768")):
        palette.setColor(role, QColor(value))
    app.setPalette(palette)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setStyleSheet(STYLE)
    app.setWindowIcon(application_icon())
    router = WheelRouter(app)
    app.installEventFilter(router)
    app.setProperty("wheelRouter", router)
    # Keep a Python reference in addition to QObject ownership.
    app.wheel_router = router
    window = LauncherWindow(data_root, minecraft_root=minecraft_root)
    router.enabled = window.animations
    window.show()
    if screenshot:
        def capture():
            path = Path(screenshot)
            path.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(path))
        QTimer.singleShot(1000, capture)
    if quit_after:
        QTimer.singleShot(int(quit_after*1000), app.quit)
    elif getattr(sys, "frozen", False):
        QTimer.singleShot(2000, window.startup_updates)
    return app.exec()
