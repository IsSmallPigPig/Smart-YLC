"""工具首页：头图横幅（渐变过渡到背景）+ 欢迎大字 + 居中两个入口按钮。

字重体系：标题类文字用重字重（Black/Bold），说明类用小号常规字重。
头图持久化到配置文件 headerImage 键，未设置时显示同色系占位渐变。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QLinearGradient, QPainterPath, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QVBoxLayout, QWidget

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import InfoBar, PrimaryPushButton, PushButton, SubtitleLabel, TitleLabel, ToolButton, isDarkTheme

from app.ui.page import FluentPage, note_label


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
    """头图横幅：图片 cover 铺满，底部渐隐到页面背景色；未设图时显示同色渐变占位。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self.setMinimumHeight(120)

        self.pick_btn = ToolButton(FIF.PHOTO, self)
        self.pick_btn.setToolTip("设置头图")
        self.clear_btn = ToolButton(FIF.DELETE, self)
        self.clear_btn.setToolTip("清除头图")
        self.clear_btn.setVisible(False)

    def set_image_path(self, path: str):
        if path and os.path.isfile(path):
            self._pixmap = QPixmap(path)
            self.clear_btn.setVisible(True)
        else:
            self._pixmap = None
            self.clear_btn.setVisible(False)
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        bg = page_bg_color()
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        painter.setClipPath(path)

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

        # 底部 45% 渐隐到页面背景，与下方内容自然衔接
        fade = QLinearGradient(0, h * 0.55, 0, h)
        top = QColor(bg.red(), bg.green(), bg.blue(), 0)
        fade.setColorAt(0, top)
        fade.setColorAt(1, bg)
        painter.fillRect(QRectF(0, h * 0.55, w, h * 0.45), fade)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 右上角小按钮
        m = 10
        bw = self.clear_btn.isVisible()
        if bw:
            self.clear_btn.move(self.width() - m - self.clear_btn.width(), m)
        self.pick_btn.move(self.width() - m - self.pick_btn.width() - (bw * (self.clear_btn.width() + 4)), m)


class HomePage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__('page-home', parent)
        self.window_ref = window
        lay = self.vBoxLayout
        lay.setSpacing(10)

        # 头图横幅（顶部，渐变过渡到背景）
        self.banner = Banner()
        self.banner.setFixedHeight(240)
        self.banner.pick_btn.clicked.connect(self._pick_header)
        self.banner.clear_btn.clicked.connect(self._clear_header)
        lay.addWidget(self.banner)

        # 欢迎大字块：弹性留白撑在横幅与按钮之间，左对齐
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
        self._load_header()

    # ---------- 头图持久化 ----------

    def _load_header(self):
        from app.services import store

        path = store.load().get("headerImage", "")
        self.banner.set_image_path(path)

    def _pick_header(self):
        from PySide6.QtWidgets import QFileDialog
        from app.services import store

        path, _ = QFileDialog.getOpenFileName(self, "选择头图", "", "图片 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return
        cfg = store.load()
        cfg["headerImage"] = path
        store.save(cfg)
        self.banner.set_image_path(path)
        InfoBar.success("头图已设置", os.path.basename(path), duration=2500, parent=self)

    def _clear_header(self):
        from app.services import store

        cfg = store.load()
        cfg["headerImage"] = ""
        store.save(cfg)
        self.banner.set_image_path("")

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
        self._apply_font()
