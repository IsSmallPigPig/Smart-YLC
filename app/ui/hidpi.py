"""HiDPI：把手工画的位图按屏幕缩放因子栅格化。

Qt6 会自己把控件、字体、布局按 devicePixelRatio 放大，但 QPixmap 是我们手画的：
按逻辑像素建画布，在 2× 屏上就等于贴了一张 1× 图，显示时被放大，边缘发虚。
统一规则——画布按「逻辑尺寸 × dpr」建物理像素，再 setDevicePixelRatio，
逻辑尺寸不变、像素密度跟上屏幕，QIcon 与 QLabel 都不必改调用方式。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPixmap


def ui_dpr() -> float:
    """取屏幕缩放因子；多屏取最大值，保证在最清晰的那块屏上不糊。"""
    app = QGuiApplication.instance()
    if app is None:
        return 1.0
    screens = app.screens()
    if not screens:
        return 1.0
    return max(float(s.devicePixelRatio()) for s in screens)


def canvas_pixmap(size: int) -> QPixmap:
    """透明画布：物理像素 size×dpr，逻辑尺寸仍是 size。"""
    dpr = ui_dpr()
    px = max(1, round(size * dpr))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    return pm


def scale_to(pm: QPixmap, size: int) -> QPixmap:
    """把位图缩到逻辑尺寸 size，并按当前 dpr 标记，避免在 2× 屏上二次插值。"""
    dpr = ui_dpr()
    px = max(1, round(size * dpr))
    out = pm.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
    out.setDevicePixelRatio(dpr)
    return out
