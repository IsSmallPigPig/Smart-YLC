"""品牌图标：窗口、托盘、欢迎页、主页与打包图标都取这里。

资源放在 app/assets/：
- smart-ylc@512.png   完整应用图标（带白色底板），窗口与托盘用
- smart-ylc-icon.svg  应用图标的矢量版
- smart-ylc-mark.svg  透明底的团徽标记，欢迎页与标题栏用
- smart-ylc.ico      Windows 打包用的多尺寸图标

打包成 exe/app 后资源在 PyInstaller 的临时目录里，所以路径要按 sys._MEIPASS 找，
不能只认源码目录。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap

from app.ui.hidpi import hidpi_canvas, scale_to


def assets_dir() -> Path:
    """资源目录：源码里是 app/assets，打包后在临时目录的 assets 下。"""
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen) / "assets"
    return Path(__file__).resolve().parent.parent / "assets"


def icon_path(name: str) -> Path:
    return assets_dir() / name


def app_pixmap(size: int = 256) -> QPixmap:
    """应用图标位图：优先用 PNG，其次渲染 SVG。

    按屏幕 dpr 栅格化（2× 屏出 2 倍像素），否则窗口/托盘图标会被放大发虚。"""
    png = icon_path("smart-ylc@512.png")
    if png.is_file():
        pm = QPixmap(str(png))
        if not pm.isNull():
            return scale_to(pm, size)
    svg = icon_path("smart-ylc-icon.svg")
    if svg.is_file():
        from PySide6.QtSvg import QSvgRenderer

        renderer = QSvgRenderer(str(svg))
        return hidpi_canvas(size, lambda p: renderer.render(p, QRectF(0, 0, size, size)))
    return QPixmap()


def app_tile_pixmap(size: int = 64) -> QPixmap:
    """只要白底方块本身，裁掉投影和四周留白。

    app_pixmap 是完整图标（带投影与边距），适合当窗口/托盘图标；放进正文当 logo 时
    那圈留白会让方块缩进去，左缘和同一列的文字对不上（512 的图左边空了 54px）。
    """
    png = icon_path("smart-ylc@512.png")
    src = QPixmap(str(png)) if png.is_file() else app_pixmap(512)
    if src.isNull():
        return src
    # 缩到 128 再找不透明区：512 逐像素太慢，回到原尺寸的误差不到 1px
    probe = src.toImage().scaled(
        128, 128, Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation
    ).convertToFormat(QImage.Format.Format_ARGB32)
    x0 = y0 = 128
    x1 = y1 = -1
    for y in range(128):
        for x in range(128):
            if (probe.pixel(x, y) >> 24) >= 240:   # 近不透明 = 白底方块本身
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x), max(y1, y)
    if x1 < 0:
        return scale_to(src, size)
    k = src.width() / 128
    crop = QRect(int(x0 * k), int(y0 * k),
                 int((x1 - x0 + 1) * k), int((y1 - y0 + 1) * k))
    return scale_to(src.copy(crop), size)


def app_icon() -> QIcon:
    """窗口与托盘图标。"""
    icon = QIcon()
    for size in (16, 32, 64, 128, 256):
        icon.addPixmap(app_pixmap(size))
    return icon


def mark_pixmap(size: int = 64) -> QPixmap:
    """透明底的团徽标记：欢迎页、标题栏这类地方用。"""
    svg = icon_path("smart-ylc-mark.svg")
    if not svg.is_file():
        return QPixmap()
    try:
        from PySide6.QtSvg import QSvgRenderer

        renderer = QSvgRenderer(str(svg))
        return hidpi_canvas(size, lambda p: renderer.render(p, QRectF(0, 0, size, size)))
    except Exception:
        return QPixmap()
