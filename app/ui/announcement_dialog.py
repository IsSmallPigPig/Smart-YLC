"""登录公告弹窗：标题 + 编号列表 + 确定/取消，样式沿用 fluent 面板弹层。

一次拉取可能有多条未读公告，按文件顺序逐条弹。点「确定」把该条标为已读；
点「取消」只是这次不读，下次启动仍会弹。弹窗一律 show() 非模态打开——
exec() 的嵌套事件循环在本项目里会让主窗口定时器与弹窗互相卡住。
"""

from __future__ import annotations

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QFont, QTextOption
from PyQt5.QtWidgets import QDialog, QTextBrowser

from qfluentwidgets import SubtitleLabel

from app.services import announcements
from app.services.announcements import Announcement
from app.ui import palette as P
from app.ui.dialogs import FluentPanelDialog
from app.ui.page import note_label, theme_font

# 弹窗与队列都由这里持有：Qt 不会替 Python 对象续命，局部变量一出作用域就被回收
_OPEN: list[AnnouncementDialog] = []
_QUEUES: list[AnnouncementQueue] = []


class AnnouncementDialog(FluentPanelDialog):
    def __init__(self, item: Announcement, parent=None):
        super().__init__(parent)
        self.item = item
        self.widget.setMinimumWidth(520)

        title = theme_font(SubtitleLabel(item.title, self.widget),
                           P.FONT["title"], int(QFont.Weight.DemiBold))
        self.viewLayout.addWidget(title)
        if item.date:
            self.viewLayout.addWidget(note_label(f"发布于 {item.date}"))

        body = QTextBrowser(self.widget)
        body.setReadOnly(True)
        body.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        body.setPlainText("\n".join(f"{i}. {text}" for i, text in enumerate(item.items, 1)))
        theme_font(body, P.FONT["body"])
        body.setMaximumHeight(420)
        body.setStyleSheet(
            "QTextBrowser { background: transparent; border: none; padding: 0; }")
        self.viewLayout.addWidget(body)
        self.body = body

        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.finished.connect(self._release_ref)

    def _release_ref(self):
        if self in _OPEN:
            _OPEN.remove(self)
        self.deleteLater()


class AnnouncementQueue:
    """逐条显示未读公告；一条关闭后再放下一条，不会叠成一堆窗口。"""

    def __init__(self, parent, items: list[Announcement]):
        self.parent = parent
        self.items = list(items)
        self.index = 0
        self.current: AnnouncementDialog | None = None
        _QUEUES.append(self)
        self._show_next()

    def _show_next(self):
        if self.index >= len(self.items):
            self._finish()
            return
        item = self.items[self.index]
        dlg = AnnouncementDialog(item, self.parent)
        self.current = dlg
        _OPEN.append(dlg)

        def _done(code: int):
            if code == QDialog.Accepted:
                announcements.mark_seen([item.id])
            self.index += 1
            self.current = None
            # 给 fluent 的淡入/阴影特效留出收尾时间：紧接着构造下一个弹窗
            # 会在 offscreen 上撞到 Qt 图形特效的悬空指针
            QTimer.singleShot(250, self._show_next)

        dlg.finished.connect(_done)
        dlg.show()

    def _finish(self):
        if self in _QUEUES:
            _QUEUES.remove(self)
        if self.parent is not None and getattr(self.parent, "_announcement_queue", None) is self:
            self.parent._announcement_queue = None


def show_pending(parent, items: list[Announcement]) -> AnnouncementQueue | None:
    """有未读公告才弹；同一窗口只保留一条队列，重复拉取不会叠窗。"""
    if parent is not None and getattr(parent, "_announcement_queue", None) is not None:
        return None
    todo = announcements.pending(items)
    if not todo:
        return None
    queue = AnnouncementQueue(parent, todo)
    if parent is not None:
        parent._announcement_queue = queue
    return queue
