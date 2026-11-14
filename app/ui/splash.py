"""启动闪屏：盖住整个主窗口（包括侧边栏和标题栏）。

构图参考浏览器欢迎页：居中品牌图标 + 标题 + 一行说明 + 加载环，整窗铺底色。
首屏快照回来后淡出，避免启动瞬间看到侧边栏、标题栏与尚未排好的首页内容。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, IndeterminateProgressRing, TitleLabel

from app.ui.anim import fade_out
from app.ui.brand import app_pixmap
from app.ui.home_page import page_bg_color


class SplashScreen(QWidget):
    """整窗不透明遮罩；四角按窗口圆角裁切，淡出时不露出直角。"""

    CORNER_RADIUS = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.logo = QLabel(self)
        self.logo.setPixmap(app_pixmap(80))
        self.logo.setFixedSize(80, 80)
        lay.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(20)

        self.title = TitleLabel("智慧团委", self)
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        f = self.title.font()
        f.setPixelSize(20)
        self.title.setFont(f)
        lay.addWidget(self.title)
        lay.addSpacing(8)

        self.desc = CaptionLabel("正在加载概览数据…", self)
        self.desc.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.desc)
        lay.addSpacing(22)

        self.ring = IndeterminateProgressRing(self)
        self.ring.setFixedSize(32, 32)
        self.ring.setCustomBarColor(QColor("#0078d4"), QColor("#4aa3ff"))
        lay.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(page_bg_color())
        r = self.CORNER_RADIUS
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), r, r)
        p.drawPath(path)
        p.end()

    def start(self):
        self.ring.start()

    def finish(self):
        """停止转圈并淡出；淡出结束后清掉合成层并隐藏。"""
        self.ring.stop()
        fade_out(self, duration=260, on_done=self._hidden)

    def _hidden(self):
        self.setGraphicsEffect(None)
        self.hide()
