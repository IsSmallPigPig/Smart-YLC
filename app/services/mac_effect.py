"""macOS 窗口毛玻璃（Mica 式混合）：NSVisualEffectView 置于窗口内容之下，
blending mode = behind-window。开启时窗口背景透明露出混合层，关闭时恢复纯色。

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


def enable_acrylic(window, material: str = "acrylic") -> None:
    """在窗口下方插入混合层；已开启同材质时无副作用，换材质时重建。"""
    if sys.platform != "darwin":
        return
    key = material if material in MATERIALS else "acrylic"
    if getattr(window, "_acrylic_view", None) is not None:
        if getattr(window, "_acrylic_material", None) == key:
            return
        disable_acrylic(window)
    import Cocoa
    from qframelesswindow.mac.window_effect import QMacCocoaViewContainer

    from qframelesswindow.utils.mac_utils import getNSWindow

    view = Cocoa.NSVisualEffectView.alloc().initWithFrame_(
        Cocoa.NSMakeRect(0, 0, window.width(), window.height()))
    view.setAutoresizingMask_(Cocoa.NSViewWidthSizable | Cocoa.NSViewHeightSizable)
    view.setState_(Cocoa.NSVisualEffectStateActive)
    view.setMaterial_(getattr(Cocoa, MATERIALS[key]))
    view.setBlendingMode_(Cocoa.NSVisualEffectBlendingModeBehindWindow)

    ns_window = getNSWindow(window.winId())
    content = ns_window.contentView()
    container = QMacCocoaViewContainer(0, window)
    content.addSubview_positioned_relativeTo_(view, Cocoa.NSWindowBelow, container)

    window._acrylic_view = view
    window._acrylic_container = container
    window._acrylic_material = key


def disable_acrylic(window) -> None:
    view = getattr(window, "_acrylic_view", None)
    if view is None:
        return
    view.removeFromSuperview()
    window._acrylic_view = None
    window._acrylic_container = None
    window._acrylic_material = None
