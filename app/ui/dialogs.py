"""mac 可靠的模态弹窗。

qfluentwidgets 的遮罩弹窗（MessageBox）基于 qframelesswindow 的 FramelessDialog：
在 macOS 上对话框窗口不成为 key window 时鼠标点击全部无效，模态锁同时卡住主窗口
（程序化点击正常、真实鼠标点不动）。因此本项目的确认类弹窗统一用 Qt 原生
QMessageBox——走系统原生模态面板，不存在 key window 问题。
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox


def confirm(parent, title: str, text: str, yes: str = "确定", cancel: str = "取消") -> bool:
    """双按钮确认弹窗，返回是否点了 yes。"""
    b = QMessageBox(parent)
    b.setWindowTitle(title)
    b.setText(text)
    yes_btn = b.addButton(yes, QMessageBox.ButtonRole.AcceptRole)
    b.addButton(cancel, QMessageBox.ButtonRole.RejectRole)
    b.exec()
    return b.clickedButton() is yes_btn
