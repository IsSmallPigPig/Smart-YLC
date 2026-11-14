"""导出弹窗：选择导出范围、内容与呈现方式。

一个入口装下原来「分班导出 / 合并导出」两个按钮：范围在弹窗里选。
非模态打开（与项目其它弹窗一致），点「开始导出」后发出 chosen(contents, mode, scope)，
由调用方接文件保存对话框并在后台写盘。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QWidget

from qfluentwidgets import CheckBox, SegmentedWidget, StrongBodyLabel, SubtitleLabel

from app.services.export_data import CONTENT_KEYS, CONTENT_TITLES
from app.ui.dialogs import FluentPanelDialog
from app.ui.page import theme_font


class ExportDialog(FluentPanelDialog):
    chosen = Signal(list, str, str)  # contents, mode, scope

    def __init__(self, scope: str = "branch", parent=None):
        super().__init__(parent)
        self.widget.setMinimumWidth(480)
        self.viewLayout.setSpacing(8)

        self.viewLayout.addWidget(SubtitleLabel("导出数据", self.widget))
        self.scope_seg = SegmentedWidget(self.widget)
        self.scope_seg.addItem("branch", "分班导出")
        self.scope_seg.addItem("merged", "合并导出")
        self.scope_seg.setCurrentItem(scope if scope in ("branch", "merged") else "branch")
        self.scope_seg.setFixedHeight(34)
        self.viewLayout.addWidget(self.scope_seg)

        self.viewLayout.addWidget(
            theme_font(StrongBodyLabel("导出内容", self.widget), 14,
                       int(QFont.Weight.DemiBold)))
        self.boxes: dict[str, CheckBox] = {}
        for key in CONTENT_KEYS:
            box = CheckBox(CONTENT_TITLES[key], self.widget)
            box.setChecked(True)
            box.stateChanged.connect(self._sync_state)
            self.boxes[key] = box
            self.viewLayout.addWidget(box)

        self.viewLayout.addWidget(
            theme_font(StrongBodyLabel("呈现方式", self.widget), 14,
                       int(QFont.Weight.DemiBold)))
        self.mode = SegmentedWidget(self.widget)
        self.mode.addItem("split", "每项一表")
        self.mode.addItem("single", "合成一表")
        self.mode.setCurrentItem("split")
        self.mode.setFixedHeight(34)
        self.viewLayout.addWidget(self.mode)

        self.yesButton.setText("开始导出")
        self.cancelButton.setText("取消")
        self.finished.connect(self.deleteLater)
        self._sync_state()

    @property
    def scope(self) -> str:
        return self.scope_seg.currentRouteKey() or "branch"

    def _contents(self) -> list[str]:
        return [key for key in CONTENT_KEYS if self.boxes[key].isChecked()]

    def _sync_state(self, *_):
        self.yesButton.setEnabled(bool(self._contents()))

    def validate(self) -> bool:
        contents = self._contents()
        if not contents:
            return False
        self.chosen.emit(contents, self.mode.currentRouteKey() or "split", self.scope)
        return True
