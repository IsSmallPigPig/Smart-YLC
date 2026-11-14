"""macOS 窗口毛玻璃（Mica 式混合）：NSVisualEffectView 置于窗口内容之下，
blending mode = behind-window，采样窗口背后的桌面内容。

前提：Qt 必须在原生 NSWindow 创建之前声明 WA_TranslucentBackground，否则
backing store 没有 alpha 通道、NSWindow 保持 opaque，behind-window 混合层
不会采样窗口背后的内容（见 main_window 的 _translucent_window 钩子）。
窗口创建后再调 NSWindow.setOpaque_(False) 无效，QNSWindow 会立即覆盖回来。

材质对应 Ghost Downloader 等同类应用的「窗口背景透明材质」：
- acrylic → Popover（Acrylic 式，与 qframelesswindow AcrylicWindow 同款）
- sidebar → Sidebar（更透，接近侧栏质感）
- hud     → HudWindow（更暗更沉，暗色模式下更融合）
"""

from __future__ import annotations

import sys

MATERIALS = {
    "acrylic": "NSVisualEffectMaterialPopover",
    "sidebar": "NSVisualEffectMaterialSidebar",
    "hud": "NSVisualEffectMaterialHUDWindow",  # 注意大写：HUD 全大写
}

# 材质整体透明度（NSView alpha）：Popover 材质本身偏实，压到 0.8 让壁纸
# 隐约透出来；sidebar/hud 保持材质原味（sidebar 本来就更透，hud 要沉）
MATERIAL_ALPHA = {
    "acrylic": 0.8,
    "sidebar": 1.0,
    "hud": 1.0,
}


def _ns_window(window):
    """取窗口的 NSWindow；窗口尚未 show / contentView 未就绪时返回 None。"""
    from qframelesswindow.utils.mac_utils import getNSWindow

    ns = getNSWindow(window.winId())
    return ns if ns is not None and ns.contentView() is not None else None


def enable_acrylic(window, material: str = "acrylic") -> bool:
    """在窗口内容之下插入混合层；同材质已开启时无副作用，换材质时重建。

    返回是否挂上：窗口还没 show 时返回 False，由 MainWindow.showEvent 重试。"""
    if sys.platform != "darwin":
        return False
    import Cocoa
    from qframelesswindow.mac.window_effect import QMacCocoaViewContainer

    ns_window = _ns_window(window)
    if ns_window is None:
        return False
    content = ns_window.contentView()

    key = material if material in MATERIALS else "acrylic"
    view = getattr(window, "_acrylic_view", None)
    if view is not None:
        if getattr(window, "_acrylic_material", None) == key:
            sync_frame(window)
            return True
        disable_acrylic(window)

    # behind-window 混合只能透过透明窗口底色采样桌面内容
    ns_window.setBackgroundColor_(Cocoa.NSColor.clearColor())

    view = Cocoa.NSVisualEffectView.alloc().initWithFrame_(content.bounds())
    view.setAutoresizingMask_(Cocoa.NSViewWidthSizable | Cocoa.NSViewHeightSizable)
    view.setState_(Cocoa.NSVisualEffectStateActive)
    view.setMaterial_(getattr(Cocoa, MATERIALS[key]))
    view.setBlendingMode_(Cocoa.NSVisualEffectBlendingModeBehindWindow)
    view.setAlphaValue_(MATERIAL_ALPHA.get(key, 1.0))

    # 放到 contentView 最底层：Qt 的原生子视图在其上，界面文字不会被盖住
    container = QMacCocoaViewContainer(0, window)
    content.addSubview_positioned_relativeTo_(view, Cocoa.NSWindowBelow, container)

    window._acrylic_view = view
    window._acrylic_container = container
    window._acrylic_material = key
    return True


def sync_frame(window) -> None:
    """窗口尺寸变化后对齐混合层；autoresizing 之外再兜一次，避免启动帧错位。"""
    if sys.platform != "darwin":
        return
    view = getattr(window, "_acrylic_view", None)
    if view is None:
        return
    ns_window = _ns_window(window)
    if ns_window is not None:
        view.setFrame_(ns_window.contentView().bounds())


def disable_acrylic(window) -> None:
    view = getattr(window, "_acrylic_view", None)
    if view is None:
        return
    view.removeFromSuperview()
    window._acrylic_view = None
    window._acrylic_container = None
    window._acrylic_material = None

    import Cocoa

    ns_window = _ns_window(window)
    if ns_window is not None:
        ns_window.setBackgroundColor_(Cocoa.NSColor.windowBackgroundColor())
