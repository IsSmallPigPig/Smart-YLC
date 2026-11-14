"""批量压缩页：目录内 PDF 逐个压到目标大小（默认 9.5MB），表格实时刷新。"""

from __future__ import annotations

import os

from PySide6.QtCore import QThread, QTimer, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import QTableWidgetItem
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    ProgressRing,
    PushButton,
    TableWidget,
    ToolButton,
    FluentIcon as FIF,
    InfoBar,
)

from app.ui import dialogs
from app.ui.page import FluentPage, fit_table_columns
from app.constants import LIMITS, MB, human_size
from app.services.compress import compress_pdf

TARGET_95 = LIMITS["PDF_DEFAULT_TARGET_BYTES"]
TARGET_195 = LIMITS["PDF_20MB_PRESET_BYTES"]


class CompressWorker(QThread):
    row_ready = Signal(int, dict)
    all_done = Signal(int, int)

    def __init__(self, files: list[str], out_dir: str, target: int):
        super().__init__()
        self.files = files
        self.out_dir = out_dir
        self.target = target
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        ok = 0
        for i, path in enumerate(self.files):
            if self._stop:
                break
            name = os.path.basename(path)
            try:
                dst = os.path.join(self.out_dir, name)
                r = compress_pdf(path, dst, self.target)
                r["name"] = name
                r["error"] = ""
            except Exception as e:  # 单文件失败不中断批次
                r = {"name": name, "src_size": os.path.getsize(path), "dst_size": 0,
                     "dpi": 0, "quality": 0, "iterations": 0, "skipped": False,
                     "over": False, "error": str(e)}
            self.row_ready.emit(i, r)
            ok += 0 if r.get("error") else 1
        self.all_done.emit(ok, len(self.files))


class CompressPage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__('page-compress', parent)
        self.window_ref = window
        self.worker = None
        self.out_dir = ""
        lay = self.vBoxLayout

        row = QHBoxLayout()
        self.dir_edit = LineEdit()
        self.dir_edit.setPlaceholderText("选择包含 PDF 的文件夹（原地输出压缩副本到 压缩结果 子目录）")
        self.dir_btn = ToolButton(FIF.FOLDER)
        self.dir_btn.clicked.connect(self._pick_dir)
        row.addWidget(self.dir_edit, 1)
        row.addWidget(self.dir_btn)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self.target_combo = ComboBox()
        self.target_combo.addItems(["压到 9.5MB 以下（站点硬限 10MB）", "压到 19.5MB 以下"])
        self.start_btn = PrimaryPushButton(FIF.ZOOM, "开始压缩")
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = PushButton(FIF.PAUSE, "停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(lambda: self.worker and self.worker.stop())
        self.ring = ProgressRing()
        self.ring.setFixedSize(56, 56)
        self.clear_btn = PushButton(FIF.DELETE, "清除结果")
        self.clear_btn.clicked.connect(self._clear_results)
        row2.addWidget(self.target_combo, 1)
        row2.addWidget(self.start_btn)
        row2.addWidget(self.stop_btn)
        row2.addWidget(self.ring)
        row2.addWidget(self.clear_btn)
        lay.addLayout(row2)

        self.stat = BodyLabel("待开始")
        lay.addWidget(self.stat)

        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["文件", "原大小", "压缩后", "结果", "参数", "输出路径"])
        self.add_table(self.table, 1)

    def _clear_results(self):
        if self.worker is not None and self.worker.isRunning():
            InfoBar.warning("压缩进行中", "等当前批次结束或先点「停止」再清除", duration=3000, parent=self)
            return
        self.table.setRowCount(0)
        self.ring.setValue(0)
        self.stat.setText("待开始")

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d:
            self.dir_edit.setText(d)

    def _start(self):
        d = self.dir_edit.text().strip()
        if not d or not os.path.isdir(d):
            InfoBar.warning("路径无效", "请先选择一个存在的文件夹", duration=2500, parent=self)
            return
        files = sorted(
            os.path.join(d, f) for f in os.listdir(d)
            if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(d, f))
        )
        if not files:
            InfoBar.warning("没有 PDF", "所选文件夹里没有 .pdf 文件", duration=2500, parent=self)
            return
        out_dir = os.path.join(d, "压缩结果")
        os.makedirs(out_dir, exist_ok=True)
        self.out_dir = out_dir
        target = TARGET_95 if self.target_combo.currentIndex() == 0 else TARGET_195
        self.table.setRowCount(len(files))
        for i, p in enumerate(files):
            self.table.setItem(i, 0, QTableWidgetItem(os.path.basename(p)))
            self.table.setItem(i, 1, QTableWidgetItem(human_size(os.path.getsize(p))))
            for col in (2, 3, 4, 5):
                self.table.setItem(i, col, QTableWidgetItem("…"))
        fit_table_columns(self.table)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.worker = CompressWorker(files, out_dir, target)
        self.worker.row_ready.connect(self._on_row)
        self.worker.all_done.connect(self._on_done)
        self.worker.start()

    def _on_row(self, i: int, r: dict):
        QTimer.singleShot(0, lambda: self._apply_row(i, r))

    def _apply_row(self, i: int, r: dict):
        green, red, orange = QColor("#2e7d32"), QColor("#c62828"), QColor("#ef6c00")
        if r.get("error"):
            self.table.setItem(i, 3, QTableWidgetItem("失败"))
            self.table.item(i, 3).setForeground(red)
            self.table.setItem(i, 4, QTableWidgetItem(r["error"]))
            self.table.setItem(i, 2, QTableWidgetItem("-"))
            self.table.setItem(i, 5, QTableWidgetItem("-"))
            return
        dst_size, src_size = r["dst_size"], r["src_size"]
        if r.get("skipped"):
            verdict, color = "本已达标", green
        elif r.get("over"):
            verdict, color = "仍超标", red
        else:
            verdict, color = "达标", green
        ratio = f"-{(1 - dst_size / src_size) * 100:.0f}%" if src_size and dst_size else "-"
        self.table.setItem(i, 2, QTableWidgetItem(human_size(dst_size)))
        self.table.setItem(i, 3, QTableWidgetItem(verdict))
        self.table.item(i, 3).setForeground(color)
        self.table.setItem(i, 4, QTableWidgetItem(f"{r['dpi']}dpi/q{r['quality']} ×{r['iterations']} {ratio}"))
        self.table.setItem(i, 5, QTableWidgetItem(os.path.join(os.path.dirname(self.worker.files[i]), "压缩结果", r["name"])))
        done = i + 1
        self.ring.setValue(int(done / max(1, self.table.rowCount()) * 100))
        self.stat.setText(f"进行中 {done}/{self.table.rowCount()}：{r['name']}")
        fit_table_columns(self.table)

    def _on_done(self, ok: int, total: int):
        QTimer.singleShot(0, lambda: self._apply_done(ok, total))

    def _apply_done(self, ok: int, total: int):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.ring.setValue(100)
        self.stat.setText(f"完成：{ok}/{total} 个文件已输出到「压缩结果」子目录")
        dialogs.confirm(
            self.window(), "压缩完成",
            f"{ok}/{total} 个文件已完成，输出到「压缩结果」子目录。\n{self.out_dir}",
            yes="打开目录", cancel="我知道了",
            on_result=lambda open_it: QDesktopServices.openUrl(QUrl.fromLocalFile(self.out_dir))
            if open_it and self.out_dir and os.path.isdir(self.out_dir) else None,
        )
