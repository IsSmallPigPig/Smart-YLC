"""工具首页：欢迎大字 + 居中两个入口按钮。"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import PrimaryPushButton, PushButton, SubtitleLabel, TitleLabel

from app.ui.page import FluentPage, note_label


class HomePage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__('page-home', parent)
        self.window_ref = window
        lay = self.vBoxLayout
        lay.setSpacing(10)

        # 欢迎大字块（大标题左对齐）
        lay.addSpacing(72)
        lay.addWidget(SubtitleLabel("欢迎，"))
        lay.addSpacing(2)
        lay.addWidget(TitleLabel("团委管理员"))
        lay.addSpacing(28)

        # 两个入口按钮：居中
        row = QHBoxLayout()
        row.addStretch(1)
        upload_btn = PrimaryPushButton(FIF.CLOUD, "上传档案")
        upload_btn.setFixedWidth(150)
        upload_btn.clicked.connect(lambda: window.switchTo(window.upload_page))
        org_btn = PushButton(FIF.PIE_SINGLE, "组织状况")
        org_btn.setFixedWidth(150)
        org_btn.clicked.connect(lambda: window.switchTo(window.org_page))
        row.addWidget(upload_btn)
        row.addSpacing(16)
        row.addWidget(org_btn)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addSpacing(20)

        lay.addWidget(note_label("上传后需在智慧团建网页端点击「审核」完成认证"))
