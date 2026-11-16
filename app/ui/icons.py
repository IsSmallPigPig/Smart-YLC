"""首页卡片图标：Microsoft 官方 Fluent UI System Icons 的 color 图标（MIT）。

素材取自 microsoft/fluentui-system-icons 仓库的 `*_24_color.svg`：彩色渐变
实心图形，不是线条描边，也不是给单色图标染色。SVG 自带渐变与配色，直接
按原色渲染，深浅主题下都不需要额外处理。

打包时 app/assets 整个目录都进了可执行文件（见 release.yml 的 --add-data），
新增图标不需要改打包脚本。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtCore import QRectF
from PySide6.QtGui import QIcon, QPainter, QPixmap

from app.ui.hidpi import hidpi_canvas


def icons_dir() -> Path:
    """图标目录：源码里是 app/assets/icons，打包后在可执行文件旁边。

    资源根目录由 brand.assets_dir() 统一判断（PyInstaller / Nuitka / 源码三种落点），
    这里只管拼 icons 这一层。"""
    from app.ui.brand import assets_dir

    return assets_dir() / "icons"


def icon_path(name: str) -> Path:
    return icons_dir() / f"{name}.svg"


def native_pixmap(name: str, size: int = 32) -> QPixmap:
    """按 SVG 原始配色渲染，不做任何染色或字符串替换。"""
    path = icon_path(name)
    if not path.is_file():
        return QPixmap()

    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer

    try:
        data = path.read_bytes()
    except OSError:
        return QPixmap()
    renderer = QSvgRenderer(QByteArray(data))
    if not renderer.isValid():
        return QPixmap()

    def _paint(painter: QPainter) -> None:
        renderer.render(painter, QRectF(0, 0, size, size))

    return hidpi_canvas(size, _paint)


def native_icon(name: str, size: int = 32) -> QIcon:
    """同 native_pixmap，返回 QIcon。"""
    icon = QIcon()
    pm = native_pixmap(name, size)
    if not pm.isNull():
        icon.addPixmap(pm)
    return icon
