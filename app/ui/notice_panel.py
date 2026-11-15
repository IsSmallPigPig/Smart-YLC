"""banner 下方的公告面板：只列「已知问题」，默认折叠，点标题展开。

对应「不重复弹窗、公告面板要稳」：
- 空清单时整块隐藏，正常情况不出现在界面上；
- 默认折叠，只留一行「N 项待处理」，不占地方；
- 每条问题是一张错误样式的卡片（背景取 InfoBar 的错误态配色，无圆点）；
- 每条按 key 复用行控件，只改文字与按钮，不整块清空重建——展开/切页/刷新都不会闪。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    IconWidget,
    TransparentPushButton,
    TransparentToolButton,
    isDarkTheme,
    qconfig,
)

from app.services.notices import LEVEL_ISSUE, Notice
from app.ui.page import chevron_icon, note_label, set_tip, theme_font

HEADER_H = 48
RADIUS = 8
# 与 qfluentwidgets InfoBar 的错误态背景一致（实测取样）
ERROR_BG_DARK = "#442726"
ERROR_BG_LIGHT = "#fde7e9"


def _error_bg() -> QColor:
    return QColor(ERROR_BG_DARK if isDarkTheme() else ERROR_BG_LIGHT)


class _NoticeRow(QWidget):
    """一条问题：错误样式卡片（标题 + 具体原因 + 右侧处理入口），无圆点。"""

    def __init__(self, notice: Notice, on_action, parent=None):
        super().__init__(parent)
        self._notice = notice
        self._on_action = on_action
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
        lay.setSpacing(12)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        self.title_lbl = theme_font(BodyLabel(notice.title), 13, int(QFont.Weight.DemiBold))
        text.addWidget(self.title_lbl)
        self.detail_lbl = note_label(notice.detail)
        text.addWidget(self.detail_lbl)
        lay.addLayout(text, 1)

        self.action_btn = TransparentPushButton(text="", parent=self)
        self.action_btn.setFixedHeight(28)
        self.action_btn.clicked.connect(self._fire)
        lay.addWidget(self.action_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.apply(notice)

    def _fire(self):
        if self._notice.action and self._on_action is not None:
            self._on_action(self._notice.action)

    def apply(self, notice: Notice):
        self._notice = notice
        self.title_lbl.setText(notice.title)
        self.detail_lbl.setText(notice.detail)
        self.detail_lbl.setVisible(bool(notice.detail))
        has_action = bool(notice.action and notice.action_text)
        self.action_btn.setVisible(has_action)
        if has_action:
            self.action_btn.setText(notice.action_text)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_error_bg())
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), RADIUS, RADIUS)


class NoticePanel(QWidget):
    """问题公告：标题行 + 可展开的问题列表。空清单时隐藏，默认折叠。"""

    def __init__(self, on_action=None, parent=None):
        super().__init__(parent)
        self._on_action = on_action
        self._expanded = False  # 默认折叠，只留一行摘要
        self._notices: list[Notice] = []
        self._rows: dict[str, _NoticeRow] = {}
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QWidget(self)
        self._header.setFixedHeight(HEADER_H)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(14, 0, 10, 0)
        hl.setSpacing(10)
        self._icon = IconWidget(FIF.INFO, self._header)
        self._icon.setFixedSize(18, 18)
        hl.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._summary = theme_font(BodyLabel(""), 14, int(QFont.Weight.DemiBold))
        hl.addWidget(self._summary, 1)
        self._toggle_btn = TransparentToolButton(self._header)
        self._toggle_btn.setFixedSize(28, 28)
        set_tip(self._toggle_btn, "展开或收起")
        self._toggle_btn.clicked.connect(self._toggle)
        hl.addWidget(self._toggle_btn, 0)
        outer.addWidget(self._header)
        self._header.mouseReleaseEvent = lambda _e: self._toggle()  # type: ignore[assignment]

        self._body = QWidget(self)
        self._body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._list = QVBoxLayout(self._body)
        self._list.setContentsMargins(0, 0, 0, 8)
        self._list.setSpacing(6)
        outer.addWidget(self._body)
        self._body.setVisible(False)

        self.setVisible(False)
        qconfig.themeChanged.connect(lambda *_: self.update())

    # ---------- 内容 ----------

    def set_notices(self, notices: list[Notice]):
        """把清单里的问题同步到这个面板；按 key 增量更新，不清空重建。"""
        issues = [n for n in notices if n.level == LEVEL_ISSUE]
        keys = {n.key for n in issues}
        for key in [k for k in self._rows if k not in keys]:
            row = self._rows.pop(key)
            row.setParent(None)
            row.deleteLater()
        for notice in issues:
            row = self._rows.get(notice.key)
            if row is None:
                row = _NoticeRow(notice, self._on_action, self._body)
                self._rows[notice.key] = row
                self._list.addWidget(row)
            else:
                row.apply(notice)
        self._notices = issues
        if not issues:
            self.setVisible(False)
            return
        self._summary.setText(f"{len(issues)} 项待处理")
        self._toggle_btn.setIcon(chevron_icon(self._expanded))
        self._body.setVisible(self._expanded)
        self.setVisible(True)
        self._sync_height()

    def _toggle(self):
        self._expanded = not self._expanded
        self._toggle_btn.setIcon(chevron_icon(self._expanded))
        self._body.setVisible(self._expanded)
        self._sync_height()

    def _sync_height(self):
        # 只定面板高度；不要 adjustSize()，那会把 _body 强制缩成 sizeHint 的窄宽度
        if not self._expanded:
            self.setFixedHeight(HEADER_H)
            return
        self.setFixedHeight(HEADER_H + max(0, self._body.sizeHint().height()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._expanded:
            self._sync_height()

    # ---------- 外观 ----------

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(0, 0, -1, -1)
        dark = isDarkTheme()
        bg = QColor(45, 45, 48) if dark else QColor(252, 252, 253)
        border = QColor(255, 255, 255, 18) if dark else QColor(0, 0, 0, 16)
        p.setPen(border)
        p.setBrush(bg)
        p.drawRoundedRect(r, RADIUS, RADIUS)
