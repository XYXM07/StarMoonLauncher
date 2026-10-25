"""Explicit, task-bound consent and version selection for failed pack mod files."""
from PySide6.QtWidgets import QButtonGroup, QRadioButton

from .core import LauncherError
from .desktop import NativeDialog, row, clear_layout
from .i18n import tr
from .widgets import Button, card, label


class RecoveryDialog(NativeDialog):
    def __init__(self, window, task):
        pending = task["interaction"]
        selecting = pending["kind"] == "ai_recovery_select"
        super().__init__(window, tr("选择补下载版本") if selecting else tr("整合包文件下载失败"), width=700)
        self.setObjectName("ai-recovery-select" if selecting else "ai-recovery-confirm")
        self.resize(min(700, window.width()), min(560 if selecting else 390, window.height()))
        self.task_id, self.request_id = task["id"], pending["id"]
        self.resolved = False
        self.selected = None
        details = pending["details"]
        self.body.setSpacing(12)
        self.body.addWidget(label(details.get("filename", ""), "section", True))
        versions = " · ".join(str(details.get(key, "")) for key in ("game_version", "loader", "loader_version") if details.get(key))
        self.body.addWidget(label(versions, "muted", True))
        clear_layout(self.footer)
        self.footer.addStretch()
        self.footer.addWidget(Button(tr("取消安装"), self.reject))
        if not selecting:
            self.body.addWidget(label(tr("原下载地址均不可用。是否使用 AI 查找替代下载？找到后将由你选择版本。"), "muted", True))
            self.body.addWidget(label(tr("优先检索 MC百科，使用设置中的模型 API。"), "muted", True))
            self.submit = Button(tr("使用 AI 查找版本"), lambda: self.respond("search"), True)
        else:
            self.body.addWidget(label(tr("请选择要补下载的文件；安装前仍会核对原整合包的哈希，不能替换为其他版本。"), "muted", True))
            if details.get("previous_failed"):
                self.body.addWidget(label(tr("上次选择下载失败或校验不通过，请选择其他候选。"), "muted", True))
                if details.get("last_error"):
                    self.body.addWidget(label(tr(details["last_error"]), "muted", True))
            self.group = QButtonGroup(self)
            for option in pending["options"]:
                frame, box = card()
                frame.setObjectName("ai-recovery-version")
                radio = QRadioButton(option.get("version") or tr("版本信息未知"))
                radio.setObjectName("ai-recovery-choice-" + option["id"])
                self.group.addButton(radio)
                radio.toggled.connect(lambda checked, value=option["id"]: self.choose(value) if checked else None)
                box.addWidget(radio)
                box.addWidget(label(option.get("filename", ""), "", True))
                if option.get("name") and option["name"] not in (option.get("version"), option.get("filename")):
                    box.addWidget(label(option["name"], "muted", True))
                metadata = [option.get("provider", ""), ", ".join(option.get("game_versions", [])),
                            ", ".join(option.get("loaders", [])), option.get("published", "")[:10]]
                box.addWidget(label(" · ".join(value for value in metadata if value), "muted", True))
                source = option.get("source") or option.get("project_url")
                if source:
                    box.addLayout(row(label(tr("MC百科相关链接") if option.get("source") else tr("项目来源"), "muted"),
                                      None, Button(tr("查看来源"), lambda checked=False, url=source: window.open_content_url(url))))
                self.body.addWidget(frame)
            self.submit = Button(tr("下载所选版本"), lambda: self.respond(self.selected), True)
            self.submit.setEnabled(False)
        self.submit.setObjectName("ai-recovery-submit")
        self.footer.addWidget(self.submit)

    def choose(self, choice):
        self.selected = choice
        self.submit.setEnabled(True)

    def respond(self, choice):
        if self.resolved or not choice:
            return
        try:
            self.window.service.route("POST", "/api/tasks/respond", {},
                {"id": self.task_id, "request": self.request_id, "choice": choice})
        except LauncherError as exc:
            self.error(str(exc))
            return
        self.resolved = True
        self.accept()

    def reject(self):
        if not self.resolved:
            try:
                self.window.service.route("POST", "/api/tasks/respond", {},
                    {"id": self.task_id, "request": self.request_id, "choice": "cancel"})
            except LauncherError:
                pass
        self.resolved = True
        super().reject()

    def expire(self):
        """A finished/cancelled/replaced request must not cancel another request."""
        self.resolved = True
        super().reject()
