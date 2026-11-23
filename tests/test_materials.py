"""窗口材质：Windows 上 acrylic 与 mica 是两条不同的接口，不能混作一谈。

Win11 两档各有系统接口；Win10 的 WCA 亚克力实测出黑块与发灰，2026-11-23 起
一律不挂、交还纯色底。这里锁住分档与「Win10 不启用」这条边界。
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

    def DwmSetWindowAttribute(self, hwnd, attr, value, size):
        self.calls.append(("dwm", int(attr)))

    def DwmExtendFrameIntoClientArea(self, hwnd, margins):
        self.calls.append(("extend", int(hwnd)))


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
    """Win10 两档都不可用；Win11 上 Mica 22000 起、Acrylic 22H2 起。"""
    for build, acrylic, mica in [
        (7601, False, False),     # Win7
        (17134, False, False),    # Win10 1803：旧亚克力已停用
        (19045, False, False),    # Win10 22H2
        (22000, True, True),      # Win11 21H2：Mica + WCA 亚克力
        (22621, True, True),      # Win11 22H2：系统背板
    ]:
        win32(build)
        assert materials.windows_material_available("acrylic") is acrylic, build
        assert materials.windows_material_available("mica") is mica, build
        assert materials.windows_material_available("none") is False, build


def test_win10_applies_nothing(win32):
    """Win10 两档都不挂效果，并撤掉可能残留的模糊，交给纯色底。"""
    for material in ("acrylic", "mica"):
        window, effect = win32(19045)
        assert materials.apply_windows_effect(window, material) is False, material
        assert not any(c[0] in ("acrylic", "blur-on") for c in effect.calls), material
        assert ("blur-off", 42) in effect.calls, material
        assert ("mica", True) not in window.calls, material


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
    assert not any(c[0] in ("acrylic", "blur-on") for c in effect.calls)


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


def test_acrylic_on_win11_uses_system_backdrop(win32):
    """Win11 22H2 之后 WCA_ACRYLIC 已失效：必须走 DWMSBT_TRANSIENTWINDOW。

    旧通路在深色模式下只剩一片纯色（看着像坏掉），浅色模式因为 tint 近似白色
    干脆看不出效果——这就是「深色异常、浅色无效果」的根因。
    """
    window, effect = win32(22621)
    assert materials.apply_windows_effect(window, "acrylic") is True
    assert ("mica", False) in window.calls       # 先摘掉 Fluent 的 Mica，免得被顶掉
    assert ("extend", 42) in effect.calls         # 背板要铺满客户区
    assert ("dwm", materials.DWMWA_SYSTEMBACKDROP_TYPE) in effect.calls
    assert not any(c[0] == "acrylic" for c in effect.calls), "旧通路不该再被调用"


def test_acrylic_on_win11_21h2_keeps_wca_path(win32):
    """21H2 没有系统背板，但 WCA 亚克力那时还能用：别顺手删掉这条。"""
    window, effect = win32(22000)
    assert materials.apply_windows_effect(window, "acrylic") is True
    assert ("blur-on", 42) in effect.calls
    assert any(c[0] == "acrylic" for c in effect.calls)
    assert ("dwm", materials.DWMWA_SYSTEMBACKDROP_TYPE) not in effect.calls


def test_preferred_keeps_blur_off_on_win10(win32):
    """Win10 新装默认给 none：选了不会有任何变化，就别让界面半透明发黑。"""
    win32(19045)
    assert materials.preferred({}) == "none"
    win32(22621)
    assert materials.preferred({}) == "mica"
    assert materials.preferred({"translucent": False}) == "none"
    assert materials.preferred({"translucentMaterial": "acrylic"}) == "acrylic"


def test_hint_explains_win10_has_no_blur(win32):
    """Win10 的说明要直说系统没有这个能力，而不是让用户去查具体版本号。"""
    win32(19045)
    hint = materials.material_hint("mica")
    assert "Windows 10" in hint and "Windows 11" in hint


def test_none_clears_system_backdrop_on_win11(win32):
    """关材质要把系统背板重置成 AUTO，否则窗口仍然是半透明的。"""
    window, effect = win32(22621)
    assert materials.apply_windows_effect(window, "none") is False
    assert ("dwm", materials.DWMWA_SYSTEMBACKDROP_TYPE) in effect.calls


def test_material_hint_no_longer_hides_dark(win32, monkeypatch):
    """深色不再当作「不启用」：旧文案来自会刷白的旧通路，现在已不走那条。"""
    monkeypatch.setattr(materials, "_is_dark", lambda: True)
    win32(22621)
    hint = materials.material_hint("mica")
    assert "不启用" not in hint
    assert "Acrylic" in hint          # 要实时模糊得改选另一档


def test_material_hint_reports_system_transparency_off(win32, monkeypatch):
    """系统关掉透明效果时，说明要指向那个开关，而不是让用户以为选错了。"""
    win32(22621)
    monkeypatch.setattr(materials, "system_transparency_enabled", lambda: False)
    hint = materials.material_hint("mica")
    assert "透明效果" in hint and "个性化" in hint


def test_transparency_probe_is_true_off_windows():
    """非 Windows 没有这个概念；探测要返回 True，不能顺手拦掉 macOS 的材质。"""
    assert materials.system_transparency_enabled() is True
