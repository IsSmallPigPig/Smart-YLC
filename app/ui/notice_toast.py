"""窗口右上角的浮动提示：单行、左侧色条 + 优先级配色，动作按钮在最左。

形态参考 Ghost Downloader：一条圆角卡片，左侧一条按优先级着色的竖条，
内容一行展示（过长省略），右侧关闭；有处理入口时按钮放在最左。
系统通知（osascript / 托盘气泡）没法可靠加按钮，所以「点一下去处理」放在这里。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    TransparentPushButton,
    TransparentToolButton,
    isDarkTheme,
    qconfig,
)

from app.services.notices import LEVEL_ISSUE, Notice
from app.ui import palette as P
from app.ui.page import set_tip

WIDTH = 360
HEIGHT = 46
MARGIN = 16
GAP = 8
TOP = 56  # 让开标题栏

# 优先级 → 颜色；未显式给 tone 时按 level 推断
TONE_COLOR = {
    "info": P.BRAND,
    "success": P.OK,
    "warning": P.WARNING,
    "error": P.DANGER,
}


def _tone_color(notice: Notice) -> str:
    tone = notice.tone or ("error" if notice.level == LEVEL_ISSUE else "info")
    return TONE_COLOR.get(tone, P.BRAND)


class _Bar(QWidget):
    """左侧优先级竖条。"""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedWidth(4)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._color))
        p.drawRoundedRect(self.rect(), 2, 2)


class _Dot(QWidget):
    """无动作时的状态圆点。"""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(16, 16)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._color))
        p.drawEllipse(2, 2, 12, 12)


class _ElidedLabel(BodyLabel):
    """单行标签：放不下时右侧省略，保证提示始终一行。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text)
        self._full = text
        self.setWordWrap(False)
        self.setMinimumWidth(0)
        if parent is not None:
            self.setParent(parent)

    def setText(self, text: str):  # noqa: N802 (Qt 命名)
        self._full = text
        super().setText(text)
        self._apply_elide()

    def _apply_elide(self):
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, Qt.TextElideMode.ElideRight,
                                      max(0, self.width() - 2)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_elide()


class NoticeToast(QWidget):
    """一条问题提示：左侧色条 + 动作（或状态点）+ 单行内容 + 关闭。"""

    closed = Signal()
    action_clicked = Signal(str)

    def __init__(self, notice: Notice, parent=None):
        super().__init__(parent)
        self._notice = notice
        color = _tone_color(notice)
        self.setFixedWidth(WIDTH)
        self.setFixedHeight(HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 6, 8)
        lay.setSpacing(10)

        lay.addWidget(_Bar(color, self), 0)

        # 动作按钮放最左；没有动作时只放一个状态点
        if notice.action and notice.action_text:
            self.action_btn = TransparentPushButton(text=notice.action_text, parent=self)
            self.action_btn.setFixedHeight(28)
            self.action_btn.clicked.connect(self._fire)
            lay.addWidget(self.action_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            self._dot = _Dot(color, self)
            lay.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)

        text = notice.title or notice.detail
        self._label = _ElidedLabel(text, self)
        lay.addWidget(self._label, 1)

        self._close = TransparentToolButton(FIF.CLOSE, self)
        self._close.setFixedSize(24, 24)
        set_tip(self._close, "关闭")
        self._close.clicked.connect(self.dismiss)
        lay.addWidget(self._close, 0, Qt.AlignmentFlag.AlignVCenter)

        has_action = bool(notice.action and notice.action_text)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._timer.start(12000 if has_action else 7000)
        qconfig.themeChanged.connect(lambda *_: self.update())
        set_tip(self, text)

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
            toast.setFixedWidth(WIDTH)
            toast.setFixedHeight(HEIGHT)
            toast.move(QPoint(max(0, w - WIDTH - MARGIN), y))
            toast.raise_()
            y += HEIGHT + GAP

    def eventFilter(self, obj, e):
        if obj is self._window and e.type() == QEvent.Type.Resize:
            self._relayout()
        return super().eventFilter(obj, e)
