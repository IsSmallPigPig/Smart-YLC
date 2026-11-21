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

# 系统背板（Win11 22H2 起）：1=自动 2=Mica 3=亚克力(Transient) 4=Tabbed
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMSBT_AUTO = 0
DWMSBT_TRANSIENTWINDOW = 3
# 22H2（22621）起 SystemBackdrop 稳定；更早的 Win11 仍走 WCA 旧通路
WINDOWS_BACKDROP_BUILD = 22621


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


def window_corner_radius(radius: int = 10) -> int:
    """这台机器上窗口应该画多大圆角；Win10 与 Linux 保持直角。

    启动画面与主窗口接力，两边半径必须一致，否则淡出时会露出一块角。
    """
    if sys.platform == "darwin" or win11_mica_available():
        return radius
    return 0


def win10_acrylic_available() -> bool:
    """亚克力在 Windows 10 1803（build ≥ 17134）及更高版本可用。"""
    return win11_build() >= WINDOWS_ACRYLIC_BUILD


def system_transparency_enabled() -> bool:
    """系统「透明效果」总开关；关闭时 DWM 收下 backdrop 属性但什么都不画。

    「关闭透明效果」与「性能选项选最佳性能」都会把它置 0。此时窗口会拿到
    一片不透明的系统色，用户看到的是「选了材质没反应」，界面必须说明原因。
    """
    if sys.platform != "win32":
        return True
    try:
        import winreg

        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "EnableTransparency")[0])
    except Exception:
        # 读不到就按「开着」处理：这是提示用的探测，不该拦住材质本身
        return True


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
        if material != "none" and not _is_dark():
            return "浅色下不启用：透出的桌面会压住正文"
        return "macOS 用系统毛玻璃，深色模式生效"
    if sys.platform != "win32":
        return "当前系统没有可用的窗口透明接口"
    if material == "none":
        return "关闭后铺纯色底"
    if not system_transparency_enabled():
        return "系统「透明效果」已关闭，去 设置 → 个性化 → 颜色 打开后才有效果"
    if material == "mica":
        if win11_mica_available():
            return "Win11 系统背景，取桌面壁纸的色调；要实时模糊改选 Acrylic"
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


def _is_dark() -> bool:
    try:
        from qfluentwidgets import isDarkTheme

        return bool(isDarkTheme())
    except Exception:
        return True


def _margins_full():
    """DwmExtendFrameIntoClientArea 要的 MARGINS，-1 表示整窗扩展。"""
    from ctypes import Structure, c_int

    class _Margins(Structure):
        _fields_ = [("left", c_int), ("right", c_int),
                    ("top", c_int), ("bottom", c_int)]

    return _Margins(-1, -1, -1, -1)


def _set_backdrop(window, value: int) -> bool:
    """写 DWMWA_SYSTEMBACKDROP_TYPE；Win11 22H2 前不支持，返回 False。"""
    try:
        from ctypes import byref, c_int

        effect = _window_effect(window)
        hwnd = int(window.winId())
        if hwnd == 0:
            return False
        effect.DwmExtendFrameIntoClientArea(hwnd, byref(_margins_full()))
        effect.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            byref(c_int(1 if _is_dark() else 0)), 4)
        effect.DwmSetWindowAttribute(
            hwnd, DWMWA_SYSTEMBACKDROP_TYPE, byref(c_int(value)), 4)
        return True
    except Exception:
        return False


def _apply_acrylic(window) -> bool:
    """亚克力背景。Win11 22H2 起走系统背板，更早的 Windows 走 WCA 旧通路。

    为什么必须分两条：
      * Win11 22H2 之后 ACCENT_ENABLE_ACRYLICBLURBEHIND 已失效——再调用只会
        得到一片没有模糊的纯色（深色模式下看起来像坏掉的纯黑，浅色模式下
        因为 tint 接近白色，干脆看不出效果）。这正是用户报的「深色异常、
        浅色无效果」。新系统改用 DWMSBT_TRANSIENTWINDOW，由 DWM 真正合成亚克力。
      * Win10 仍然只有旧通路：blur behind + 亚克力 tint 两个一起下才有观感。

    两种情况下都先关掉 Fluent 自己的 Mica 开关，否则主题切换时
    （_onThemeChangedFinished）它会重挂系统背景，把亚克力顶掉。"""
    try:
        window.setMicaEffectEnabled(False)
    except Exception:
        pass

    if win11_build() >= WINDOWS_BACKDROP_BUILD and _set_backdrop(
            window, DWMSBT_TRANSIENTWINDOW):
        return True

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
    * Windows 11：只写 DWM 圆角偏好（纯属性，不改窗口几何）。绝不 setMask，
      那会和「客户区扩展成玻璃边框」打架。
    * Windows 10 及更早：不做圆角。遮罩没有抗锯齿，还会与透明材质、系统阴影
      互相裁切，观感比直角更差。
    * Linux：不做圆角。主流桌面环境对无边框窗口本来就有自己的圆角与阴影，
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
    return False


def clear_corners(window) -> None:
    """清掉幂等缓存与可能残留的窗口遮罩（老版本用遮罩切过圆角）。"""
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
    """撤掉三条通路：系统背板、系统背景与 DWM 模糊，否则关材质后仍透出桌面。"""
    if win11_build() >= WINDOWS_BACKDROP_BUILD:
        _set_backdrop(window, DWMSBT_AUTO)
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
