"""Native, localized launcher update review and download dialog."""
import os
import sys

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QPlainTextEdit

from .desktop import NativeDialog, human_bytes, row
from .i18n import tr
from .progress_ui import GlassProgress
from .updater import RELEASES_URL
from .widgets import Button, label


class UpdateDialog(NativeDialog):
    def __init__(self, window, info=None):
        super().__init__(window, tr("启动器更新"), width=650)
        self.setObjectName("launcher-update-dialog")
        self.resize(min(650, window.width()), min(530, window.height()))
        self.body.setSpacing(12)
        self.manager = window.service.updater
        self.info, self.task_id = None, None
        self.closed = self.checking = self.preparing = False
        self.body.addWidget(label(tr("当前版本：{}").format(self.manager.current_version), "muted"))
        self.status = label(tr("正在检查启动器更新…"), "muted", True)
        self.status.setObjectName("launcher-update-status")
        self.body.addWidget(self.status)
        self.progress = GlassProgress()
        self.progress.setRange(0, 0)
        self.body.addWidget(self.progress)
        self.notes = QPlainTextEdit()
        self.notes.setObjectName("launcher-release-notes")
        self.notes.setReadOnly(True)
        self.notes.setMinimumHeight(130)
        self.notes.hide()
        self.body.addWidget(self.notes)
        self.check_button = Button(tr("重新检查"), self.check)
        self.cancel_download = Button(tr("取消下载"), self.cancel)
        self.cancel_download.hide()
        self.body.addLayout(row(self.check_button, None, self.cancel_download))
        self.open_release = Button(tr("打开发布页"), self.open_page)
        self.footer.insertWidget(1, self.open_release)
        self.action = Button(tr("下载更新"), self.download, True)
        self.action.setObjectName("launcher-update-action")
        self.action.setEnabled(False)
        self.footer.addWidget(self.action)
        self.poller = QTimer(self)
        self.poller.setInterval(200)
        self.poller.timeout.connect(self.poll)
        self.finished.connect(self.shutdown)
        if info:
            self.show_info(info)
        elif self.manager.ready and self.manager.latest:
            self.show_info(self.manager.latest)
        else:
            self.check()

    def check(self):
        if self.checking or self.task_id or self.preparing or self.closed:
            return
        self.checking = True
        self.error("")
        self.check_button.setEnabled(False)
        self.action.setEnabled(False)
        self.status.setText(tr("正在检查启动器更新…"))
        self.progress.setRange(0, 0)
        self.window.async_call(self.manager.check, self.show_info, self.failed)

    def show_info(self, info):
        if self.closed:
            return
        self.checking = False
        self.info = info
        self.check_button.setEnabled(True)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.notes.setPlainText(info.get("notes") or tr("此版本未提供更新说明"))
        self.notes.setVisible(bool(info.get("available")))
        if not info.get("available"):
            self.status.setText(tr("当前版本不低于最新正式版"))
            self.action.setEnabled(False)
            return
        self.status.setText(tr("发现新版本 {} · {}").format(info["version"], human_bytes(info["asset"]["size"])))
        self.action.setText(tr("下载更新"))
        self.action.setEnabled(True)
        self.poller.start()
        self.refresh_ready()

    def failed(self, message):
        if self.closed:
            return
        self.checking = self.preparing = False
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.check_button.setEnabled(True)
        self.cancel_download.hide()
        self.status.setText(tr("更新未完成，可重试或打开发布页"))
        self.error(message)
        self.action.setEnabled(bool(self.info and self.info.get("available")))

    def refresh_ready(self):
        ready = self.manager.ready
        if not ready or not self.info or ready["version"] != self.info["version"] or self.preparing:
            return False
        self.action.setText(tr("安装并重启"))
        self.progress.setValue(1000)
        if not getattr(sys, "frozen", False) or os.name != "nt":
            self.action.setEnabled(False)
            self.status.setText(tr("更新已下载；源码运行请手动使用新程序"))
        else:
            reason = self.window.update_block_reason()
            self.action.setEnabled(not reason)
            self.status.setText(tr(reason) if reason else tr("更新已校验，安装后自动重新打开启动器"))
        return True

    def download(self):
        if self.preparing or self.task_id or self.checking or not self.info or not self.info.get("available"):
            return
        if self.manager.ready and self.manager.ready["version"] == self.info["version"]:
            self.preparing = True
            self.action.setEnabled(False)
            self.check_button.setEnabled(False)
            self.status.setText(tr("正在准备安装更新…"))
            self.window.install_launcher_update(self.failed)
            return
        self.error("")
        self.action.setEnabled(False)
        self.check_button.setEnabled(False)
        self.cancel_download.show()
        result = self.window.service.tasks.submit("下载启动器更新", self.manager.download)
        self.task_id = result["task_id"]
        self.poller.start()
        self.poll()

    def poll(self):
        if self.closed:
            return
        if not self.task_id:
            if not self.checking:
                self.refresh_ready()
            return
        task = self.window.service.tasks.tasks.get(self.task_id)
        if not task:
            self.task_id = None
            self.failed(tr("更新下载记录不可用，请重试"))
            return
        state = task.public()
        download = state.get("download")
        if download and download.get("active"):
            self.status.setText(tr("下载更新 · {} / {} · {}/s").format(
                human_bytes(download["bytes"]), human_bytes(download["total"] or 0), human_bytes(state["speed"])))
        else:
            self.status.setText(tr(state["message"]))
        self.progress.setValue(round(state["progress"] * 10))
        if state["status"] in ("completed", "failed", "cancelled"):
            self.task_id = None
            self.cancel_download.hide()
            self.check_button.setEnabled(True)
            if state["status"] == "completed":
                self.refresh_ready()
            else:
                self.action.setText(tr("下载更新"))
                self.failed(state.get("error") or tr("更新下载已取消"))

    def cancel(self):
        if self.task_id:
            self.window.service.tasks.cancel_task(self.task_id)

    def open_page(self):
        QDesktopServices.openUrl(QUrl((self.info or {}).get("release_url", RELEASES_URL)))

    def shutdown(self, *_):
        self.closed = True
        self.poller.stop()
