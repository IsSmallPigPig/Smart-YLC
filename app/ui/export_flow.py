"""导出流程：弹窗选内容 → 选保存位置 → 后台写盘 → 信息条反馈。

首页与组织管理共用，避免两处各写一份保存对话框与线程收尾。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog

from qfluentwidgets import InfoBar

from app.ui.export_dialog import ExportDialog
from app.ui.page import CallbackWorker

_WORKERS: list[CallbackWorker] = []


def start_export(parent, scope: str = "branch") -> ExportDialog:
    """打开导出弹窗；范围与呈现方式都在弹窗里选，最终始终落一个 xlsx。"""
    dialog = ExportDialog(scope, parent.window() if parent is not None else None)
    dialog.chosen.connect(
        lambda contents, mode, chosen_scope: QTimer.singleShot(
            0, lambda: _save(parent, chosen_scope, contents, mode)))
    dialog.show()
    return dialog


def _save(parent, scope: str, contents: list[str], mode: str):
    from app.services.export_data import CONTENT_LABELS, default_name

    scope_text = "分班导出" if scope == "branch" else "合并导出"
    mode_text = "每项一表" if mode == "split" else "合成一表"
    picked = "+".join(CONTENT_LABELS.get(c, c) for c in contents)
    default = default_name("导出数据", ".xlsx", scope_text, mode_text, picked)
    filt = "Excel (*.xlsx)"
    path, _ = QFileDialog.getSaveFileName(parent, "选择保存位置", default, filt)
    if not path:
        return
    if not path.lower().endswith(".xlsx"):
        path += ".xlsx"

    from app.services import export_data, store

    cfg = store.load()  # GUI 线程读一次快照，写盘在后台

    def _run():
        return export_data.run_export(cfg, contents, mode, scope, path)

    def _done(result: dict):
        _WORKERS[:] = [w for w in _WORKERS if w is worker]
        InfoBar.success(
            "导出完成",
            f"{result['rows']} 行数据，{result['files']} 个文件"
            f"（{result['branches']} 个支部）\n{path}",
            duration=8000, parent=parent)

    def _fail(message: str):
        _WORKERS[:] = [w for w in _WORKERS if w is worker]
        InfoBar.error("导出失败", message, duration=8000, parent=parent)

    worker = CallbackWorker(_run, parent)
    worker.ok.connect(_done)
    worker.fail.connect(_fail)
    _WORKERS.append(worker)
    worker.start()
