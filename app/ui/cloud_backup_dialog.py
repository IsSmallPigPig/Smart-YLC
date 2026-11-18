"""云端备份弹层：填服务端地址，用 6 位提取码上传或恢复整份设置。

弹层不自己写配置——恢复出来的 dict 交回设置页，走与「从文件恢复」同一条
确认与重载路径，避免两条恢复逻辑各自演化。
"""

from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal as Signal
from PyQt5.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import BodyLabel, InfoBar, LineEdit, PushButton

from app.services import cloud_backup
from app.ui.dialogs import ConfigDialog


class _Worker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # noqa: BLE001 - 网络/解密错误都直接告诉用户
            self.failed.emit(str(exc))


class CloudBackupDialog(ConfigDialog):
    def __init__(self, parent, get_config, on_restore, server: str = "", note: str = ""):
        super().__init__("云端备份", parent, note=note, width=480)
        self._get_config = get_config
        self._on_restore = on_restore
        self._worker: _Worker | None = None

        self.server_edit = LineEdit(self.widget)
        self.server_edit.setPlaceholderText(cloud_backup.DEFAULT_SERVER)
        self.server_edit.setText(server)
        self.add(self.server_edit)

        self.code_edit = LineEdit(self.widget)
        self.code_edit.setPlaceholderText("6 位提取码，如 K7M2QX")
        self.code_edit.setMaxLength(6)
        self.add(self.code_edit)

        self.status = BodyLabel("", self.widget)
        self.add(self.status)

        row = QWidget(self.widget)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.upload_btn = PushButton("上传当前设置", row)
        self.restore_btn = PushButton("从提取码恢复", row)
        lay.addWidget(self.upload_btn)
        lay.addWidget(self.restore_btn)
        self.add(row)

        self.upload_btn.clicked.connect(self._upload)
        self.restore_btn.clicked.connect(self._restore)
        self.yesButton.hide()
        self.cancelButton.setText("关闭")
        self.cancelButton.show()

    # ---- 动作 -------------------------------------------------------------

    def _code(self) -> str:
        raw = self.code_edit.text().strip()
        if not raw:
            code = cloud_backup.generate_code()
            self.code_edit.setText(code)
            return code
        return cloud_backup.normalize_code(raw)

    def _busy(self, running: bool, text: str = "") -> None:
        self.upload_btn.setEnabled(not running)
        self.restore_btn.setEnabled(not running)
        if text:
            self.status.setText(text)

    def _upload(self):
        try:
            code = self._code()
            server = cloud_backup.normalize_server(self.server_edit.text())
        except ValueError as exc:
            InfoBar.warning("还不能上传", str(exc), duration=4000, parent=self)
            return
        cfg = self._get_config()
        self._busy(True, f"正在上传，提取码 {code}")

        def job():
            envelope = cloud_backup.encrypt_config(cfg, code)
            cloud_backup.upload(server, code, envelope)
            return code

        self._run(job, lambda code: self._uploaded(code))

    def _uploaded(self, code: str):
        self._busy(False, f"已上传。提取码 {code}，在另一台机器上输入它即可恢复")
        InfoBar.success("已上传到云端", f"提取码 {code}，请记下它", duration=8000, parent=self)

    def _restore(self):
        try:
            code = self._code()
            server = cloud_backup.normalize_server(self.server_edit.text())
        except ValueError as exc:
            InfoBar.warning("还不能恢复", str(exc), duration=4000, parent=self)
            return
        self._busy(True, "正在下载并解密")

        def job():
            envelope = cloud_backup.download(server, code)
            return cloud_backup.decrypt_config(envelope, code)

        self._run(job, self._restored)

    def _restored(self, cfg: dict):
        self._busy(False, "已解密，等待确认")
        self.accept()
        self._on_restore(cfg)

    def _run(self, fn, on_done):
        self._worker = _Worker(fn, self)
        self._worker.done.connect(lambda value: (self._busy(False), on_done(value)))
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _failed(self, message: str):
        self._busy(False, message)
        InfoBar.error("云端备份失败", message, duration=6000, parent=self)
