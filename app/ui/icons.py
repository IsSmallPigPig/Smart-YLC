"""彩色实心图标：从 app/assets/icons/ 里的 SVG 渲染成指定颜色的图标。

素材来自 Fluent UI System Icons 的 filled 变体（MIT 协议，Microsoft），
经 Iconify 分发的单色 SVG：图形本身是实心填充，不是描边线条，配色由
调用方给一个颜色即可。

fluent 自带的图标只有黑白描边两版；这里要的是首页卡片上那种彩色实心
图标，所以单独存一份 SVG 资源，用 QSvgRenderer 按颜色渲染。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap


def icons_dir() -> Path:
    """图标目录：源码里是 app/assets/icons，打包后在临时目录的 assets/icons 下。"""
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen) / "assets" / "icons"
    return Path(__file__).resolve().parent.parent / "assets" / "icons"


def icon_path(name: str) -> Path:
    return icons_dir() / f"{name}.svg"


def colored_icon(name: str, color: str = "#0067f4", size: int = 24) -> QIcon:
    """按颜色渲染一个 filled 实体图标。"""
    path = icon_path(name)
    if not path.is_file():
        return QIcon()

    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer

    # SVG 里写的是 currentColor，QSvgRenderer 不认它（渲染成空），
    # 所以先把 currentColor 换成目标色再交给渲染器。
    try:
        svg = path.read_text(encoding="utf-8")
    except OSError:
        return QIcon()
    svg = svg.replace("currentColor", QColor(color).name())

    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()

    icon = QIcon()
    icon.addPixmap(pm)
    return icon


def colored_pixmap(name: str, color: str = "#0067f4", size: int = 24) -> QPixmap:
    """同 colored_icon，但返回位图。"""
    icon = colored_icon(name, color, size)
    return icon.pixmap(QSize(size, size))
