"""banner 下方的公告面板：只列「已知问题」，可展开看全部。

对应「不重复弹窗、公告面板要稳」：
- 空清单时整块隐藏，正常情况不出现在界面上；
- 每条按 key 复用行控件，只改文字与按钮，不整块清空重建——展开/切页/刷新都不会闪；
- 标题行点击展开或收起；问题变化时才增删对应行。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    TransparentPushButton,
    IconWidget,
    TransparentToolButton,
    isDarkTheme,
    qconfig,
)

from app.services.notices import LEVEL_ISSUE, Notice
from app.ui import palette as P
from app.ui.page import chevron_icon, note_label, set_tip, theme_font

HEADER_H = 48
ROW_H = 52
RADIUS = 10


class _Dot(QWidget):
    """问题标记：一个 8px 圆点，颜色随严重度。"""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(8, 8)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._color))
        p.drawEllipse(self.rect())


class _NoticeRow(QWidget):
    """一条问题：色调点 + 标题/说明 + 右侧处理入口。"""

    def __init__(self, notice: Notice, on_action, parent=None):
        super().__init__(parent)
        self._notice = notice
        self._on_action = on_action
        self.setFixedHeight(ROW_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 10, 6)
        lay.setSpacing(10)

        self._dot = _Dot(P.DANGER if notice.level == LEVEL_ISSUE else P.BRAND, self)
        lay.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self.title_lbl = theme_font(BodyLabel(notice.title), 13, int(QFont.Weight.DemiBold))
        text.addWidget(self.title_lbl)
        self.detail_lbl = note_label(notice.detail)
        self.detail_lbl.setWordWrap(False)
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
        self._dot._color = P.DANGER if notice.level == LEVEL_ISSUE else P.BRAND
        self._dot.update()


class NoticePanel(QWidget):
    """问题公告：标题行 + 可展开的问题列表。空清单时隐藏。"""

    def __init__(self, on_action=None, parent=None):
        super().__init__(parent)
        self._on_action = on_action
        self._expanded = True
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
        self._list = QVBoxLayout(self._body)
        self._list.setContentsMargins(0, 0, 0, 8)
        self._list.setSpacing(0)
        outer.addWidget(self._body)

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
        rows = len(self._notices)
        h = HEADER_H + (rows * ROW_H + 8 if self._expanded else 0)
        self.setFixedHeight(max(HEADER_H, h))

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
        # 左侧 3px 强调竖线，表明这是「待处理」区
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(P.DANGER))
        p.drawRoundedRect(QRectF(r.left() + 1, r.top() + 8, 3, r.height() - 16), 1.5, 1.5)
