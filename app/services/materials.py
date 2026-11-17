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


def apply_dwm_round_corners(window) -> bool:
    """只设 Windows 11 的 DWM 圆角偏好，不碰阴影。

    和阴影分开是有原因的：`addShadowEffect` 内部是
    `DwmExtendFrameIntoClientArea(-1,-1,-1,-1)`，它会让 DWM 重算窗口 frame 并
    发一次 `WM_SIZE`。在 `resizeEvent` 里顺手调它，等于「resize → 改 frame →
    又 resize」，会自激成死循环，CPU 直接打满、栈深到崩溃。
    所以圆角偏好（纯属性写入，不改变窗口几何）可以随尺寸重设，
    阴影只能在外面单独调一次。
    """
    if win11_build() < WINDOWS_MICA_BUILD:
        return False
    try:
        from ctypes import byref, c_int, sizeof

        effect = _window_effect(window)
        hwnd = int(window.winId())
        if hwnd == 0:
            return False
        pref = c_int(DWMWCP_ROUND)
        effect.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, byref(pref), sizeof(pref))
        return True
    except Exception:
        return False


def add_dwm_shadow(window) -> bool:
    """给无边框窗口补一层 DWM 投影。

    只能在窗口几何稳定之后调（showEvent / 显式尺寸变更后），
    绝不要放在 `resizeEvent` 的同步路径里——见 apply_dwm_round_corners 的说明。
    """
    if win11_build() < WINDOWS_MICA_BUILD:
        return False
    try:
        effect = _window_effect(window)
        hwnd = int(window.winId())
        if hwnd == 0:
            return False
        effect.addShadowEffect(hwnd)
        return True
    except Exception:
        return False


def apply_mask_corners(window, radius: int = 10) -> bool:
    """用 setMask 把窗口切成圆角（没有系统圆角接口时用）。

    遮罩按位图走，边缘没有抗锯齿，半径给小一点更干净。

    两个限制要知道：
    * Windows 上 `setMask` 会调 `SetWindowRgn`，那同样会让窗口重算区域并触发
      重绘甚至尺寸确认——所以它**不能**放在 resizeEvent 的同步路径里反复调，
      只能由调用方去抖后调用，并且尺寸没变时不要重复调（见 set_corners）。
    * 它和「DWM 把客户区扩展成玻璃边框」是冲突的：一个要求裁掉圆角外的像素，
      另一个要求那块像素交给系统合成。所以开了亚克力/Mica 的 Windows 窗口
      不要走这条路径。
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


# 上次切圆角时的 (id(窗口), 宽, 高, 半径)：尺寸没变就不重复调系统接口
_CORNER_STATE: dict[int, tuple[int, int, int]] = {}


def set_corners(window, radius: int = 10, force: bool = False,
                platform: str | None = None, build: int | None = None) -> bool:
    """统一入口：按平台切窗口圆角，并保证幂等。

    调用方可以在 resizeEvent 里放心调——尺寸和半径都没变时这里直接返回，
    不碰任何系统接口。真正变化时也只做一次，不会自激。

    `force=True` 时忽略幂等检查（例如材质切换后窗口 frame 被系统重设过，
    需要重新贴一次圆角偏好）。

    各平台策略：
    * macOS：无边框窗口由系统切圆角，什么都不做。
    * Windows 11：只写 DWM 圆角偏好（纯属性，不改窗口几何）。**绝不 setMask**，
      那会和「客户区扩展成玻璃边框」打架。
    * Windows 10：没有系统接口，退到 setMask。
    * Linux：不进 setMask。Qt 在 Wayland 上不支持窗口遮罩，X11 上遮罩会把阴影
      一起裁掉；而主流桌面环境对无边框窗口本来就有自己的圆角与阴影，
      再切一次反而更差。

    `platform` / `build` 只为测试注入：这些分支在 macOS 上跑不到，闪退恰恰出在
    Windows 那两条，必须能在开发机上覆盖。正常调用留空即取当前系统。
    """
    platform = sys.platform if platform is None else platform
    build = win11_build() if build is None else build

    key = id(window)
    size = (window.width(), window.height(), radius)
    if not force and _CORNER_STATE.get(key) == size:
        return True
    _CORNER_STATE[key] = size

    if platform == "darwin":
        return True
    if platform != "win32":
        return False
    if build >= WINDOWS_MICA_BUILD:
        return apply_dwm_round_corners(window)
    return apply_mask_corners(window, radius)


def clear_corners(window) -> None:
    """撤掉遮罩式圆角，并把幂等缓存清掉。

    窗口要切成「不透明纯色 + 直角」或者要交给 DWM 合成圆角时必须先撤遮罩：
    mask 与「DWM 把客户区扩展成玻璃边框」是互斥的，两者同时生效会在圆角处
    切掉一块系统合成的像素。
    """
    _CORNER_STATE.pop(id(window), None)
    try:
        window.clearMask()
    except Exception:
        pass


def apply_rounded_corners(window, radius: int = 10) -> bool:
    """切圆角 + 补 Windows 11 投影。

    「切圆角」走幂等的 set_corners；「补投影」只在 Win11 上做，且因此
    这个函数**不能**出现在 resizeEvent 的同步路径里——`addShadowEffect` 内部是
    `DwmExtendFrameIntoClientArea(-1,-1,-1,-1)`，会改变窗口 frame 并触发新的
    WM_SIZE，在缩放回调里调它就成了自激循环（CPU 打满、栈深到崩溃）。
    现有调用点都在 showEvent / set_material 之后，属于「几何已定」的时机。
    """
    if sys.platform == "darwin":
        return True
    ok = set_corners(window, radius)
    if sys.platform == "win32" and win11_build() >= WINDOWS_MICA_BUILD:
        add_dwm_shadow(window)
    return ok


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
