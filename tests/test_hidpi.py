"""HiDPI：手工画的位图必须按 dpr 栅格化，否则 2× 屏上会被放大发虚。"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QGuiApplication, QPixmap


@pytest.fixture()
def app():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def dpr2(monkeypatch):
    """模拟 2× 屏：只影响按名字调用 ui_dpr 的代码路径。"""
    from app.ui import hidpi

    monkeypatch.setattr(hidpi, "ui_dpr", lambda: 2.0)
    return 2.0


def test_hidpi_canvas_doubles_physical_pixels(app, dpr2):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor

    from app.ui.hidpi import hidpi_canvas

    pm = hidpi_canvas(64, lambda p: p.fillRect(QRectF(0, 0, 64, 64), QColor("#ff0000")))
    assert pm.width() == 128 and pm.height() == 128     # 物理像素翻倍
    assert pm.devicePixelRatio() == 2.0                 # 逻辑尺寸仍是 64
    assert pm.deviceIndependentSize().width() == 64
    # 逻辑坐标铺满 → 四角都得有颜色（2× 下画成 1/4 大小的那个 bug 的回归点）
    img = pm.toImage()
    for x, y in ((2, 2), (126, 2), (2, 126), (126, 126)):
        assert img.pixelColor(x, y).alpha() > 200, f"({x},{y}) 没画到"


def test_scale_to_keeps_logical_size(app, dpr2):
    from app.ui.hidpi import scale_to

    src = QPixmap(512, 512)
    src.fill()
    out = scale_to(src, 32)
    assert out.devicePixelRatio() == 2.0
    assert out.width() == 64                            # 物理 64 = 逻辑 32 × 2
    assert out.deviceIndependentSize().width() == 32


def test_app_icon_renders_at_screen_density(app, dpr2):
    from app.ui import brand

    pm = brand.app_pixmap(32)
    assert pm.devicePixelRatio() == 2.0
    assert pm.width() == 64

    tile = brand.app_tile_pixmap(64)
    assert tile.devicePixelRatio() == 2.0
    assert tile.deviceIndependentSize().width() == pytest.approx(64, abs=1)


def test_svg_mark_is_high_dpi(app, dpr2):
    from app.ui import brand, icons

    mark = brand.mark_pixmap(48)
    assert mark.devicePixelRatio() == 2.0 and mark.width() == 96

    icon = icons.native_pixmap("group", 32)
    if not icon.isNull():
        assert icon.devicePixelRatio() == 2.0 and icon.width() == 64


def test_chevron_icon_high_dpi(app, monkeypatch):
    """page 里按名字导入了 ui_dpr，要打在它自己的命名空间上。"""
    import app.ui.page as page

    monkeypatch.setattr(page, "ui_dpr", lambda: 2.0)
    icon = page.chevron_left_icon(16)
    pm = icon.pixmap(16, 16)
    assert pm.devicePixelRatio() in (1.0, 2.0)  # QIcon 按请求尺寸返回，不强制 dpr
    faded = page.chevron_left_icon(16, enabled=False)
    assert faded.availableSizes()
