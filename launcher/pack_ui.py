"""Local archive drops and explicit failed-install cleanup reports."""
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QWidget

from .desktop import NativeDialog
from .i18n import tr
from .widgets import Button, label


class PackDropFilter(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    @staticmethod
    def paths(mime):
        if not mime.hasUrls():
            return []
        urls = mime.urls()
        if not urls or any(not url.isLocalFile() for url in urls):
            return []
        paths = [url.toLocalFile() for url in urls]
        return paths if all(Path(path).suffix.lower() in (".mrpack", ".zip") for path in paths) else []

    def eventFilter(self, watched, event):
        if (event.type() not in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop) or
                not isinstance(watched, QWidget) or watched.window() is not self.window):
            return False
        paths = self.paths(event.mimeData())
        if not paths or getattr(self.window, "_installing_update", False):
            return False
        if not event.possibleActions() & Qt.CopyAction:
            event.ignore()
            return True
        event.setDropAction(Qt.CopyAction)
        event.accept()
        if event.type() == QEvent.Drop:
            self.window.import_pack_paths(paths)
        return True


class PackFailureDialog(NativeDialog):
    def __init__(self, window, task):
        failed = task["status"] == "failed"
        super().__init__(window, tr("整合包安装失败") if failed else tr("整合包文件清理未完成"), width=700)
        self.setObjectName("pack-install-failure")
        self.resize(min(700, window.width()), min(470, window.height()))
        self.body.addWidget(label(task["title"], "section", True))
        self.body.addWidget(label(tr(task.get("error") or task.get("message", "")), "muted", True))
        report = task.get("pack_install") or {}
        if report.get("cleaned"):
            self.body.addWidget(label(tr("本次安装的残留文件已清理，已有游戏和存档保持不变。"), "muted", True))
        else:
            self.body.addWidget(label(tr("部分残留文件未能删除，请关闭占用程序后手动清理："), "muted", True))
            self.body.addWidget(label("\n".join(report.get("cleanup_errors", [])), "muted", True))
        self.footer.addWidget(Button(tr("查看任务"), lambda: (self.accept(), window.navigate("downloads"))))
