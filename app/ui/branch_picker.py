"""访问支部弹窗：选一个团支部账号，打开它的网页端标签页。

侧边栏「访问支部」与账号网页窗口的「新建标签」共用这个弹窗，
避免在侧边栏里再放一页浏览器。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QDialog

from qfluentwidgets import CaptionLabel, SubtitleLabel

from app.domain.account_health import account_status
from app.ui.combo import ComboBox
from app.ui.dialogs import FluentPanelDialog


def normal_accounts(accounts: list[dict]) -> list[dict]:
    """只留状态正常的账号。「未检测」不算正常：这个弹窗是打开网页的入口，
    列表要尽量干净，异常账号先到系统配置处理。"""
    return [a for a in accounts if a.get("userName") and account_status(a) == "ok"]


class BranchPickerDialog(FluentPanelDialog):
    """选中支部后 accept()，调用方从 chosen 取账号名。"""

    def __init__(self, accounts: list[dict], parent: QObject | None = None,
                 current: str = "", note: str = ""):
        super().__init__(parent)
        # 必须是独立窗口：基类把 FramelessWindowHint 设成唯一标志位，弹窗会退化成
        # 父窗口里的普通子控件。账号网页窗口里是原生网页图层（QWebEngineView），
        # 子控件会被它整片盖住——对话框看着在、点不到，像整个窗口卡住。
        self.setWindowFlags(Qt.WindowType.Dialog
                            | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        self.setClosableOnMaskClicked(True)   # 点遮罩即取消，不用非找到按钮
        self.chosen = ""
        self.widget.setMinimumWidth(420)
        self.viewLayout.setSpacing(10)
        self.viewLayout.addWidget(SubtitleLabel("访问支部", self.widget))
        if note:
            self.viewLayout.addWidget(CaptionLabel(note, self.widget))

        usable = normal_accounts(accounts)
        hidden = len([a for a in accounts if a.get("userName")]) - len(usable)
        if hidden:
            self.viewLayout.addWidget(CaptionLabel(
                f"仅显示状态正常的支部，已隐藏 {hidden} 个异常账号", self.widget))

        self.combo = ComboBox(self.widget)
        for acc in usable:
            user = str(acc["userName"])
            label = str(acc.get("remark") or user)
            self.combo.addItem(f"{label}（{user}）", userData=user)
        index = self.combo.findData(current)
        if index >= 0:
            self.combo.setCurrentIndex(index)
        self.viewLayout.addWidget(self.combo)

        self.yesButton.setText("打开")
        self.cancelButton.setText("取消")
        self.finished.connect(self._on_finished)

    def _on_finished(self, code: int):
        if code == QDialog.DialogCode.Accepted:
            self.chosen = str(self.combo.currentData() or "")
        self.deleteLater()


def pick_branch(accounts: list[dict], parent: QObject | None,
                on_pick, current: str = "", note: str = ""
                ) -> BranchPickerDialog | None:
    """非模态弹出；选中后回调 on_pick(账号名)。没有状态正常的账号时返回 None。"""
    usable = normal_accounts(accounts)
    if not usable:
        return None
    dlg = BranchPickerDialog(usable, parent, current, note)
    picked: list[str] = []

    def _deliver():
        # 等弹窗这一轮彻底收掉再建网页视图：原生视图创建会让窗口重建一次，
        # 在 finished 回调里同步做，遮罩与窗口状态容易错位
        if picked:
            on_pick(picked[0])

    def _finished(code: int):
        if code == QDialog.DialogCode.Accepted and dlg.chosen:
            picked.append(dlg.chosen)
            QTimer.singleShot(0, _deliver)

    dlg.finished.connect(_finished)
    if parent is not None:
        # 遮罩铺满它所属的窗口、弹窗居中：几何跟父窗口对齐才有这个效果
        dlg.setGeometry(parent.window().frameGeometry())
    dlg.show()
    return dlg
