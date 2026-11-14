"""macOS 窗口毛玻璃：按 qframelesswindow 官方 MacWindowEffect.setAcrylicEffect
的默认参数挂 NSVisualEffectView，只把材质做成可切换。

材质、状态、混合模式、alpha 全部沿用官方默认（alpha 不设，即 1.0）。
额外只做一件事：开启时把 NSWindow 底色清成透明、关闭时还原——QNSWindow
不会自己清底色，不透明底色会让 behind-window 混合层只采到纯色。
"""

from __future__ import annotations

import sys

MATERIALS = {
    "acrylic": "NSVisualEffectMaterialPopover",
    "sidebar": "NSVisualEffectMaterialSidebar",
    "hud": "NSVisualEffectMaterialHUDWindow",  # 注意大写：HUD 全大写
}


def _content_view(window):
    from qframelesswindow.utils.mac_utils import getNSWindow

    ns = getNSWindow(window.winId())
    return ns, (ns.contentView() if ns is not None else None)


def enable_acrylic(window, material: str = "acrylic") -> bool:
    """按官方默认参数插入混合层；窗口未就绪时返回 False，由 showEvent 重试。"""
    if sys.platform != "darwin":
        return False
    key = material if material in MATERIALS else "acrylic"
    view = getattr(window, "_acrylic_view", None)
    if view is not None:
        if getattr(window, "_acrylic_material", None) == key:
            return True
        disable_acrylic(window)

    import Cocoa
    from qframelesswindow.mac.window_effect import QMacCocoaViewContainer

    ns_window, content = _content_view(window)
    if content is None:
        return False
    ns_window.setBackgroundColor_(Cocoa.NSColor.clearColor())

    # 官方 MacWindowEffect.setAcrylicEffect 的默认组合；不调用 setAlphaValue_，
    # 半透明的效果层会让背后的内容清晰透出，看起来是清玻璃而不是毛玻璃。
    frame = Cocoa.NSMakeRect(0, 0, window.width(), window.height())
    view = Cocoa.NSVisualEffectView.new()
    view.setFrame_(frame)
    view.setAutoresizingMask_(Cocoa.NSViewWidthSizable | Cocoa.NSViewHeightSizable)
    view.setState_(Cocoa.NSVisualEffectStateActive)
    view.setMaterial_(getattr(Cocoa, MATERIALS[key]))
    view.setBlendingMode_(Cocoa.NSVisualEffectBlendingModeBehindWindow)

    container = QMacCocoaViewContainer(0, window)
    content.addSubview_positioned_relativeTo_(view, Cocoa.NSWindowBelow, container)

    window._acrylic_view = view
    window._acrylic_container = container
    window._acrylic_material = key
    return True


def disable_acrylic(window) -> None:
    view = getattr(window, "_acrylic_view", None)
    if view is None:
        return
    view.removeFromSuperview()
    window._acrylic_view = None
    window._acrylic_container = None
    window._acrylic_material = None

    import Cocoa

    ns_window, _ = _content_view(window)
    if ns_window is not None:
        ns_window.setBackgroundColor_(Cocoa.NSColor.windowBackgroundColor())
