"""fluent 风格模态弹窗。

官方 MessageBox / MessageBoxBase 在 macOS 上可直接使用：Qt 的 QCocoaNSWindow
自行管理 key window（borderless 也能成为 key），主窗口同为 FramelessWindowHint
而鼠标完全正常即为佐证。

此前加过的 styleMask 补丁（show 后补 Titled + makeKeyWindow）反而导致卡死：
Qt 会按自己的 window flags 重设 styleMask，双方来回覆盖、窗口状态不一致，
模态期间鼠标事件投递失败。已全部移除。

done() 仍跳过 MaskDialogBase 的渐出动画：QGraphicsOpacityEffect 动画在部分
环境不触发 finished，模态循环会永远不退出（关闭卡死）。跳过只损失关闭渐出，
不影响输入。
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog
from qfluentwidgets import MessageBox, MessageBoxBase


class FluentMessageBox(MessageBox):
    def done(self, code: int) -> None:
        QDialog.done(self, code)


class FluentPanelDialog(MessageBoxBase):
    """fluent 面板弹层：往 self.viewLayout 里放自定义内容（表格/树等）。"""

    def done(self, code: int) -> None:
        QDialog.done(self, code)


def confirm(parent, title: str, text: str, yes: str = "确定", cancel: str = "取消") -> bool:
    """双按钮确认弹窗，返回是否点了 yes。"""
    w = FluentMessageBox(title, text, parent)
    w.yesButton.setText(yes)
    w.cancelButton.setText(cancel)
    return w.exec() == 1  # QDialog.Accepted
