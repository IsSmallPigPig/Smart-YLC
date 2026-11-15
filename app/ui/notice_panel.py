"""banner 下方的公告面板：只列「已知问题」，默认折叠，点标题展开。

对应「不重复弹窗、公告面板要稳」：
- 空清单时整块隐藏，正常情况不出现在界面上；
- **整块是一张折叠卡片**：标题行与各条问题同属一张卡，行与行之间只有一条分隔线，
  不再每条各画一张卡片（那样看着像一堆碎片）；
- 卡片底色是半透明的，mica / 毛玻璃下能透出窗口背景；
- 展开收起带高度动画，不是一下子弹开；
- 每条按 key 复用行控件，只改文字与按钮，不整块清空重建——展开/切页/刷新都不会闪。
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

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
RADIUS = 10
EXPAND_MS = 180
PAD_X = 16        # 行内左右边距：标题与卡片边缘、按钮与右边缘都按这个对齐
ROW_PAD_Y = 10


def _card_bg() -> QColor:
    """卡片底：半透明，让 mica / 毛玻璃透上来。"""
    return QColor(255, 255, 255, 20) if isDarkTheme() else QColor(255, 255, 255, 170)


def _card_border() -> QColor:
    return QColor(255, 255, 255, 34) if isDarkTheme() else QColor(0, 0, 0, 20)


def _divider() -> QColor:
    return QColor(255, 255, 255, 26) if isDarkTheme() else QColor(0, 0, 0, 18)


def _title_color() -> QColor:
    """错误态标题色：与 InfoBar 的 ERROR 文字同色系，不再是整块红底。"""
    return QColor("#FF99A4") if isDarkTheme() else QColor("#C42B1C")


class _NoticeRow(QWidget):
    """一条问题：标题 + 具体原因 + 右侧处理入口。行本身不画卡片，只在底部画分隔线。"""

    def __init__(self, notice: Notice, on_action, parent=None):
        super().__init__(parent)
        self._notice = notice
        self._on_action = on_action
        self._last = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(PAD_X, ROW_PAD_Y, PAD_X - 10, ROW_PAD_Y)
        lay.setSpacing(12)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(3)
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
        self.title_lbl.setTextColor(_title_color(), _title_color())
        self.detail_lbl.setText(notice.detail)
        self.detail_lbl.setVisible(bool(notice.detail))
        has_action = bool(notice.action and notice.action_text)
        self.action_btn.setVisible(has_action)
        if has_action:
            self.action_btn.setText(notice.action_text)

    def set_last(self, last: bool):
        """最后一条不画分隔线，卡片底边就是它的收口。"""
        if self._last != last:
            self._last = last
            self.update()

    def drop(self):
        """从面板里移除：先把焦点挪走，再交给 Qt 延迟销毁。

        不 setParent(None)——那会让它短暂变成顶层控件，碰巧握着焦点时
        后续的 clearFocus 会踩空。"""
        focus = QApplication.focusWidget()
        if focus is not None and (focus is self or self.isAncestorOf(focus)):
            focus.clearFocus()
        self.hide()
        self.deleteLater()

    def paintEvent(self, _e):
        if self._last:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(_divider(), 1))
        y = self.height() - 1
        p.drawLine(PAD_X, y, self.width() - PAD_X, y)


class NoticePanel(QWidget):
    """问题公告：一张折叠卡片，标题行 + 可展开的问题列表。空清单时隐藏。"""

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
        hl.setContentsMargins(PAD_X, 0, PAD_X - 10, 0)
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
        self._list.setContentsMargins(0, 0, 0, 6)
        self._list.setSpacing(0)
        outer.addWidget(self._body)
        self._body.setVisible(False)

        # 高度动画：展开/收起是滑出来的，不是一下弹开
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(EXPAND_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: self.setFixedHeight(int(v)))
        self._anim.finished.connect(self._after_anim)

        self.setVisible(False)
        qconfig.themeChanged.connect(self._restyle)

    # ---------- 内容 ----------

    def set_notices(self, notices: list[Notice]):
        """把清单里的问题同步到这个面板；按 key 增量更新，不清空重建。"""
        issues = [n for n in notices if n.level == LEVEL_ISSUE]
        keys = {n.key for n in issues}
        for key in [k for k in self._rows if k not in keys]:
            row = self._rows.pop(key)
            self._list.removeWidget(row)
            row.drop()
        for notice in issues:
            row = self._rows.get(notice.key)
            if row is None:
                row = _NoticeRow(notice, self._on_action, self._body)
                self._rows[notice.key] = row
                self._list.addWidget(row)
            else:
                row.apply(notice)
        for i, row in enumerate(self._rows.values()):
            row.set_last(i == len(self._rows) - 1)

        self._notices = issues
        if not issues:
            self._anim.stop()
            self._body.setVisible(False)
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
        if self._expanded:
            self._body.setVisible(True)
        self._sync_height(animate=True)

    def _sync_height(self, animate: bool = False):
        """折叠时只到标题行；展开时到「标题 + 全部行」。

        不要 adjustSize()：那会把 _body 压成 sizeHint 的窄宽度，行就只剩半截。
        """
        target = HEADER_H
        if self._expanded:
            target = HEADER_H + max(0, self._body.sizeHint().height())
        if not animate:
            self._anim.stop()
            self.setFixedHeight(target)
            return
        self._anim.stop()
        self._anim.setStartValue(max(self.height(), HEADER_H))
        self._anim.setEndValue(target)
        self._anim.start()

    def _restyle(self):
        """主题切换后行标题的错误色要跟着变（颜色是按当前主题算的）。"""
        for row in self._rows.values():
            row.apply(row._notice)
        self.update()

    def _after_anim(self):
        """收起动画走完再把 body 藏掉，展开过程中它必须可见。"""
        if not self._expanded:
            self._body.setVisible(False)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._expanded and self._anim.state() != QVariantAnimation.State.Running:
            self._sync_height()

    # ---------- 外观 ----------

    def paintEvent(self, _e):
        """整块卡片：圆角 + 半透明底 + 细边框，行与行之间只靠分隔线区隔。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(0, 0, -1, -1)
        p.setPen(QPen(_card_border(), 1))
        p.setBrush(_card_bg())
        p.drawRoundedRect(r, RADIUS, RADIUS)

        # 标题行与问题列表之间也有一条分隔线
        if self._expanded and self._rows:
            p.setPen(QPen(_divider(), 1))
            y = HEADER_H
            p.drawLine(PAD_X, y, self.width() - PAD_X, y)
