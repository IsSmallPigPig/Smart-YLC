"""启动时拉取公告：后台线程走 GitHub 代理，拿到内容后再决定要不要弹窗。

和检查更新分开：更新检查直连、失败会发系统通知；公告是可选内容，代理全挂
也只记一行日志，不能挡住启动，更不能因为网络问题弹一个错误框给用户。
"""

from __future__ import annotations

from PyQt5.QtCore import QThread, QTimer, pyqtSignal as Signal

from app.services import announcements

_WORKERS: list[QThread] = []


class FetchWorker(QThread):
    loaded = Signal(list)   # list[Announcement]
    failed = Signal(str)

    def run(self):
        try:
            self.loaded.emit(announcements.fetch_announcements())
        except Exception as e:      # 拉取是尽力而为，失败原因交给调用方记日志
            self.failed.emit(str(e))


def check_on_login(parent=None, delay_ms: int = 2500):
    """启动后延迟拉取一次。同一窗口重复调用不会起第二个请求。"""
    if parent is not None and getattr(parent, "_announcement_check_started", False):
        return
    if parent is not None:
        parent._announcement_check_started = True

    worker = FetchWorker(parent)
    _WORKERS.append(worker)
    worker.finished.connect(
        lambda: _WORKERS.remove(worker) if worker in _WORKERS else None)

    def _loaded(items):
        from app.ui.announcement_dialog import show_pending

        show_pending(parent, items)

    def _failed(message):
        from app.services.app_log import warning

        warning("公告拉取失败：%s", message)

    worker.loaded.connect(_loaded)
    worker.failed.connect(_failed)
    QTimer.singleShot(delay_ms, worker.start)
