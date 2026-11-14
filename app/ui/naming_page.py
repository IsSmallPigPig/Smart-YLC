"""文档检查页：扫描目录 → 命名判定 + PDF 总页数核对 → 表格展示 → 一键改名。

页数核对：预期页数在「系统配置」设置，留空则只查命名不查页数。
"""

from __future__ import annotations

import os

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTableWidgetItem
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout

from qfluentwidgets import (
    BodyLabel,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    TableWidget,
    ToolButton,
    FluentIcon as FIF,
    InfoBar,
)

from app.ui.page import FluentPage, fit_table_columns, note_label
from app.constants import NAME_SUFFIX, PDF_EXT_RE
from app.domain.naming import analyze_file_name, apply_page_verdict, build_naming_report, page_verdict

SEV_LABEL = {"ok": "通过", "warn": "提醒", "issue": "问题"}
SEV_COLOR = {"ok": "#2e7d32", "warn": "#ef6c00", "issue": "#c62828"}


def pdf_page_count(path: str) -> int | None:
    """读取 PDF 总页数；损坏或非有效 PDF 返回 None（判为问题，不静默放过）。"""
    try:
        from pypdf import PdfReader

        return len(PdfReader(path).pages)
    except Exception:
        return None


class ScanWorker(QThread):
    done = Signal(object)

    def __init__(self, scan_dir: str, roster, allow_alt: bool, expected_pages: int):
        super().__init__()
        self.scan_dir = scan_dir
        self.roster = roster
        self.allow_alt = allow_alt
        self.expected_pages = expected_pages

    def run(self):
        names = sorted(
            f for f in os.listdir(self.scan_dir)
            if os.path.isfile(os.path.join(self.scan_dir, f)) and not f.startswith(".")
        )
        issues = []
        for n in names:
            a = analyze_file_name(n, self.allow_alt)
            if self.expected_pages > 0 and PDF_EXT_RE.search(n):
                v = page_verdict(pdf_page_count(os.path.join(self.scan_dir, n)), self.expected_pages)
                apply_page_verdict(a, v)
            issues.append(a)
        self.done.emit(build_naming_report(self.scan_dir, issues, self.roster))


class NamingPage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__('page-naming', parent)
        self.window_ref = window
        self.report = None
        lay = self.vBoxLayout

        # 目录选择行
        row = QHBoxLayout()
        self.dir_edit = LineEdit()
        self.dir_edit.setPlaceholderText("选择待检查的文件夹")
        self.dir_btn = ToolButton(FIF.FOLDER)
        self.dir_btn.clicked.connect(self._pick_dir)
        self.scan_btn = PrimaryPushButton(FIF.SEARCH, "开始检查")
        self.scan_btn.clicked.connect(self._scan)
        self.rename_btn = PushButton(FIF.SAVE_AS, "一键改为建议名")
        self.rename_btn.setEnabled(False)
        self.rename_btn.clicked.connect(self._rename_all)
        row.addWidget(self.dir_edit, 1)
        row.addWidget(self.dir_btn)
        row.addWidget(self.scan_btn)
        row.addWidget(self.rename_btn)
        lay.addLayout(row)

        self.hint = note_label(self._hint_text())
        lay.addWidget(self.hint)

        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(False)
        self.add_table(self.table, 1)

    def _hint_text(self) -> str:
        n = self.window_ref.settings_page.expected_pages()
        base = "规则：姓名+手机号码+入团志愿书.pdf（半角 +，无空格）"
        return base + f"；核对 PDF 总页数 = {n} 页" if n > 0 else base + "；预期页数未设置，不核对总页数（在系统配置填写）"

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d:
            self.dir_edit.setText(d)

    def _roster(self):
        return self.window_ref.settings_page.roster

    def _scan(self):
        d = self.dir_edit.text().strip()
        if not d or not os.path.isdir(d):
            InfoBar.warning("路径无效", "请先选择一个存在的文件夹", duration=2500, parent=self)
            return
        self.scan_btn.setEnabled(False)
        self.hint.setText(self._hint_text())
        expected = self.window_ref.settings_page.expected_pages()
        self.worker = ScanWorker(d, self._roster(), False, expected)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _on_done(self, report):
        QTimer.singleShot(0, lambda: self._apply_done(report))

    def _apply_done(self, report):
        self.scan_btn.setEnabled(True)
        self.report = report
        s = report.summary
        issues = report.issues
        self.table.setRowCount(len(issues))
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["文件名", "判定", "问题", "建议名", "说明"])
        for i, it in enumerate(issues):
            self.table.setItem(i, 0, QTableWidgetItem(it.original_name))
            self.table.setItem(i, 1, QTableWidgetItem(SEV_LABEL[it.severity]))
            self.table.setItem(i, 2, QTableWidgetItem(it.problem))
            self.table.setItem(i, 3, QTableWidgetItem(it.suggested_name))
            self.table.setItem(i, 4, QTableWidgetItem(it.reason))
            color = QColor(SEV_COLOR[it.severity])
            for col in range(5):
                self.table.item(i, col).setForeground(color)
        self.rename_btn.setEnabled(any(i.parsed and i.suggested_name != i.original_name for i in issues))
        fit_table_columns(self.table)
        InfoBar.success(
            self, "检查完成",
            f"共 {s.total} 个文件：通过 {s.ok}，有{'' if s.with_issues == 0 else '问题 ' + str(s.with_issues)}"
            f"，重复团员 {s.duplicates}，分 {s.batches} 批",
            duration=4000, parent=self,
        )

    def _rename_all(self):
        if not self.report:
            return
        d = self.report.scan_dir
        renamed = 0
        for it in self.report.issues:
            if not it.parsed:
                continue
            target = it.suggested_name
            if target == it.original_name:
                continue
            src = os.path.join(d, it.original_name)
            dst = os.path.join(d, target)
            if os.path.exists(dst):
                continue
            os.rename(src, dst)
            renamed += 1
        InfoBar.success("改名完成", f"已按建议名重命名 {renamed} 个文件", duration=3000, parent=self)
        self._scan()
