"""手机同步对话框：局域网直传（首选），中转作为兜底。

界面要解决的核心问题：让用户在两秒内知道「现在该怎么把手机上的 PDF 送进来」。
所以顺序是：状态 → 二维码/地址 → 收到的文件；不做没用的设置项。

局域网不可用（电脑没连网、或与手机不在同一网段）时，明确给出排查步骤，
并把云端中转作为备选说明写在这里——不假装直传可用。
"""

from __future__ import annotations

import io
import os

from PyQt5.QtCore import Qt, QTimer, pyqtSignal as Signal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    ToolButton,
)

from app.services.lan_share import LanShare, Received, lan_addresses
from app.ui import palette as P
from app.ui.page import note_label, theme_font


class PhoneSyncDialog(QDialog):
    """手机 → 电脑的局域网接收窗口。"""

    file_received = Signal(str)     # 落盘路径

    def __init__(self, parent=None, auto_start: bool = True):
        super().__init__(parent)
        self.setWindowTitle("手机同步")
        self.resize(520, 640)
        self.share = LanShare(on_received=self._on_received)
        self._timer: QTimer | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        title = StrongBodyLabel("从手机传档案", self)
        theme_font(title, P.FONT['title'], int(title.font().weight()))
        lay.addWidget(title)

        self.state_label = BodyLabel("正在准备…", self)
        self.state_label.setWordWrap(True)
        lay.addWidget(self.state_label)

        # 二维码：手机扫一下就能打开上传页，不用手输 IP
        self.qr_label = QLabel(self)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumHeight(220)
        lay.addWidget(self.qr_label, 1)

        # 地址行：二维码扫不了时手输
        addr_row = QWidget(self)
        addr_lay = QHBoxLayout(addr_row)
        addr_lay.setContentsMargins(0, 0, 0, 0)
        addr_lay.setSpacing(8)
        self.addr_label = note_label("—")
        self.addr_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.copy_btn = ToolButton(FIF.COPY, addr_row)
        self.copy_btn.setToolTip("复制地址")
        self.copy_btn.clicked.connect(self._copy_address)
        addr_lay.addWidget(self.addr_label)
        addr_lay.addWidget(self.copy_btn)
        addr_lay.addStretch(1)
        lay.addWidget(addr_row)

        # 操作
        btn_row = QWidget(self)
        btn_lay = QHBoxLayout(btn_row)
        btn_lay.setContentsMargins(0, 0, 0, 0)
        btn_lay.setSpacing(12)
        self.toggle_btn = PrimaryPushButton(FIF.DOWNLOAD, "开始接收", btn_row)
        self.toggle_btn.clicked.connect(self._toggle)
        self.open_btn = PushButton(FIF.FOLDER, "打开同步文件夹", btn_row)
        self.open_btn.clicked.connect(self._open_folder)
        for b in (self.toggle_btn, self.open_btn):
            b.setMinimumWidth(140)
            btn_lay.addWidget(b, 1)
        lay.addWidget(btn_row)

        self.result_label = BodyLabel("", self)
        self.result_label.setWordWrap(True)
        lay.addWidget(self.result_label)

        if auto_start:
            self._toggle()

    # ---- 开关 ----

    def _toggle(self):
        if self.share.running:
            self.share.stop()
            self._stop_timer()
            self.qr_label.clear()
            self.addr_label.setText("—")
            self.state_label.setText("已停止接收。点「开始接收」重新开始。")
            self.toggle_btn.setText("开始接收")
            return
        try:
            url = self.share.start()
        except OSError as exc:
            self.state_label.setText(f"无法开启：{exc}")
            return
        self.toggle_btn.setText("停止接收")
        if not url:
            # 服务起来了但没有局域网地址：典型是电脑没连 Wi-Fi
            self.state_label.setText(
                "没有找到局域网地址。请让电脑与手机连同一个 Wi-Fi，"
                "或用手机开热点让电脑连上，然后点「停止接收」再重新开始。")
            self.addr_label.setText("—")
            self.qr_label.clear()
            return
        self.state_label.setText(
            "手机与电脑连同一个 Wi-Fi，用手机相机或微信扫下面的二维码，"
            "选好 PDF 后点「开始传输」。传输只在局域网内进行，不经过互联网。")
        self.addr_label.setText(url)
        self._show_qr(url)
        self._start_timer()

    def _show_qr(self, url: str):
        try:
            import qrcode

            img = qrcode.make(url, box_size=6, border=2)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            pix = QPixmap()
            pix.loadFromData(buf.getvalue(), "PNG")
            self.qr_label.setPixmap(pix)
        except Exception:                    # 没装 qrcode 就退化成手输地址
            self.qr_label.setText("（未安装二维码组件，请手动输入下面的地址）")

    def _copy_address(self):
        from PyQt5.QtWidgets import QApplication

        text = self.addr_label.text()
        if text and text != "—":
            QApplication.clipboard().setText(text)
            self.result_label.setText("地址已复制，发到手机上打开即可。")

    def _open_folder(self):
        from PyQt5.QtGui import QDesktopServices
        from PyQt5.QtCore import QUrl

        from app.services.phone_sync import sync_dir

        QDesktopServices.openUrl(QUrl.fromLocalFile(sync_dir()))

    # ---- 收件回调 ----

    def _on_received(self, item: Received):
        # 回调在 HTTP 线程里，回到界面线程再更新
        QTimer.singleShot(0, lambda: self._show_received(item))

    def _show_received(self, item: Received):
        mark = "已收到" if item.status in ("ok", "duplicate", "need_rename") else "未接收"
        line = f"{mark}：{item.name}"
        if item.note:
            line += f"（{item.note}）"
        self.result_label.setText(line)
        if item.status in ("ok", "need_rename") and item.path:
            self.file_received.emit(item.path)

    # ---- 空闲自动停止 ----

    def _start_timer(self):
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._idle_check)
        self._timer.start(60_000)

    def _stop_timer(self):
        if self._timer is not None:
            self._timer.stop()

    def _idle_check(self):
        if self.share.expired():
            self.share.stop()
            self._stop_timer()
            self.state_label.setText("半小时没有收到文件，已自动停止接收。")
            self.toggle_btn.setText("开始接收")

    def closeEvent(self, event):
        self.share.stop()
        self._stop_timer()
        super().closeEvent(event)


def lan_ready() -> tuple[bool, str]:
    """给页面判断用：现在能不能开局域网直传。"""
    addrs = lan_addresses()
    if addrs:
        return True, addrs[0]
    return False, ""
