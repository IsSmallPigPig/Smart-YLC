"""工具首页：头图铺满顶部（渐变过渡到背景）+ 欢迎大字 + 居中两个入口按钮。

字重体系：标题类文字用重字重（Black/Bold），说明类用小号常规字重。
头图在「系统配置」设置，持久化到配置文件 headerImage 键，未设置时显示同色系占位渐变。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QLinearGradient, QPainterPath, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TitleLabel,
    isDarkTheme,
)

from app.ui.page import FluentPage, note_label

BANNER_RATIO = 0.40  # 头图占视口高度的比例，铺满上方页面


def page_bg_color() -> QColor:
    """首页实际渲染出来的窗口背景色（窗口底色与 StackedWidget 半透明白叠加后）。"""
    base = QColor(32, 32, 32) if isDarkTheme() else QColor(240, 244, 249)
    overlay = QColor(255, 255, 255, 8 if isDarkTheme() else 128)
    return QColor(
        base.red() + (overlay.red() - base.red()) * overlay.alpha() // 255,
        base.green() + (overlay.green() - base.green()) * overlay.alpha() // 255,
        base.blue() + (overlay.blue() - base.blue()) * overlay.alpha() // 255,
    )


class Banner(QWidget):
    """头图：铺满整个宽度、直角贴边，图片 cover 铺满，底部渐隐到页面背景色；未设图时显示同色渐变占位。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None

    def set_image_path(self, path: str):
        if path and os.path.isfile(path):
            self._pixmap = QPixmap(path)
        else:
            self._pixmap = None
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        bg = page_bg_color()
        w, h = self.width(), self.height()
        if self._pixmap and not self._pixmap.isNull():
            # cover 铺满：等比缩放到覆盖后居中裁剪
            pm = self._pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap((pm.width() - w) // 2 * -1, (pm.height() - h) // 2 * -1, pm)
        else:
            # 占位：从略亮到背景色的柔和渐变
            lighter = QColor(bg.red() + 12, bg.green() + 12, bg.blue() + 14)
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0, lighter)
            grad.setColorAt(1, bg)
            painter.fillRect(self.rect(), grad)

        # 底部 40% 渐隐到页面背景，与下方内容自然衔接
        fade = QLinearGradient(0, h * 0.60, 0, h)
        top = QColor(bg.red(), bg.green(), bg.blue(), 0)
        fade.setColorAt(0, top)
        fade.setColorAt(1, bg)
        painter.fillRect(QRectF(0, h * 0.60, w, h * 0.40), fade)


class HomePage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__('page-home', parent)
        self.window_ref = window
        lay = self.vBoxLayout
        # 头图铺满上方页面：页面外边距归零，正文内容用自己的边距
        lay.setContentsMargins(0, 0, 0, 28)
        lay.setSpacing(0)
        # 首页内容固定刚好一屏：不出现滚动条
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # 头图横幅（通栏，贴顶，无圆角）
        self.banner = Banner()
        self.banner.setFixedHeight(260)
        lay.addWidget(self.banner)

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(36, 0, 36, 0)
        body_lay.setSpacing(8)

        # 欢迎大字块 + 按钮在头图以下的剩余空间内垂直居中
        body_lay.addStretch(1)
        self.hello_label = SubtitleLabel("欢迎，")
        self.title_label = TitleLabel("团委管理员")
        body_lay.addWidget(self.hello_label)
        body_lay.addWidget(self.title_label)
        body_lay.addSpacing(18)

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
        body_lay.addLayout(row)
        body_lay.addStretch(1)

        body_lay.addWidget(note_label("上传后需在智慧团建网页端点击「审核」完成认证"))
        lay.addWidget(body, 1)

        self._apply_font()
        self._load_header()

    # ---------- 头图持久化（设置入口在「系统配置」） ----------

    def _load_header(self):
        from app.services import store

        self.banner.set_image_path(store.load().get("headerImage", ""))

    # ---------- 字重与缩放 ----------

    def _apply_font(self):
        """大字随窗口高度缩放：团委管理员约 0.14 倍视口高、Black 字重；欢迎 Bold 略小。"""
        vh = max(420, self.viewport().height())
        title_px = max(40, int(vh * 0.14))
        hello_px = max(32, int(vh * 0.105))
        f = self.title_label.font()
        f.setPixelSize(title_px)
        f.setWeight(QFont.Weight.Black)
        self.title_label.setFont(f)
        f2 = self.hello_label.font()
        f2.setPixelSize(hello_px)
        f2.setWeight(QFont.Weight.Bold)
        self.hello_label.setFont(f2)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 头图铺满上方页面：高度跟随视口
        self.banner.setFixedHeight(max(200, int(self.viewport().height() * BANNER_RATIO)))
        self._apply_font()
