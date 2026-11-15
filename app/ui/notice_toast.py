"""窗口右上角的浮动提示：文案之外带一个处理按钮。

系统通知（osascript / 托盘气泡）在两端都没法可靠地加操作按钮，所以「点一下就去
处理」的能力放在这个应用内浮层上：前台收到新问题时用它提示，按钮文案由通知自身
给出（例如「去设置」），点击后由主窗口把用户送到对应位置。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    TransparentPushButton,
    TransparentToolButton,
    isDarkTheme,
    qconfig,
)

from app.services.notices import Notice
from app.ui import palette as P
from app.ui.page import set_tip, theme_font

WIDTH = 360
MARGIN = 16
GAP = 8
TOP = 56  # 让开标题栏


class _Dot(QWidget):
    """问题标记圆点。"""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(18, 18)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._color))
        p.drawEllipse(3, 3, 12, 12)


class NoticeToast(QWidget):
    """一条问题提示：图标 + 标题/说明 + 关闭；有处理入口时在底部给按钮。"""

    closed = Signal()
    action_clicked = Signal(str)  # action id

    def __init__(self, notice: Notice, parent=None):
        super().__init__(parent)
        self._notice = notice
        self.setFixedWidth(WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 10, 10)
        outer.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        self._icon = _Dot(P.DANGER, self)
        top.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        self.title_lbl = theme_font(BodyLabel(notice.title), 14, int(QFont.Weight.DemiBold))
        text.addWidget(self.title_lbl)
        self.detail_lbl = CaptionLabel(notice.detail)
        self.detail_lbl.setWordWrap(True)
        self.detail_lbl.setTextColor(QColor("#3f4751"), QColor("#9aa4b2"))
        text.addWidget(self.detail_lbl)
        top.addLayout(text, 1)

        self._close = TransparentToolButton(FIF.CLOSE, self)
        self._close.setFixedSize(24, 24)
        set_tip(self._close, "关闭")
        self._close.clicked.connect(self.dismiss)
        top.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(top)

        self.action_btn = TransparentPushButton(text="", parent=self)
        self.action_btn.setFixedHeight(26)
        self.action_btn.clicked.connect(self._fire)
        outer.addWidget(self.action_btn, 0, Qt.AlignmentFlag.AlignLeft)

        has_action = bool(notice.action and notice.action_text)
        self.action_btn.setVisible(has_action)
        if has_action:
            self.action_btn.setText(notice.action_text)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._timer.start(12000 if has_action else 7000)
        qconfig.themeChanged.connect(lambda *_: self.update())
        self.adjustSize()

    def _fire(self):
        self.action_clicked.emit(self._notice.action)
        self.dismiss()

    def dismiss(self):
        self._timer.stop()
        self.close()

    def closeEvent(self, e):
        self.closed.emit()
        super().closeEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(0, 0, -1, -1)
        dark = isDarkTheme()
        bg = QColor(45, 45, 48) if dark else QColor(252, 252, 253)
        border = QColor(255, 255, 255, 22) if dark else QColor(0, 0, 0, 16)
        p.setPen(border)
        p.setBrush(bg)
        p.drawRoundedRect(r, 10, 10)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(P.DANGER))
        p.drawRoundedRect(r.left() + 1, r.top() + 10, 3, r.height() - 20, 1.5, 1.5)


class NoticeToastHost(QObject):
    """把提示堆在窗口右上角，最多同时 3 条，超出挤掉最旧的。"""

    MAX = 3

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._toasts: list[NoticeToast] = []
        window.installEventFilter(self)

    def show(self, notice: Notice, on_action=None):
        toast = NoticeToast(notice, self._window)
        if on_action is not None:
            toast.action_clicked.connect(on_action)
        toast.closed.connect(lambda t=toast: self._remove(t))
        self._toasts.append(toast)
        while len(self._toasts) > self.MAX:
            self._toasts[0].dismiss()
        toast.show()
        self._relayout()

    def _remove(self, toast: NoticeToast):
        if toast in self._toasts:
            self._toasts.remove(toast)
            toast.deleteLater()
        self._relayout()

    def _relayout(self):
        w = self._window.width()
        y = TOP
        for toast in self._toasts:
            toast.adjustSize()
            toast.setFixedWidth(WIDTH)
            toast.move(QPoint(max(0, w - WIDTH - MARGIN), y))
            toast.raise_()
            y += toast.height() + GAP

    def eventFilter(self, obj, e):
        if obj is self._window and e.type() == QEvent.Type.Resize:
            self._relayout()
        return super().eventFilter(obj, e)
