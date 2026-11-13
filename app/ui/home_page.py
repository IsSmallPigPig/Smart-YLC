"""工具首页：版本信息 + 各功能入口卡片。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon as FIF,
    PrimaryPushButton,
    SubtitleLabel,
    TitleLabel,
)

from app.ui.page import FluentPage


def _card(title: str, desc: str, on_click) -> CardWidget:
    card = CardWidget()
    lay = QHBoxLayout(card)
    lay.setContentsMargins(20, 16, 20, 16)
    left = QVBoxLayout()
    t = SubtitleLabel(title)
    d = BodyLabel(desc)
    d.setWordWrap(True)
    left.addWidget(t)
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

        lay.addWidget(TitleLabel("智慧团建管理员辅助"))
        lay.addWidget(BodyLabel("站点 tuan.12355.net · 接口协议来自只读侦察 · 上传后需在网页端点击「审核」"))

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
