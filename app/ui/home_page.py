"""工具首页：版本信息 + 各功能入口卡片。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    CardWidget,
    FluentIcon as FIF,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)

from app.ui.page import FluentPage, note_label


def _card(title: str, desc: str, on_click) -> CardWidget:
    card = CardWidget()
    lay = QHBoxLayout(card)
    lay.setContentsMargins(20, 16, 20, 16)
    left = QVBoxLayout()
    t = StrongBodyLabel(title)
    d = note_label(desc)
    left.addWidget(t)
    left.addSpacing(2)
    left.addWidget(d)
    lay.addLayout(left, 1)
    btn = PrimaryPushButton(FIF.CHEVRON_RIGHT, "开始")
    btn.setFixedWidth(100)
    btn.clicked.connect(on_click)
    lay.addWidget(btn)
    return card


class HomePage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__('page-home', parent)
        self.window_ref = window
        lay = self.vBoxLayout

        # 欢迎区：大标题左对齐，第二行字号略大
        lay.addWidget(SubtitleLabel("欢迎，"))
        lay.addWidget(TitleLabel("团委管理员"))
        lay.addSpacing(4)

        row = QHBoxLayout()
        upload_btn = PrimaryPushButton(FIF.CLOUD, "上传档案")
        upload_btn.setFixedWidth(140)
        upload_btn.clicked.connect(lambda: window.switchTo(window.upload_page))
        org_btn = PushButton(FIF.PIE_SINGLE, "组织状况")
        org_btn.setFixedWidth(140)
        org_btn.clicked.connect(lambda: window.switchTo(window.org_page))
        row.addWidget(upload_btn)
        row.addWidget(org_btn)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addSpacing(6)

        lay.addWidget(_card(
            "命名检查", "按「张三+13800000000+入团志愿书.pdf」校验符号、结构、重复与名单交叉",
            lambda: window.switchTo(window.naming_page)))
        lay.addWidget(_card(
            "批量压缩", "PDF 压到 9.5MB 以下（站点硬限 10MB），逐文件显示结果",
            lambda: window.switchTo(window.compress_page)))
        lay.addWidget(_card(
            "批量上传", "分片协议上传志愿书，单批 ≤50 份、单文件 ≤10MB",
            lambda: window.switchTo(window.upload_page)))
        lay.addWidget(_card(
            "批量检查", "报到身份 / 报到情况 / 团员缴费三项核对，异常清单可导出",
            lambda: window.switchTo(window.check_page)))
        lay.addWidget(_card(
            "系统配置", "团支部账号模板、团员名单导入、压缩与并发参数",
            lambda: window.switchTo(window.settings_page)))
