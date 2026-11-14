"""fluent 风格弹窗（非模态异步：show + 回调，绝不 exec 阻塞）。

两代教训：
1. borderless key-window 补丁卡死——Qt 的 QCocoaNSWindow 自管 key window，
   官方 borderless 弹窗在 macOS 本来就能点击，任何 pyobjc 改 styleMask 都会
   与 Qt 的 flags 状态机打架（详见 git 历史）。
2. exec() 的嵌套模态事件循环在 mac 上与主窗口的定时器/动画互相干扰，表现为
   弹窗点不动、界面假死。所有弹窗一律 show() 非模态打开，结果经回调续跑——
   等效"在别处异步执行"，主循环全程不阻塞。
"""

from __future__ import annotations

from qfluentwidgets import MessageBox, MessageBoxBase


class FluentMessageBox(MessageBox):
    def done(self, code: int) -> None:
        """跳过 MaskDialogBase 渐出动画：QGraphicsOpacityEffect finished 在部分
        环境不触发会让弹窗永远关不掉。"""
        from PySide6.QtWidgets import QDialog

        QDialog.done(self, code)


class FluentPanelDialog(MessageBoxBase):
    """fluent 面板弹层：往 self.viewLayout 里放自定义内容（表格/树等）。"""

    def done(self, code: int) -> None:
        from PySide6.QtWidgets import QDialog

        QDialog.done(self, code)


def confirm(parent, title: str, text: str, yes: str = "确定", cancel: str = "取消",
            on_result=None) -> FluentMessageBox:
    """非模态确认弹窗：show() 打开、立即返回弹窗实例。

    结果经 on_result(bool) 回调（True=点了 yes）。收口用 QDialog.finished 信号——
    按钮点击、Esc、代码调用 accept/reject 全部统一经过它，不会漏；回调执行后
    弹窗自清理。不传 on_result 时弹窗纯提示用，点按钮即关。"""
    from PySide6.QtWidgets import QDialog

    w = FluentMessageBox(title, text, parent)
    w.yesButton.setText(yes)
    w.cancelButton.setText(cancel)

    def _finish(code: int):
        w.deleteLater()
        if on_result:
            on_result(code == QDialog.Accepted)

    w.finished.connect(_finish)
    w.show()
    return w
