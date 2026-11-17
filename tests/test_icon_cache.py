"""图标缓存：拖动/滚动时每个 paintEvent 都会取图，重复栅格化是纯浪费。

缓存键必须带 dpr——同一逻辑尺寸在不同缩放屏上物理像素不同，复用低分辨率
那张会糊。测试用 monkeypatch 换 dpr，验证这一点。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def app():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_native_pixmap_caches_by_name_size_dpr(app, monkeypatch):
    from app.ui import icons

    icons._render.cache_clear()
    monkeypatch.setattr(icons, "ui_dpr", lambda: 1.0)
    p1 = icons.native_pixmap("unread", 32)
    p2 = icons.native_pixmap("unread", 32)
    assert p1 is p2
    assert icons._render.cache_info().misses == 1

    # 换尺寸、换 dpr 都要重新渲染，不能拿错的分辨率复用
    icons.native_pixmap("unread", 48)
    assert icons._render.cache_info().misses == 2
    monkeypatch.setattr(icons, "ui_dpr", lambda: 2.0)
    p3 = icons.native_pixmap("unread", 32)
    # 不同 dpr 是不同缓存键，必须重新渲染，不能复用 1x 那张
    assert p3 is not p1
    assert icons._render.cache_info().misses == 3


def test_app_icon_is_single_cached_instance(app):
    from app.ui import brand

    brand._app_icon.cache_clear()
    brand._app_pixmap.cache_clear()
    misses_before = brand._app_pixmap.cache_info().misses
    i1 = brand.app_icon()
    misses_after_first = brand._app_pixmap.cache_info().misses
    i2 = brand.app_icon()
    assert i1 is i2
    assert len(i1.availableSizes()) == 5
    # 第二次调用连底层位图都不碰（app_icon 自身就缓存了）
    assert brand._app_pixmap.cache_info().misses == misses_after_first
    assert misses_after_first > misses_before  # 第一次确实栅格化了 5 个尺寸
