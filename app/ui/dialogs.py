"""fluent 风格模态弹窗（修复 mac 鼠标失效）。

qfluentwidgets 的 MessageBox / MessageBoxBase（MaskDialogBase）用纯 Qt
FramelessWindowHint 实现，对应 macOS 的 borderless NSWindow——系统默认不允许
borderless 窗口成为 key window，真实鼠标点击全部无效，模态锁同时卡住主窗口
（程序化点击正常）。

修法：保留完整 fluent 样式与动画，仅在窗口创建后与每次 showEvent 后把 NSWindow
的 styleMask 补回 Titled + FullSizeContentView（配合透明标题栏与隐藏标题文字，
外观不变），并确保它成为 key window，鼠标即可正常点击。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QDialog
from qfluentwidgets import MessageBox, MessageBoxBase

if sys.platform == "darwin":
    import Cocoa

    from qframelesswindow.utils.mac_utils import getNSWindow


class _MacKeyWindowMixin:
    """让 borderless 弹窗在 mac 上能成为 key window（真实鼠标可点）。

    __init__ 时补 mask（保证窗口属性正确）；显示完成后（QTimer.singleShot(0)
    延迟到 show 处理结束）再补一次 mask 并确保成为 key window——不能在
    showEvent 里同步做，否则模态 exec 的显示流程会被干扰。"""

    def _apply_mask(self) -> None:
        """补回 Titled + FullSizeContentView（配合透明标题栏，外观不变）。"""
        if sys.platform != "darwin":
            return
        ns = getNSWindow(self.winId())
        ns.setStyleMask_(
            ns.styleMask()
            | Cocoa.NSWindowStyleMaskTitled
            | Cocoa.NSWindowStyleMaskFullSizeContentView
        )
        ns.setTitlebarAppearsTransparent_(True)
        ns.setTitleVisibility_(Cocoa.NSWindowTitleHidden)

    def _become_key_window(self) -> None:
        if sys.platform != "darwin":
            return
        ns = getNSWindow(self.winId())
        if not ns.isKeyWindow():
            Cocoa.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            ns.makeKeyWindow()

    def _apply_key_window_fix(self) -> None:
        self._apply_mask()
        self._become_key_window()

    def showEvent(self, e: QShowEvent) -> None:
        super().showEvent(e)
        QTimer.singleShot(0, self._apply_key_window_fix)

    def done(self, code: int) -> None:
        """跳过 MaskDialogBase 的渐出动画：QGraphicsOpacityEffect 动画在 mac 上
        不结束，弹窗会永远关不掉（与本库此前的「删除卡死」同源）。"""
        QDialog.done(self, code)


class FluentMessageBox(_MacKeyWindowMixin, MessageBox):
    def __init__(self, title: str, content: str, parent=None):
        super().__init__(title, content, parent)
        self._apply_mask()


class FluentPanelDialog(_MacKeyWindowMixin, MessageBoxBase):
    """fluent 面板弹层：往 self.viewLayout 里放自定义内容（表格/树等）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._apply_mask()


def confirm(parent, title: str, text: str, yes: str = "确定", cancel: str = "取消") -> bool:
    """双按钮确认弹窗，返回是否点了 yes。"""
    w = FluentMessageBox(title, text, parent)
    w.yesButton.setText(yes)
    w.cancelButton.setText(cancel)
    return w.exec() == 1  # QDialog.Accepted
