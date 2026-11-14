"""工具首页：头图铺满顶部并柔和融进窗口底色，欢迎大字 + 右下两个入口按钮。

头图在系统配置设置，持久化到配置文件 headerImage 键，未设置时显示同色系占位渐变。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TitleLabel,
    isDarkTheme,
)

from app.ui.page import FluentPage

BANNER_RATIO = 0.40  # 头图占视口高度的比例，铺满上方页面


def page_bg_color() -> QColor:
    """关闭材质时窗口绘制用的底色（窗口底色与 StackedWidget 半透明白叠加后）。"""
    base = QColor(32, 32, 32) if isDarkTheme() else QColor(240, 244, 249)
    overlay = QColor(255, 255, 255, 8 if isDarkTheme() else 128)
    return QColor(
        base.red() + (overlay.red() - base.red()) * overlay.alpha() // 255,
        base.green() + (overlay.green() - base.green()) * overlay.alpha() // 255,
        base.blue() + (overlay.blue() - base.blue()) * overlay.alpha() // 255,
    )


class Banner(QWidget):
    """头图：通栏贴顶，图片 cover 铺满，下沿按 alpha 渐隐到全透明。

    不再把图片压到某个猜出来的背景色上，而是让它直接淡出到真实窗口底色
    （纯色、毛玻璃、明暗主题都一致），交界处因此没有颜色对不上的硬切线。
    """

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
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        layer = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        layer.fill(Qt.GlobalColor.transparent)
        p = QPainter(layer)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        if self._pixmap and not self._pixmap.isNull():
            pm = self._pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((pm.width() - w) // 2 * -1, (pm.height() - h) // 2 * -1, pm)
        else:
            top = QColor(38, 84, 140) if isDarkTheme() else QColor(166, 199, 235)
            bottom = QColor(24, 42, 68) if isDarkTheme() else QColor(214, 230, 246)
            grad = QLinearGradient(0, 0, w * 0.35, h)
            grad.setColorAt(0, top)
            grad.setColorAt(1, bottom)
            p.fillRect(layer.rect(), grad)

        # DestinationIn 只改 alpha：下沿拉长渐隐，头图自然化进窗口底色
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        fade = QLinearGradient(0, 0, 0, h)
        fade.setColorAt(0, QColor(0, 0, 0, 255))
        fade.setColorAt(0.66, QColor(0, 0, 0, 255))
        fade.setColorAt(0.84, QColor(0, 0, 0, 170))
        fade.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(layer.rect(), fade)
        p.end()

        painter = QPainter(self)
        painter.drawImage(0, 0, layer)


class HomePage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__('page-home', parent)
        self.window_ref = window
        lay = self.vBoxLayout
        # 头图叠加在页面底层（不占布局），正文铺满整页：欢迎块以整个视口垂直居中，
        # 正好落在头图与下方背景的交界处
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.banner = Banner(self.scrollWidget)
        self.banner.lower()

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(36, 0, 36, 0)
        body_lay.setSpacing(8)
        body_lay.addStretch(1)
        self.hello_label = SubtitleLabel("欢迎，")
        self.title_label = TitleLabel("团委管理员")
        body_lay.addWidget(self.hello_label)
        body_lay.addWidget(self.title_label)
        body_lay.addStretch(1)

        row = QHBoxLayout()
        row.setContentsMargins(36, 0, 36, 28)
        upload_btn = PrimaryPushButton(FIF.CLOUD, "上传档案")
        upload_btn.setFixedWidth(150)
        upload_btn.clicked.connect(lambda: window.switchTo(window.upload_page))
        org_btn = PushButton(FIF.PIE_SINGLE, "组织状况")
        org_btn.setFixedWidth(150)
        org_btn.clicked.connect(lambda: window.switchTo(window.org_page))
        row.addStretch(1)
        row.addWidget(upload_btn)
        row.addSpacing(16)
        row.addWidget(org_btn)
        lay.addWidget(body, 1)
        lay.addLayout(row)

        self._apply_font()
        self._load_header()

    # ---------- 头图持久化（设置入口在系统配置） ----------

    def _load_header(self):
        from app.services import store

        self.banner.set_image_path(store.load().get("headerImage", ""))

    # ---------- 字号与字重 ----------

    def _apply_font(self):
        """保留原来的大字构图，只把字重收轻、字距放松，去掉 Black 的笨重感。"""
        vh = max(420, self.viewport().height())
        title_px = max(40, int(vh * 0.14))
        hello_px = max(32, int(vh * 0.105))

        f = self.title_label.font()
        f.setPixelSize(title_px)
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
        self.title_label.setFont(f)

        f2 = self.hello_label.font()
        f2.setPixelSize(hello_px)
        f2.setWeight(QFont.Weight.Medium)
        f2.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        self.hello_label.setFont(f2)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.banner.setFixedSize(self.scrollWidget.width(),
                                 max(200, int(self.viewport().height() * BANNER_RATIO)))
        self.banner.move(0, 0)
        self.banner.lower()
        self._apply_font()
