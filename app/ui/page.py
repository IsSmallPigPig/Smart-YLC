"""页面基类：官方 gallery 同款骨架。

页面本身即 ScrollArea（FluentWindow 的 addSubInterface 直接接受 QWidget 子类），
内容放在透明的 scrollWidget 中，窗口变小时自动出现滚动条，表格与卡片不会被裁切。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QVBoxLayout, QWidget

from qfluentwidgets import CaptionLabel, ScrollArea

PAGE_MARGINS = (36, 28, 36, 28)


def note_label(text: str) -> CaptionLabel:
    """参考设计：说明文字用 12px 灰色小字，标题与备注分层。"""
    lbl = CaptionLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextColor(QColor("#6b7280"), QColor("#9ca3af"))
    return lbl


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
        """表格在滚动容器里需要保底高度，窗口缩小时不会被压扁。"""
        table.setMinimumHeight(min_height)
        self.vBoxLayout.addWidget(table, stretch)
        return table
