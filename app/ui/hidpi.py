"""HiDPI：把手工画的位图按屏幕缩放因子栅格化。

Qt6 会自己把控件、字体、布局按 devicePixelRatio 放大，但 QPixmap 是我们手画的：
按逻辑像素建画布，在 2× 屏上就等于贴了一张 1× 图，显示时被放大，边缘发虚。
统一规则——画布按「逻辑尺寸 × dpr」建物理像素，再 setDevicePixelRatio，
逻辑尺寸不变、像素密度跟上屏幕，QIcon 与 QLabel 都不必改调用方式。
"""

from __future__ import annotations

from typing import Callable

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QGuiApplication, QImage, QPainter, QPixmap


def ui_dpr() -> float:
    """取屏幕缩放因子；多屏取最大值，保证在最清晰的那块屏上不糊。"""
    app = QGuiApplication.instance()
    if app is None:
        return 1.0
    screens = app.screens()
    if not screens:
        return 1.0
    return max(float(s.devicePixelRatio()) for s in screens)


def scale_to(pm: QPixmap, size: int) -> QPixmap:
    """把位图缩到逻辑尺寸 size，并按当前 dpr 标记，避免在 2× 屏上二次插值。"""
    dpr = ui_dpr()
    px = max(1, round(size * dpr))
    out = pm.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
    out.setDevicePixelRatio(dpr)
    return out


def hidpi_canvas(size: int, paint: Callable[[QPainter], None]) -> QPixmap:
    """逻辑尺寸 size 的位图，paint 里按逻辑坐标画，物理像素自动 ×dpr。

    必须这么绕：QPainter 画在带 devicePixelRatio 的 QPixmap 上时坐标仍是
    物理像素（不像画在控件上会自动缩放），直接按逻辑坐标画就会只占左上角
    1/dpr² 一块——首页卡片图标在 2× 屏被判成 1/4 大小就是这么来的。
    先画进 QImage（纯物理像素、无 DPR 概念），再把 dpr 标上去。
    """
    dpr = ui_dpr()
    px = max(1, round(size * dpr))
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(dpr, dpr)          # 之后一律用逻辑坐标
    paint(painter)
    painter.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm
