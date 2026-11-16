"""品牌图标：窗口、托盘、欢迎页、主页与打包图标都取这里。

资源放在 app/assets/：
- smart-ylc@512.png   完整应用图标（圆角矩形白底，所有效果都收在边界内），窗口与托盘用
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
from PySide6.QtGui import QIcon, QImage, QPainter, QPainterPath, QPixmap

from app.ui.hidpi import hidpi_canvas, scale_to


def assets_dir() -> Path:
    """资源目录：源码里是 app/assets，打包后在可执行文件旁边。

    落点按优先级：PyInstaller 的 sys._MEIPASS/assets → 可执行文件旁的 assets →
    源码里 app/ui 的上一级。后几项作兜底，正常打包只走第一项。"""
    candidates = []
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        candidates.append(Path(frozen) / "assets")
    exe_dir = Path(sys.executable).resolve().parent
    candidates += [exe_dir / "app/assets", exe_dir / "assets"]
    candidates.append(Path(__file__).resolve().parent.parent / "assets")
    for path in candidates:
        if (path / "smart-ylc@512.png").is_file():
            return path
    return candidates[-1]


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
    """只要圆角矩形本身，裁掉四周留白。

    方块在 icon.svg 的 viewBox 里占 (4, 4, 92, 92)，外面那一圈是 4% 的透明边距；
    app_pixmap 是完整图标（带边距），放进正文当 logo 时那圈留白会让方块
    缩进去，左缘和同一列的文字对不上。

    优先用矢量按目标尺寸栅格化：位图缩到 64 再由 2× 屏放大，边缘一圈是插值糊影。
    """
    svg = icon_path("smart-ylc-icon.svg")
    if svg.is_file():
        from PySide6.QtSvg import QSvgRenderer

        renderer = QSvgRenderer(str(svg))
        if renderer.isValid():
            renderer.setViewBox(QRectF(4, 4, 92, 92))
            # 图标本身已经把内容裁进圆角矩形了，这里再按同一个圆角裁一次：
            # 以后往 svg 里加效果时，正文 logo 也不会在深色背景上渗出一圈白雾
            radius = 22 / 92 * size

            def paint(p: QPainter):
                path = QPainterPath()
                path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
                p.setClipPath(path)
                renderer.render(p, QRectF(0, 0, size, size))

            return hidpi_canvas(size, paint)

    # 没有矢量资源时回退到位图裁剪
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
