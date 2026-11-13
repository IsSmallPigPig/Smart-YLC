"""批量上传页：登录 → 校验 → 分片上传 → 入库；上传后需网页端人工「审核」。"""

from __future__ import annotations

import os

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTableWidgetItem
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    InfoBar,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    TableWidget,
    ToolButton,
    FluentIcon as FIF,
)

from app.ui.page import FluentPage
from app.constants import CONFIRM_PHRASE, LIMITS
from app.domain.chunk_protocol import check_site_limits

MAX_BATCH = LIMITS["MAX_UPLOAD_PER_BATCH"]


class UploadWorker(QThread):
    row_ready = Signal(int, str, bool, str)  # index, name, ok, reason
    all_done = Signal(int, int)

    def __init__(self, client, files: list[str]):
        super().__init__()
        self.client = client
        self.files = files
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        ok = 0
        for i, path in enumerate(self.files):
            if self._stop:
                break
            name = os.path.basename(path)
            result = self.client.upload_file(path)
            self.row_ready.emit(i, name, result.ok, result.message)
            ok += 1 if result.ok else 0
        self.all_done.emit(ok, len(self.files))


class UploadPage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__('page-upload', parent)
        self.window_ref = window
        self.worker = None
        lay = self.vBoxLayout

        row = QHBoxLayout()
        self.dir_edit = LineEdit()
        self.dir_edit.setPlaceholderText("选择待上传 PDF 所在文件夹（单批 ≤50 份、单文件 ≤10MB）")
        self.dir_btn = ToolButton(FIF.FOLDER)
        self.dir_btn.clicked.connect(self._pick_dir)
        row.addWidget(self.dir_edit, 1)
        row.addWidget(self.dir_btn)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self.confirm_edit = LineEdit()
        self.confirm_edit.setPlaceholderText(f"真实上传前请在此手输「{CONFIRM_PHRASE}」确认")
        self.start_btn = PrimaryPushButton(FIF.CLOUD, "开始上传")
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = PushButton(FIF.PAUSE, "停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(lambda: self.worker and self.worker.stop())
        row2.addWidget(self.confirm_edit, 1)
        row2.addWidget(self.start_btn)
        row2.addWidget(self.stop_btn)
        lay.addLayout(row2)

        self.stat = BodyLabel("账号在「系统配置」中设置；密码错一次会触发验证码，尽量先在网页端登录一次再上传")
        lay.addWidget(self.stat)

        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["文件", "结果", "说明"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.add_table(self.table, 1)

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d:
            self.dir_edit.setText(d)

    def _client(self):
        return self.window_ref.settings_page.client()

    def _start(self):
        d = self.dir_edit.text().strip()
        if not d or not os.path.isdir(d):
            InfoBar.warning("路径无效", "请先选择一个存在的文件夹", duration=2500, parent=self)
            return
        if self.confirm_edit.text().strip() != CONFIRM_PHRASE:
            InfoBar.warning("请输入确认短语", f"手输「{CONFIRM_PHRASE}」才会开始真实上传", duration=3000, parent=self)
            return
        files = sorted(
            os.path.join(d, f) for f in os.listdir(d)
            if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(d, f))
        )
        if not files:
            InfoBar.warning("没有 PDF", "所选文件夹里没有 .pdf 文件", duration=2500, parent=self)
            return
        over = [{"name": os.path.basename(p), "size": os.path.getsize(p)} for p in files]
        limits = check_site_limits(over)
        if limits:
            for i, r in enumerate(limits[:5]):
                self.stat.setText(r)
            InfoBar.warning("超出站点限制", "；".join(limits[:2]), duration=5000, parent=self)
            return
        client = self._client()
        if client is None:
            InfoBar.error("未配置账号", "请先到「系统配置」填写团支部账号", duration=3000, parent=self)
            return
        verdict = client.login()
        if not verdict.ok:
            kind = (verdict.data or {}).get("kind") if isinstance(verdict.data, dict) else ""
            if kind == "captcha":
                InfoBar.warning("需要验证码", "密码失败次数过多，请先在网页端登录一次再回来", duration=5000, parent=self)
            else:
                InfoBar.error("登录失败", verdict.message, duration=5000, parent=self)
            return

        self.table.setRowCount(len(files))
        for i, p in enumerate(files):
            self.table.setItem(i, 0, QTableWidgetItem(os.path.basename(p)))
            self.table.setItem(i, 1, QTableWidgetItem("…"))
            self.table.setItem(i, 2, QTableWidgetItem("-"))
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.worker = UploadWorker(client, files)
        self.worker.row_ready.connect(self._on_row)
        self.worker.all_done.connect(self._on_done)
        self.worker.start()

    def _on_row(self, i: int, name: str, ok: bool, reason: str):
        green, red = QColor("#2e7d32"), QColor("#c62828")
        self.table.setItem(i, 1, QTableWidgetItem("成功" if ok else "失败"))
        self.table.item(i, 1).setForeground(green if ok else red)
        self.table.setItem(i, 2, QTableWidgetItem(reason))
        self.stat.setText(f"已处理 {i + 1}/{self.table.rowCount()}：{name}")

    def _on_done(self, ok: int, total: int):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        InfoBar.success(
            self, "上传批次结束",
            f"成功 {ok}/{total}。请到智慧团建网页端逐个点击「审核」完成认证（本应用不代点审核）",
            duration=6000, parent=self,
        )
