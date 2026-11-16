"""下载网关选择弹窗：发现新版本后列出多个加速站，点哪个走哪个。

每个网关一行：站名、一句话说明、右侧「下载」按钮；点按钮回调所选网关并关窗。
最后一行是直连 GitHub，作为兜底。加速站是第三方服务、生命周期普遍不长，
所以列表集中在 updater.GATEWAYS 维护，这里只做展示。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import CaptionLabel, PushButton, StrongBodyLabel, SubtitleLabel

from app.services import updater
from app.ui.dialogs import FluentPanelDialog

DESCS = {
    "gh-proxy": "当前最快的公开加速站，支持断点续传",
    "ghproxy.net": "备用加速站，大文件偶尔慢",
    "direct": "不经过第三方，网络通畅时最稳",
}


class GatewayPickerDialog(FluentPanelDialog):
    def __init__(self, parent=None, version: str = ""):
        super().__init__(parent)
        self._closing = False
        self.setWindowFlags(Qt.WindowType.Dialog
                            | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        self.setClosableOnMaskClicked(True)
        self.chosen = None
        self.widget.setMinimumWidth(430)
        self.viewLayout.setSpacing(12)
        self.viewLayout.addWidget(SubtitleLabel(
            f"下载 {version}" if version else "选择下载通道", self.widget))
        self.viewLayout.addWidget(CaptionLabel(
            "国内网络直连 GitHub 通常较慢，可任选一个加速站下载；"
            "所选通道失败会自动依次尝试其余通道。", self.widget))

        for gw in updater.GATEWAYS:
            self.viewLayout.addWidget(_gateway_row(gw, self._on_pick))

        self.yesButton.hide()
        self.cancelButton.setText("取消")
        self.finished.connect(lambda _: self.deleteLater())

    def _on_pick(self, gateway: dict):
        if self._closing:
            return
        self._closing = True
        self.chosen = gateway
        self.accept()


def _gateway_row(gw: dict, on_pick) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    texts = QVBoxLayout()
    texts.setSpacing(2)
    from qfluentwidgets import StrongBodyLabel

    texts.addWidget(StrongBodyLabel(gw["label"], box))
    texts.addWidget(CaptionLabel(DESCS.get(gw["key"], ""), box))
    btn = PushButton("下载", box)
    btn.clicked.connect(lambda: on_pick(gw))
    lay.addLayout(texts, 1)
    lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
    return box


def pick_gateway(parent, on_pick, version: str = "") -> GatewayPickerDialog:
    """非模态弹出选通道；选定后回调 on_pick(gateway dict)。"""
    dlg = GatewayPickerDialog(parent, version)
    dlg._closing = False

    def _finished(code: int):
        if code == QDialog.DialogCode.Accepted and dlg.chosen is not None:
            on_pick(dlg.chosen)

    dlg.finished.connect(_finished)
    if parent is not None:
        dlg.setGeometry(parent.window().frameGeometry())
    dlg.show()
    return dlg
