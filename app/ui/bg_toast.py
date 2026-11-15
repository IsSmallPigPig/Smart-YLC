"""后台提醒：应用在托盘里时也能看到、点一下就跳到对应页面。

macOS 上 osascript 发出的通知，点开的是「脚本编辑器」——通知归属的是发通知的进程，
osascript 的归属改不了，也拿不到点击回调。所以后台提醒改用应用自己的浮窗：
置顶、不抢焦点，点卡片就把主界面叫出来并切到该处理的那一页。

Windows 仍用托盘气泡（原生、支持 messageClicked），点击回调在这里排队挂上；
气泡发不出去时同样回落到浮窗，不再静默丢消息。
"""

from __future__ import annotations

import platform

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import CaptionLabel, StrongBodyLabel, TransparentToolButton, isDarkTheme
from qfluentwidgets import FluentIcon as FIF

from app.ui import palette as P

WIDTH = 372
GAP = 10
MARGIN = 16
TOP = 44          # 让开菜单栏 / 任务栏
LIFETIME = 12000  # 12 秒后自动收起

_TONE = {
    "error": P.DANGER,
    "warning": P.WARNING,
    "success": P.OK,
    "info": P.BRAND,
}


class _Elided(CaptionLabel):
    """一行放不下就省掉尾巴——浮窗只有一行高，折行会被切掉。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = text
        self.setWordWrap(False)
        self.setText(text)

    def setText(self, text: str):  # noqa: N802 - Qt 接口
        self._full = text
        self._apply()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply()

    def _apply(self):
        width = max(0, self.width() - 2)
        if width <= 0:
            super().setText(self._full)
            return
        elided = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, width)
        super().setText(elided)


class Toast(QWidget):
    """一张浮窗卡片：左侧色条按严重程度着色，点卡片执行动作，右侧可关闭。"""

    clicked = Signal()
    closed = Signal(object)

    def __init__(self, title: str, body: str, tone: str, has_action: bool, parent=None):
        super().__init__(parent, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tone = _TONE.get(tone, P.BRAND)
        self._hover = False

        self.setFixedWidth(WIDTH)
        box = QHBoxLayout(self)
        box.setContentsMargins(16, 10, 8, 10)
        box.setSpacing(10)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.title_lbl = StrongBodyLabel(title, self)
        f = self.title_lbl.font()
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.DemiBold)
        self.title_lbl.setFont(f)
        text.addWidget(self.title_lbl)
        self.body_lbl = _Elided(body, self)
        self.body_lbl.setTextColor(QColor(P.MUTED), QColor("#b9bcc4"))
        text.addWidget(self.body_lbl)
        if not body:
            self.body_lbl.hide()
        box.addLayout(text, 1)

        if has_action:
            self.hint = CaptionLabel("查看", self)
            self.hint.setTextColor(QColor(P.BRAND), QColor("#8ab4ff"))
            box.addWidget(self.hint, 0, Qt.AlignmentFlag.AlignVCenter)

        self.close_btn = TransparentToolButton(FIF.CLOSE, self)
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setIconSize(self.close_btn.iconSize() * 0.7)
        self.close_btn.clicked.connect(self.dismiss)
        box.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.setFixedHeight(58 if body else 46)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(LIFETIME)
        self._timer.timeout.connect(self.dismiss)

    # ---------- 绘制 ----------

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(card, 10, 10)

        p.setPen(Qt.PenStyle.NoPen)
        if isDarkTheme():
            p.setBrush(QColor(43, 43, 46, 246))
        else:
            p.setBrush(QColor(252, 252, 253, 250))
        p.drawPath(path)

        # 左侧色条：限制在圆角内，避免方头露在卡片外面
        p.save()
        p.setClipPath(path)
        p.setBrush(QColor(self._tone))
        p.drawRoundedRect(QRectF(0, 0, 4, self.height()), 2, 2)
        p.restore()

        border = QColor(255, 255, 255, 26) if isDarkTheme() else QColor(0, 0, 0, 26)
        p.setPen(QPen(border, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    # ---------- 交互 ----------

    def show_at(self, pos: QPoint):
        self.move(pos)
        self.show()
        self.raise_()
        self._timer.start()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            self.dismiss()
        super().mouseReleaseEvent(e)

    def enterEvent(self, e):
        self._hover = True
        self._timer.stop()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._timer.start()
        super().leaveEvent(e)

    def dismiss(self):
        self._timer.stop()
        self.closed.emit(self)
        self.hide()
        self.deleteLater()


class _Host:
    """最多同时三张，新的挤掉最旧的；按主屏右上角向下排。"""

    def __init__(self):
        self.items: list[Toast] = []

    def push(self, title: str, body: str, tone: str, on_click=None) -> bool:
        if QApplication.instance() is None:
            return False
        toast = Toast(title, body, tone, has_action=bool(on_click or _default_action))
        if on_click is not None:
            toast.clicked.connect(on_click)
        elif _default_action is not None:
            toast.clicked.connect(_default_action)
        toast.closed.connect(self._remove)
        self.items.append(toast)
        while len(self.items) > 3:
            self.items[0].dismiss()
        toast.show_at(QPoint(0, 0))
        self._reposition()
        return True

    def _remove(self, toast):
        if toast in self.items:
            self.items.remove(toast)
        self._reposition()

    def _reposition(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        y = area.top() + TOP
        for toast in self.items:
            toast.move(area.right() - toast.width() - MARGIN, y)
            y += toast.height() + GAP


_host: _Host | None = None
_default_action = None


def set_default_action(fn):
    """没有具体动作时点卡片做什么（默认：把主界面叫出来）。"""
    global _default_action
    _default_action = fn


def alert(title: str, body: str = "", tone: str = "",
          tray=None, on_click=None) -> bool:
    """发一条后台提醒，返回是否已送达。

    Windows / Linux 先走托盘气泡（原生、可点击）；macOS 与发不出去时用应用内浮窗。
    两条路径都能绑定点击，点了就执行 on_click（默认把主界面叫出来）。"""
    global _host
    if platform.system() != "Darwin":
        from app.services.notify import send

        if send(title, body, tray, on_click):
            return True
    if _host is None:
        _host = _Host()
    return _host.push(title, body, tone, on_click)
