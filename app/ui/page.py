"""页面基类：官方 gallery 同款骨架。

页面本身即 ScrollArea（FluentWindow 的 addSubInterface 直接接受 QWidget 子类），
内容放在透明的 scrollWidget 中，窗口变小时自动出现滚动条，表格与卡片不会被裁切。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap, QTransform
from PySide6.QtWidgets import QHeaderView, QHBoxLayout, QTableView, QVBoxLayout, QWidget

from qfluentwidgets import CaptionLabel, CardWidget, FluentIcon as FIF, ScrollArea, StrongBodyLabel, TransparentToolButton, qconfig

PAGE_MARGINS = (36, 28, 36, 28)


def note_label(text: str) -> CaptionLabel:
    """说明文字：12px 灰色小字，浅色主题用 gray-600 保证对比度，标题与备注分层。"""
    lbl = CaptionLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextColor(QColor("#4b5563"), QColor("#9ca3af"))
    return lbl


def theme_font(lbl, pixel_size: int, weight: int = 0):
    """按像素设置字号/字重，不覆盖 fluent 的主题样式表。

    用 widget.setStyleSheet 写 font-* 会整体替换掉 fluent 的 qss，文字颜色
    退回 Qt 调色板——系统外观是深色时调色板文字为白色，浅色页面里就变成
    白字白底。QFont 只改字体属性，颜色仍由 fluent 按明暗模式联动。"""
    f = lbl.font()
    f.setPixelSize(pixel_size)
    if weight:
        f.setWeight(QFont.Weight(weight))
    lbl.setFont(f)
    return lbl


def section_title(text: str) -> StrongBodyLabel:
    """卡片区块标题：16px 加粗，比正文略大，与灰色说明文字拉开层级。"""
    return theme_font(StrongBodyLabel(text), 16, int(QFont.Weight.DemiBold))


def sub_title(text: str) -> StrongBodyLabel:
    """卡片内分组小标题：15px 加粗，弱于卡片标题一级。"""
    return theme_font(StrongBodyLabel(text), 15, int(QFont.Weight.DemiBold))


class SettingRow(QWidget):
    """仿 Ghost Downloader 的设置行：左侧标题 + 灰色说明，右侧控件右对齐垂直居中。

    用法：row = SettingRow("明暗模式", "更改应用的外观", combo)；
    control 也可以是先包好的 QWidget（多个控件时）。"""

    def __init__(self, title: str, desc: str = "", control: QWidget | None = None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(16)
        text_lay = QVBoxLayout()
        text_lay.setContentsMargins(0, 0, 0, 0)
        text_lay.setSpacing(3)
        text_lay.addWidget(theme_font(StrongBodyLabel(title), 14, int(QFont.Weight.DemiBold)))
        if desc:
            text_lay.addWidget(note_label(desc))
        lay.addLayout(text_lay, 1)
        if control is not None:
            lay.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


class CollapsibleCard(CardWidget):
    """折叠卡片：标题行 + 细线条 chevron 箭头（展开朝上 / 收起朝下，无按钮底色）。

    用法：content = CollapsibleCard("标题", expanded=True)；往
    content.body_lay 里加内容即可。"""

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self._expanded = bool(expanded)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(10)
        header = QHBoxLayout()
        header.addWidget(section_title(title))
        header.addStretch(1)
        self.toggle_btn = TransparentToolButton()
        self.toggle_btn.setToolTip("展开 / 收起")
        self.toggle_btn.clicked.connect(self.toggle)
        header.addWidget(self.toggle_btn)
        outer.addLayout(header)
        self.body = QWidget(self)
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(12)
        outer.addWidget(self.body)
        self._sync()
        # 主题切换时箭头颜色要按新主题重烘焙（旋转图标是像素级副本）
        qconfig.themeChanged.connect(self._sync)

    def toggle(self):
        self._expanded = not self._expanded
        self._sync()

    def _sync(self, *_):
        self.body.setVisible(self._expanded)
        icon = FIF.CHEVRON_DOWN_MED.icon()
        if self._expanded:
            # fluent 没有 chevron-up 图标：向下 chevron 旋转 180°
            img = icon.pixmap(16, 16).toImage().transformed(QTransform().rotate(180))
            icon = QIcon(QPixmap.fromImage(img))
        self.toggle_btn.setIcon(icon)


class FluentPage(ScrollArea):
    def __init__(self, object_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)

        self.scrollWidget = QWidget(self)
        self.scrollWidget.setObjectName(f"{object_name}-scroll")
        self.vBoxLayout = QVBoxLayout(self.scrollWidget)
        self.vBoxLayout.setContentsMargins(*PAGE_MARGINS)
        self.vBoxLayout.setSpacing(12)
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        # 官方 gallery 的透明背景处理：滚动区与内容容器都不画自己的底色
        self.setStyleSheet("QScrollArea{background: transparent; border: none}")
        self.scrollWidget.setStyleSheet(
            f"QWidget#{object_name}-scroll{{background: transparent}}"
        )

    def add_table(self, table, stretch: int = 0, min_height: int = 180):
        """表格在滚动容器里需要保底高度，窗口缩小时不会被压扁。

        表格列宽按内容自适应：任何文字都完整展示，不因默认宽度截断；
        内容总宽超出视口时出横向滚动条。普通容器（无表头）只做高度保底。"""
        table.setMinimumHeight(min_height)
        if isinstance(table, QTableView):
            header = table.horizontalHeader()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.vBoxLayout.addWidget(table, stretch)
        return table
