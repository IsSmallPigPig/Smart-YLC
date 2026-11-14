"""macOS 窗口毛玻璃（Mica 式混合）：NSVisualEffectView 置于窗口内容之下，
blending mode = behind-window。开启时窗口背景透明露出混合层，关闭时恢复纯色。
"""

from __future__ import annotations

import sys

MATERIAL = "NSVisualEffectMaterialPopover"  # 与 qframelesswindow AcrylicWindow 同款


def enable_acrylic(window) -> None:
    """在窗口下方插入混合层；已开启时重复调用无副作用。"""
    if sys.platform != "darwin" or getattr(window, "_acrylic_view", None) is not None:
        return
    import Cocoa
    from qframelesswindow.mac.window_effect import QMacCocoaViewContainer

    from qframelesswindow.utils.mac_utils import getNSWindow

    view = Cocoa.NSVisualEffectView.alloc().initWithFrame_(
        Cocoa.NSMakeRect(0, 0, window.width(), window.height()))
    view.setAutoresizingMask_(Cocoa.NSViewWidthSizable | Cocoa.NSViewHeightSizable)
    view.setState_(Cocoa.NSVisualEffectStateActive)
    view.setMaterial_(Cocoa.NSVisualEffectMaterialPopover)
    view.setBlendingMode_(Cocoa.NSVisualEffectBlendingModeBehindWindow)

    ns_window = getNSWindow(window.winId())
    content = ns_window.contentView()
    container = QMacCocoaViewContainer(0, window)
    content.addSubview_positioned_relativeTo_(view, Cocoa.NSWindowBelow, container)

    window._acrylic_view = view
    window._acrylic_container = container


def disable_acrylic(window) -> None:
    view = getattr(window, "_acrylic_view", None)
    if view is None:
        return
    view.removeFromSuperview()
    window._acrylic_view = None
    window._acrylic_container = None
