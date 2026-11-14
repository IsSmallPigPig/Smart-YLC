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

        # 欢迎大字块：上下弹性留白把它撑在首页上半区（约占可视高度一半），左对齐
        lay.addStretch(1)
        self.hello_label = SubtitleLabel("欢迎，")
        self.title_label = TitleLabel("团委管理员")
        lay.addWidget(self.hello_label)
        lay.addSpacing(6)
        lay.addWidget(self.title_label)
        lay.addStretch(1)

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

        self._apply_font()

    def _apply_font(self):
        """大字随窗口高度缩放：团委管理员约 0.14 倍视口高，欢迎略小。"""
        vh = max(420, self.viewport().height())
        title_px = max(40, int(vh * 0.14))
        hello_px = max(32, int(vh * 0.105))
        f = self.title_label.font()
        f.setPixelSize(title_px)
        self.title_label.setFont(f)
        f2 = self.hello_label.font()
        f2.setPixelSize(hello_px)
        self.hello_label.setFont(f2)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_font()
