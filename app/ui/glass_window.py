"""Fluent 无边框对话框：接管标题栏，毛玻璃材质与主窗口同一套。

项目里的大弹窗（档案详情这类）以前用裸 QDialog：macOS 上是系统灰标题栏，
Windows 上是系统蓝边框，和主窗口的无边框 + Fluent 按钮完全不是一套观感。

这里不重写一套材质实现——Windows 走 `materials.apply_windows_effect`，
macOS 走 `mac_effect.enable_acrylic`，与主窗口、账号网页窗口完全一致。
窗口底色用 `page.window_background`：有材质时让出底色，没材质时铺纯色。
"""

from __future__ import annotations

import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QVBoxLayout
from qfluentwidgets.window.fluent_window import MSFluentTitleBar
from qframelesswindow import FramelessDialog

from app.ui.page import window_background


def title_bar_height() -> int:
    """标题栏高度：macOS 的红绿灯更高，Windows 的窗口按钮矮一截。"""
    return 46 if sys.platform == "darwin" else 38


class GlassDialog(FramelessDialog):
    """Fluent 无边框对话框基类：标题栏、底色、材质都按主窗口的规矩来。"""

    def __init__(self, title: str, parent=None, material: str = "none"):
        super().__init__(parent)
        self.setTitleBar(MSFluentTitleBar(self))
        bar = self.titleBar
        bar.setFixedHeight(title_bar_height())
        bar.iconLabel.hide()
        bar.titleLabel.setText(title)
        if sys.platform == "darwin":
            # macOS 用系统红黄绿：它们浮在左上角，标题要让开这一段；
            # 自绘的三个按钮必须收起来，否则会和系统按钮并排出现两套
            if hasattr(self, "setSystemTitleBarButtonVisible"):
                self.setSystemTitleBarButtonVisible(True)
            for btn in (bar.minBtn, bar.maxBtn, bar.closeBtn):
                btn.hide()
            bar.titleLabel.setContentsMargins(72, 0, 0, 0)
        else:
            bar.minBtn.hide()
            bar.maxBtn.hide()
        self._material = material or "none"
        self._translucent = False
        self._material_applied = False

        # 标题栏是浮在客户区上方的子控件（qframelesswindow 不占布局），
        # 内容必须自己让出这一条高度，否则第一行文字会跟标题叠在一起
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, title_bar_height(), 0, 0)
        self.body.setSpacing(0)

    # ---- 材质 ----

    def apply_material(self, material: str) -> None:
        """按主窗口当前材质挂混合层；失败就退回纯色底，不留一个透明黑洞。"""
        from app.services.materials import (
            apply_rounded_corners, apply_windows_effect, normalize,
            windows_material_available,
        )
        from app.ui import palette as P

        material = normalize(material)
        self._material = material
        if sys.platform == "darwin":
            self._translucent = material != "none"
        elif sys.platform == "win32":
            self._translucent = windows_material_available(material)
        else:
            self._translucent = False

        if sys.platform == "darwin":
            from app.services.mac_effect import disable_acrylic, enable_acrylic

            if self._translucent:
                self._material_applied = enable_acrylic(self, material)
            else:
                disable_acrylic(self)
                self._material_applied = True
        elif sys.platform == "win32" and self._translucent:
            apply_rounded_corners(self, P.RADIUS["window"])
            self._material_applied = apply_windows_effect(self, material)
            if not self._material_applied:
                self._translucent = False
        else:
            self._material_applied = True
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        if not self._material_applied:
            # 混合层要等原生窗口就绪：第一拍挂不上就再试一次，和主窗口同样的节奏
            from PyQt5.QtCore import QTimer

            self.apply_material(self._material)
            if not self._material_applied and self._material != "none":
                QTimer.singleShot(150, lambda: self.apply_material(self._material))

    def paintEvent(self, e):
        """有材质时让出底色；没有材质时铺与主窗口相同的纯色。"""
        super().paintEvent(e)
        light, dark = window_background(self._translucent)
        color = dark if _is_dark() else light
        if color.alpha() <= 0:
            return
        p = QPainter(self)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRect(self.rect())


def _is_dark() -> bool:
    try:
        from qfluentwidgets import isDarkTheme

        return bool(isDarkTheme())
    except Exception:
        return True
