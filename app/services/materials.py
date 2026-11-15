"""窗口背景材质：配置里只保留 none / acrylic / mica 三种，旧值自动映射。

acrylic = Acrylic 毛玻璃；mica = Windows 11 的 Mica（macOS 用 UnderWindowBackground
近似）。旧的 sidebar / hud 选项已收编为 acrylic，读取旧配置时自动归一。
"""

from __future__ import annotations

import sys

MATERIAL_KEYS = ("none", "acrylic", "mica")
LEGACY_MATERIALS = {"sidebar": "acrylic", "hud": "acrylic"}


def normalize(material: str | None) -> str:
    """任意历史取值 → none/acrylic/mica；未知值按关闭处理，不静默自造材质。"""
    m = LEGACY_MATERIALS.get((material or "").strip(), (material or "").strip())
    return m if m in MATERIAL_KEYS else "none"


def preferred(cfg: dict) -> str:
    """配置里该用哪种背景材质；从没设置过时默认 Mica。

    只在「旧配置里明确关掉过」时保持关闭（`translucent: false`），
    其余情况一律按 Mica 起：新装与老配置都直接拿到最好看的那档。"""
    raw = cfg.get("translucentMaterial")
    if raw is not None:
        return normalize(raw)
    if cfg.get("translucent") is False:
        return "none"
    return "mica"


def win11_build() -> int:
    """Windows 内部版本号；非 Windows 返回 0。"""
    if sys.platform != "win32":
        return 0
    try:
        return sys.getwindowsversion().build
    except Exception:
        return 0


def win11_mica_available() -> bool:
    """Mica 只在 Windows 11（build ≥ 22000）可用。"""
    return win11_build() >= 22000


# DWM 的窗口圆角偏好（Win11 起才认这个属性）
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


def apply_rounded_corners(window) -> bool:
    """让无边框窗口也拿到系统圆角与投影（Windows 11）。

    无边框窗口不会被 DWM 自动切圆角，界面里所有圆角卡片就顶在一张方角窗户上。
    这里把窗口的圆角偏好设成 ROUND，并由 DWM 补一层窗口投影。
    macOS 的窗口原本就是圆角；Windows 10 没有这个接口，返回 False。"""
    if win11_build() < 22000:
        return False
    try:
        from ctypes import byref, c_int, sizeof

        from qframelesswindow.windows.window_effect import WindowsWindowEffect
    except Exception:
        return False
    try:
        hwnd = int(window.winId())
        if hwnd == 0:
            return False
        effect = getattr(window, "_dwm_effect", None)
        if effect is None:
            effect = WindowsWindowEffect(window)
            window._dwm_effect = effect
        pref = c_int(DWMWCP_ROUND)
        effect.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, byref(pref), sizeof(pref))
        effect.addShadowEffect(hwnd)
        return True
    except Exception:
        return False


def apply_windows_effect(window, material: str) -> bool:
    """Windows：acrylic/mica 都走 Fluent 官方的 Mica 接口（Win11+ 才生效）。

    失败（旧系统、接口缺失）返回 False，由调用方回退纯色底。"""
    if sys.platform != "win32" or not win11_mica_available():
        return False
    try:
        window.setMicaEffectEnabled(material != "none")
        return True
    except Exception:
        return False
