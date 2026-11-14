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


def win11_mica_available() -> bool:
    """Mica 只在 Windows 11（build ≥ 22000）可用。"""
    if sys.platform != "win32":
        return False
    try:
        return sys.getwindowsversion().build >= 22000
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
