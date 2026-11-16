"""窗口材质：Windows 上 acrylic 与 mica 是两条不同的接口，不能混作一谈。

历史实现把 acrylic 也塞进 Fluent 的 Mica 开关，而那个开关在 Win10 上直接
return，结果两种材质在 Win10 上都不生效。这里锁住分档与降级规则。
"""

from __future__ import annotations

import types

import pytest

from app.services import materials


class FakeEffect:
    """记录调用顺序，代替 qframelesswindow 的 WindowsWindowEffect。"""

    def __init__(self):
        self.calls = []

    def enableBlurBehindWindow(self, hwnd):
        self.calls.append(("blur-on", int(hwnd)))

    def disableBlurBehindWindow(self, hwnd):
        self.calls.append(("blur-off", int(hwnd)))

    def setAcrylicEffect(self, hwnd, tint):
        self.calls.append(("acrylic", tint))

    def removeBackgroundEffect(self, hwnd):
        self.calls.append(("remove", int(hwnd)))

    def addShadowEffect(self, hwnd):
        self.calls.append(("shadow", int(hwnd)))

    def DwmSetWindowAttribute(self, *args):
        self.calls.append(("dwm",))


class FakeWindow:
    def __init__(self):
        self.calls = []

    def winId(self):
        return 42

    def setMicaEffectEnabled(self, enabled: bool):
        self.calls.append(("mica", enabled))

    def _normalBackgroundColor(self):  # pragma: no cover - 仅供基类方法签名对齐
        return None


@pytest.fixture
def win32(monkeypatch):
    """把模块当成跑在 Windows 上，并把 build 号交给测试指定。"""

    def setup(build: int):
        monkeypatch.setattr(materials, "sys", types.SimpleNamespace(platform="win32"))
        monkeypatch.setattr(materials, "win11_build", lambda: build)
        effect = FakeEffect()
        monkeypatch.setattr(materials, "_window_effect", lambda window: effect)
        window = FakeWindow()
        return window, effect

    return setup


def test_material_availability_by_windows_build(win32, monkeypatch):
    """Win10 上 mica 也要算可用（退成亚克力），否则用户选了材质什么都没变。"""
    for build, acrylic, mica in [
        (7601, False, False),     # Win7：两条都没有
        (10240, False, False),    # Win10 1507：还没亚克力
        (17134, True, True),      # Win10 1803：有亚克力，mica 退亚克力
        (19045, True, True),      # Win10 22H2
        (22621, True, True),      # Win11
    ]:
        win32(build)
        assert materials.windows_material_available("acrylic") is acrylic, build
        assert materials.windows_material_available("mica") is mica, build
        assert materials.windows_material_available("none") is False, build


def test_acrylic_uses_acrylic_api_not_mica(win32):
    """acrylic 走 ACCENT_ENABLE_ACRYLICBLURBEHIND，不是 Mica 开关。"""
    window, effect = win32(19045)
    assert materials.apply_windows_effect(window, "acrylic") is True
    assert ("mica", False) in window.calls          # 先关掉 Fluent 的 Mica 开关
    assert ("blur-on", 42) in effect.calls          # 客户区模糊
    tints = [c[1] for c in effect.calls if c[0] == "acrylic"]
    assert tints == [materials.ACRYLIC_TINT_LIGHT] or tints == [materials.ACRYLIC_TINT_DARK]


def test_mica_on_win10_falls_back_to_acrylic(win32):
    window, effect = win32(19045)
    assert materials.apply_windows_effect(window, "mica") is True
    assert ("mica", True) not in window.calls       # 没有 Mica 接口，不能假装开了
    assert any(c[0] == "acrylic" for c in effect.calls)


def test_mica_on_win11_clears_blur_first(win32):
    """Mica 与 blur behind 叠在一起会糊成一片，切过去要先撤模糊。"""
    window, effect = win32(22621)
    assert materials.apply_windows_effect(window, "mica") is True
    assert ("blur-off", 42) in effect.calls
    assert ("mica", True) in window.calls
    assert not any(c[0] == "acrylic" for c in effect.calls)


def test_old_windows_reports_failure(win32):
    """Win7 上挂不上效果必须明确返回 False，让调用方铺纯色底。"""
    window, effect = win32(7601)
    assert materials.apply_windows_effect(window, "acrylic") is False
    assert materials.apply_windows_effect(window, "mica") is False
    assert effect.calls == []


def test_none_removes_everything_and_reports_not_applied(win32):
    """关材质要同时撤系统背景与 DWM 模糊，并且返回 False（调用方据此铺底色）。"""
    window, effect = win32(22621)
    assert materials.apply_windows_effect(window, "none") is False
    assert ("remove", 42) in effect.calls
    assert ("blur-off", 42) in effect.calls
    assert ("mica", False) in window.calls


def test_legacy_material_values_map_to_acrylic():
    assert materials.normalize("sidebar") == "acrylic"
    assert materials.normalize("hud") == "acrylic"
    assert materials.normalize("whatever") == "none"
    assert materials.normalize(None) == "none"
