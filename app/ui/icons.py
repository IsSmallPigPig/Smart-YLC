"""首页卡片图标：Microsoft 官方 Fluent UI System Icons 的 color 图标（MIT）。

素材取自 microsoft/fluentui-system-icons 仓库的 `*_24_color.svg`：彩色渐变
实心图形，不是线条描边，也不是给单色图标染色。SVG 自带渐变与配色，直接
按原色渲染，深浅主题下都不需要额外处理。

打包时 app/assets 整个目录都进了可执行文件（见 release.yml 的 --add-data），
新增图标不需要改打包脚本。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PyQt5.QtCore import QRectF
from PyQt5.QtGui import QIcon, QPainter, QPixmap

from app.ui.hidpi import hidpi_canvas, ui_dpr


def icons_dir() -> Path:
    """图标目录：源码里是 app/assets/icons，打包后在可执行文件旁边。

    资源根目录由 brand.assets_dir() 统一判断（PyInstaller / 源码落点），
    这里只管拼 icons 这一层。"""
    from app.ui.brand import assets_dir

    return assets_dir() / "icons"


def icon_path(name: str) -> Path:
    return icons_dir() / f"{name}.svg"


@lru_cache(maxsize=128)
def _render(name: str, size: int, dpr: float) -> QPixmap:
    """真正的渲染。缓存键带 dpr：同一 size 在不同缩放屏上物理像素不同，
    复用低分辨率那张会糊。dpr 由调用方传入，QPixmap 本身不可哈希。"""
    path = icon_path(name)
    if not path.is_file():
        return QPixmap()

    from PyQt5.QtCore import QByteArray
    from PyQt5.QtSvg import QSvgRenderer

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


def native_pixmap(name: str, size: int = 32) -> QPixmap:
    """按 SVG 原始配色渲染，不做任何染色或字符串替换。

    彩色图标控件在 paintEvent 里调用，拖动/滚动时每帧都会进来；每次重新读盘、
    解析 SVG、栅格化是纯粹的浪费（实测单次 0.33ms，六个图标一帧就是 2ms）。
    按 (名称, 尺寸, 缩放) 缓存，命中时零磁盘 IO。
    """
    return _render(name, size, ui_dpr())


def native_icon(name: str, size: int = 32) -> QIcon:
    """同 native_pixmap，返回 QIcon。"""
    icon = QIcon()
    pm = native_pixmap(name, size)
    if not pm.isNull():
        icon.addPixmap(pm)
    return icon
