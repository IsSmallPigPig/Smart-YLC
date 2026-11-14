"""fluent 风格模态确认弹窗（修复 mac 鼠标失效）。

qfluentwidgets 的 MessageBox（MaskDialogBase）用纯 Qt FramelessWindowHint 实现，
对应 macOS 的 borderless NSWindow——系统默认不允许 borderless 窗口成为
key window，真实鼠标点击全部无效，模态锁同时卡住主窗口（程序化点击正常）。

本封装保留 MessageBox 的完整样式与动画，仅在窗口创建后把 NSWindow 的
styleMask 补回 Titled + FullSizeContentView（配合透明标题栏与隐藏标题文字，
外观不变），对话框即可成为 key window、鼠标正常点击。
"""

from __future__ import annotations

import sys

from qfluentwidgets import MessageBox

if sys.platform == "darwin":
    import Cocoa

    from qframelesswindow.utils.mac_utils import getNSWindow


class FluentMessageBox(MessageBox):
    def __init__(self, title: str, content: str, parent=None):
        super().__init__(title, content, parent)
        if sys.platform == "darwin":
            ns = getNSWindow(self.winId())
            ns.setStyleMask_(
                ns.styleMask()
                | Cocoa.NSWindowStyleMaskTitled
                | Cocoa.NSWindowStyleMaskFullSizeContentView
            )
            ns.setTitlebarAppearsTransparent_(True)
            ns.setTitleVisibility_(Cocoa.NSWindowTitleHidden)


def confirm(parent, title: str, text: str, yes: str = "确定", cancel: str = "取消") -> bool:
    """双按钮确认弹窗，返回是否点了 yes。"""
    w = FluentMessageBox(title, text, parent)
    w.yesButton.setText(yes)
    w.cancelButton.setText(cancel)
    return w.exec() == 1  # QDialog.Accepted
