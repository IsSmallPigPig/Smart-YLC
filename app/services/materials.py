"""窗口背景材质：配置里只保留 none / acrylic / mica 三种，旧值自动映射。

两档在 Windows 上是两条不同的系统接口，不是同一个开关：
  * acrylic = ACCENT_ENABLE_ACRYLICBLURBEHIND（Win10 1803 起就有，实时模糊窗口后面）
  * mica    = DWM 系统背景（Win11 22000 起，取桌面壁纸的色调，不模糊）
Win10 没有 Mica，选 Mica 时退成亚克力，而不是干脆不生效。更老的系统（Win7/8）
两条都没有，返回 False 由调用方铺纯色底。macOS 走 mac_effect 的 NSVisualEffectView。
"""

from __future__ import annotations

import sys

MATERIAL_KEYS = ("none", "acrylic", "mica")
LEGACY_MATERIALS = {"sidebar": "acrylic", "hud": "acrylic"}

# 亚克力衬纸色，格式 RRGGBBAA（qframelesswindow 内部会反转成 DWM 要的 AABBGGRR）。
# 半透明而不是全透明：模糊本身不提供对比度，正文压不住桌面上的亮斑。
ACRYLIC_TINT_LIGHT = "F2F2F2A6"
ACRYLIC_TINT_DARK = "1E1E1EA6"

# Win10 1803 才有真正的亚克力；再早只有 Aero 模糊，观感差且不跟系统主题
WINDOWS_ACRYLIC_BUILD = 17134
WINDOWS_MICA_BUILD = 22000

# DWM 的窗口圆角偏好（Win11 起才认这个属性）
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


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
    return win11_build() >= WINDOWS_MICA_BUILD


def win10_acrylic_available() -> bool:
    """亚克力在 Windows 10 1803（build ≥ 17134）及更高版本可用。"""
    return win11_build() >= WINDOWS_ACRYLIC_BUILD


def windows_material_available(material: str) -> bool:
    """这台机器上这套材质能不能挂出效果；UI 用它决定窗口要不要让出底色。

    Mica 在 Win10 上按亚克力实现（见 apply_windows_effect），所以只要亚克力可用
    就算可用，不能一律按 Win11 判断——那会让 Win10 的用户选了材质却什么都没变。
    """
    material = normalize(material)
    if material == "none":
        return False
    if material == "mica" and win11_mica_available():
        return True
    return win10_acrylic_available()


def material_hint(material: str) -> str:
    """设置页那行说明：讲清这台机器上这个选项实际会变成什么。

    用户碰到「选了没反应」时，最需要的不是系统版本号，而是「这里退成了哪一档」。
    """
    material = normalize(material)
    if sys.platform == "darwin":
        return "macOS 用系统毛玻璃，深浅模式各自取色"
    if sys.platform != "win32":
        return "当前系统没有可用的窗口透明接口"
    if material == "none":
        return "关闭后铺纯色底"
    if material == "mica":
        if win11_mica_available():
            return "Win11 系统背景，取桌面壁纸的色调"
        if win10_acrylic_available():
            return "Mica 需 Windows 11，这台机器按 Acrylic 呈现"
        return f"需要 Windows 10 1803+（build {WINDOWS_ACRYLIC_BUILD}）"
    if win10_acrylic_available():
        return "系统亚克力，实时模糊窗口后面的内容"
    return f"Acrylic 需要 Windows 10 1803+（build {WINDOWS_ACRYLIC_BUILD}）"


def _window_effect(window):
    """取（必要时创建）qframelesswindow 的 Windows 效果对象。"""
    effect = getattr(window, "windowEffect", None)
    if effect is not None:
        return effect
    from qframelesswindow.windows.window_effect import WindowsWindowEffect

    effect = WindowsWindowEffect(window)
    window.windowEffect = effect
    return effect


def _acrylic_tint() -> str:
    try:
        from qfluentwidgets import isDarkTheme

        return ACRYLIC_TINT_DARK if isDarkTheme() else ACRYLIC_TINT_LIGHT
    except Exception:
        return ACRYLIC_TINT_LIGHT


def _apply_acrylic(window) -> bool:
    """真亚克力：先给整个客户区开 DWM 模糊，再把亚克力合成叠上去。

    只用 ACCENT_ENABLE_ACRYLICBLURBEHIND 而不开 blur behind 的话，Win10 上拿到
    的是一片纯色 tint；两个一起下才是 Fluent 那种透过窗口看后面的观感。

    同时把 Fluent 自己的 Mica 开关关掉：它在主题切换（_onThemeChangedFinished）
    时会重挂系统背景，把刚设好的亚克力顶掉。"""
    try:
        window.setMicaEffectEnabled(False)
    except Exception:
        pass
    try:
        effect = _window_effect(window)
        hwnd = int(window.winId())
        effect.enableBlurBehindWindow(hwnd)
        effect.setAcrylicEffect(hwnd, _acrylic_tint())
        return True
    except Exception:
        return False


def _apply_mica(window) -> bool:
    """Win11 系统背景：Fluent 自带那条 DWM 通路（含桌面取色与深浅模式）。

    先撤掉亚克力留下的 blur behind，否则两者叠在一起，Mica 会变成一片糊。"""
    try:
        effect = _window_effect(window)
        effect.disableBlurBehindWindow(int(window.winId()))
    except Exception:
        pass
    try:
        window.setMicaEffectEnabled(True)
        return True
    except Exception:
        return False


def apply_rounded_corners(window, radius: int = 10) -> bool:
    """让无边框窗口拿到圆角与投影，跨平台各自走最合适的那条路。

    三档实现，按系统能力从优到劣：

    * **macOS**：系统给无边框窗口切圆角（NSWindow 自带），什么都不用做。
    * **Windows 11**：DWM 有圆角偏好，设成 ROUND 并由 DWM 补窗口投影。
      半径由系统定（约 8px），传进来的 radius 不参与——这是系统级的圆角，
      和界面内自绘的卡片圆角本来就该按系统那一档走。
    * **Windows 10 / 其它**：没有系统接口，用 `setMask` 自己切。遮罩切出来的
      边缘没有抗锯齿，但总比一张方角窗户顶着一堆圆角卡片强；窗口透明材质在
      Win10 上本来就退回纯色底，边缘锯齿不明显。

    返回是否真的切了圆角（macOS 返回 True，因为系统本来就有）。
    """
    if sys.platform == "darwin":
        return True
    if win11_build() >= WINDOWS_MICA_BUILD:
        try:
            from ctypes import byref, c_int, sizeof

            effect = _window_effect(window)
            hwnd = int(window.winId())
            if hwnd == 0:
                return False
            pref = c_int(DWMWCP_ROUND)
            effect.DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, byref(pref), sizeof(pref))
            effect.addShadowEffect(hwnd)
            return True
        except Exception:
            return False
    return apply_mask_corners(window, radius)


def apply_mask_corners(window, radius: int = 10) -> bool:
    """用 setMask 把窗口切成圆角（没有系统圆角接口时用）。

    遮罩是按位图走的：边缘没有抗锯齿，半径给小一点更干净——10px 在这个窗口
    尺度上看着是「轻微倒角」，不像大圆角那样把锯齿暴露出来。

    尺寸变了要重新切，所以方法挂在窗口上、由调用方在 resizeEvent 里再喊一次。
    """
    try:
        from PyQt5.QtGui import QPainterPath, QRegion

        w, h = window.width(), window.height()
        if w <= 0 or h <= 0:
            return False
        path = QPainterPath()
        path.addRoundedRect(0, 0, w, h, radius, radius)
        window.setMask(QRegion(path.toFillPolygon().toPolygon()))
        return True
    except Exception:
        return False


def remove_windows_effect(window) -> None:
    """撤掉两条通路：系统背景与 DWM 模糊都要关，否则关材质后仍透出桌面。"""
    try:
        effect = _window_effect(window)
        hwnd = int(window.winId())
        effect.removeBackgroundEffect(hwnd)
        effect.disableBlurBehindWindow(hwnd)
    except Exception:
        pass
    try:
        window.setMicaEffectEnabled(False)
    except Exception:
        pass


def apply_windows_effect(window, material: str) -> bool:
    """Windows：按材质选接口，返回是否真的挂上了效果。

    acrylic 走 ACCENT_ENABLE_ACRYLICBLURBEHIND（Win10 1803+），mica 走 Win11 的
    DWM 系统背景；Win10 选 mica 时退成 acrylic。返回 False 表示这台机器上做不了，
    调用方要回退纯色底，别让窗口裸透明发黑。
    """
    if sys.platform != "win32":
        return False
    material = normalize(material)
    if material == "none":
        remove_windows_effect(window)
        return False
    if material == "mica" and win11_mica_available():
        if _apply_mica(window):
            return True
    if win10_acrylic_available():
        return _apply_acrylic(window)
    return False
