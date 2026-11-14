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

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap


def assets_dir() -> Path:
    """资源目录：源码里是 app/assets，打包后在临时目录的 assets 下。"""
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen) / "assets"
    return Path(__file__).resolve().parent.parent / "assets"


def icon_path(name: str) -> Path:
    return assets_dir() / name


def app_pixmap(size: int = 256) -> QPixmap:
    """应用图标位图：优先用 PNG，其次渲染 SVG。"""
    png = icon_path("smart-ylc@512.png")
    if png.is_file():
        pm = QPixmap(str(png))
        if not pm.isNull():
            return pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    svg = icon_path("smart-ylc-icon.svg")
    if svg.is_file():
        from PySide6.QtSvg import QSvgRenderer

        renderer = QSvgRenderer(str(svg))
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        renderer.render(painter)
        painter.end()
        return pm
    return QPixmap()


def app_icon() -> QIcon:
    """窗口与托盘图标。"""
    icon = QIcon()
    for size in (16, 32, 64, 128, 256):
        icon.addPixmap(app_pixmap(size))
    return icon


def mark_pixmap(size: int = 64) -> QPixmap:
    """透明底的团徽标记：欢迎页、标题栏这类地方用。"""
    svg = icon_path("smart-ylc-mark.svg")
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    if not svg.is_file():
        return pm
    try:
        from PySide6.QtSvg import QSvgRenderer

        renderer = QSvgRenderer(str(svg))
        painter = QPainter(pm)
        renderer.render(painter)
        painter.end()
    except Exception:
        pass
    return pm
