"""导出弹窗：先选内容，再选导出范围与呈现方式。

范围与呈现方式都改成下拉框，并排放在内容清单下方——三段式选择比两个
分段控件更省横向空间，也不会让「每项一表」看起来像另一个导出入口。
分班导出时一个班一张表、各类异常都在这张表里，呈现方式没有可选项，置灰。
非模态打开（与项目其它弹窗一致），点「开始导出」后发出 chosen(contents, mode, scope)，
由调用方接文件保存对话框并在后台写盘。
"""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal as Signal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import CheckBox, StrongBodyLabel, SubtitleLabel

from app.services.export_data import CONTENT_KEYS, CONTENT_TITLES
from app.ui.combo import ComboBox
from app.ui.dialogs import FluentPanelDialog
from app.ui.page import theme_font


def _field(title: str, combo: ComboBox, parent: QWidget) -> QWidget:
    """小标题 + 下拉框，作为一个整体参与横排。"""
    box = QWidget(parent)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(theme_font(StrongBodyLabel(title, box), 13,
                             int(QFont.Weight.DemiBold)))
    combo.setParent(box)
    lay.addWidget(combo)
    return box


class ExportDialog(FluentPanelDialog):
    chosen = Signal(list, str, str)  # contents, mode, scope

    def __init__(self, scope: str = "branch", parent=None):
        super().__init__(parent)
        self.widget.setMinimumWidth(480)
        self.viewLayout.setSpacing(12)

        self.viewLayout.addWidget(SubtitleLabel("导出数据", self.widget))

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

        # 范围与呈现方式并排放在内容下方：先决定导什么，再决定怎么排
        pickers = QWidget(self.widget)
        pick_lay = QHBoxLayout(pickers)
        pick_lay.setContentsMargins(0, 4, 0, 0)
        pick_lay.setSpacing(16)

        self.scope_combo = ComboBox(pickers)
        self.scope_combo.addItem("分班导出", userData="branch")
        self.scope_combo.addItem("合并导出", userData="merged")
        idx = self.scope_combo.findData(scope if scope in ("branch", "merged") else "branch")
        self.scope_combo.setCurrentIndex(max(0, idx))

        self.mode_combo = ComboBox(pickers)
        self.mode_combo.addItem("每项一表", userData="split")
        self.mode_combo.addItem("合成一表", userData="single")
        self.scope_combo.currentIndexChanged.connect(self._sync_scope)
        pick_lay.addWidget(_field("导出范围", self.scope_combo, pickers), 1)
        pick_lay.addWidget(_field("呈现方式", self.mode_combo, pickers), 1)
        self.viewLayout.addWidget(pickers)

        self.yesButton.setText("开始导出")
        self.cancelButton.setText("取消")
        self.finished.connect(self.deleteLater)
        self._sync_scope()
        self._sync_state()

    @property
    def scope(self) -> str:
        return self.scope_combo.currentData() or "branch"

    @property
    def mode(self) -> str:
        return self.mode_combo.currentData() or "split"

    def _contents(self) -> list[str]:
        return [key for key in CONTENT_KEYS if self.boxes[key].isChecked()]

    def _sync_scope(self, *_):
        """分班导出固定「一班一表、异常合并在表内」，呈现方式在这里没有意义。"""
        branch = self.scope == "branch"
        self.mode_combo.setEnabled(not branch)
        self.mode_combo.setToolTip(
            "分班导出时一个班一张表，团员信息与各类异常都在表里，靠「异常」列区分"
            if branch else "")

    def _sync_state(self, *_):
        self.yesButton.setEnabled(bool(self._contents()))

    def validate(self) -> bool:
        contents = self._contents()
        if not contents:
            return False
        self.chosen.emit(contents, self.mode, self.scope)
        return True
