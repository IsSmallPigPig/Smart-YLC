"""访问支部弹窗：选一个团支部账号，打开它的网页端标签页。

侧边栏「访问支部」与账号网页窗口的「新建标签」共用这个弹窗，
避免在侧边栏里再放一页浏览器。
"""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog

from qfluentwidgets import ComboBox, SubtitleLabel

from app.ui.dialogs import FluentPanelDialog
from app.ui.page import note_label


class BranchPickerDialog(FluentPanelDialog):
    """选中支部后 accept()，调用方从 chosen 取账号名。"""

    def __init__(self, accounts: list[dict], parent: QObject | None = None,
                 current: str = ""):
        super().__init__(parent)
        self.chosen = ""
        self.widget.setMinimumWidth(420)
        self.viewLayout.setSpacing(10)
        self.viewLayout.addWidget(SubtitleLabel("访问支部", self.widget))

        self.combo = ComboBox(self.widget)
        for acc in accounts:
            user = str(acc.get("userName") or "")
            if not user:
                continue
            label = str(acc.get("remark") or user)
            self.combo.addItem(f"{label}（{user}）", userData=user)
        index = self.combo.findData(current)
        if index >= 0:
            self.combo.setCurrentIndex(index)
        self.viewLayout.addWidget(self.combo)
        self.viewLayout.addWidget(note_label("只列出状态正常的支部；每个支部一份独立浏览器存储，标签之间互不顶号"))

        self.yesButton.setText("打开")
        self.cancelButton.setText("取消")
        self.finished.connect(self._on_finished)

    def _on_finished(self, code: int):
        if code == QDialog.DialogCode.Accepted:
            self.chosen = str(self.combo.currentData() or "")
        self.deleteLater()


def pick_branch(accounts: list[dict], parent: QObject | None,
                on_pick, current: str = "") -> BranchPickerDialog | None:
    """非模态弹出；选中后回调 on_pick(账号名)。没有账号时返回 None。"""
    usable = [a for a in accounts if a.get("userName")]
    if not usable:
        return None
    dlg = BranchPickerDialog(usable, parent, current)
    dlg.finished.connect(lambda code: code == QDialog.DialogCode.Accepted
                         and dlg.chosen and on_pick(dlg.chosen))
    dlg.show()
    return dlg
