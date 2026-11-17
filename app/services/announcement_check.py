"""启动时拉取公告：后台线程走 GitHub 代理，拿到内容后再决定要不要弹窗。

和检查更新分开：更新检查直连、失败会发系统通知；公告是可选内容，代理全挂
也只记一行日志，不能挡住启动，更不能因为网络问题弹一个错误框给用户。

用 Python daemon 线程而不是 QThread：v1.0.1 把 QThread 挂在 QRunnable/父窗口下，
退出时父对象析构运行中的线程会触发 Qt 原生 abort——绕开 sys.excepthook，既没有
崩溃窗口，日志里也没有异常。daemon 线程不参与 Qt 对象树，结果由主线程 QTimer
轮询后显示，窗口随时能关，不会在退出路径上把进程带走。
"""

from __future__ import annotations

import threading

from PyQt5.QtCore import QTimer

from app.services import announcements, app_log

_THREADS: list[threading.Thread] = []
_TIMERS: list[QTimer] = []


def check_on_login(parent=None, delay_ms: int = 2500):
    """启动后延迟拉取一次。同一窗口重复调用不会起第二个请求。"""
    if parent is not None and getattr(parent, "_announcement_check_started", False):
        return
    if parent is not None:
        parent._announcement_check_started = True

    result: dict = {"items": [], "error": ""}
    done = threading.Event()
    worker: list[threading.Thread | None] = [None]
    # 主窗口可能在网络请求期间被关掉；弹窗不能挂到已析构的对象上
    alive = parent if parent is not None else None

    def _fetch():
        try:
            result["items"] = announcements.fetch_announcements()
        except Exception as e:      # 拉取是尽力而为，失败原因交给调用方记日志
            result["error"] = str(e)
        finally:
            done.set()

    def _drop_timer(timer: QTimer):
        if timer in _TIMERS:
            _TIMERS.remove(timer)
        timer.stop()
        timer.deleteLater()

    def _settle(timer: QTimer):
        _drop_timer(timer)
        _THREADS[:] = [t for t in _THREADS if t is not worker[0]]
        if result["error"]:
            app_log.warning("公告拉取失败：%s", result["error"])
            return
        app_log.info("公告拉取完成，共 %d 条", len(result["items"]))
        if alive is not None:
            try:
                import sip

                if sip.isdeleted(alive):
                    return
            except ImportError:
                pass
        from app.ui.announcement_dialog import show_pending

        show_pending(parent, result["items"])

    def _poll(timer: QTimer):
        if not done.is_set():
            return
        _settle(timer)

    def _start(starter: QTimer):
        _drop_timer(starter)
        app_log.info("开始拉取启动公告")
        thread = threading.Thread(target=_fetch, name="announcement-fetch", daemon=True)
        worker[0] = thread
        _THREADS.append(thread)
        thread.start()

        watcher = QTimer()
        watcher.setInterval(100)
        watcher.timeout.connect(lambda: _poll(watcher))
        _TIMERS.append(watcher)
        watcher.start()

    starter = QTimer()
    starter.setSingleShot(True)
    starter.timeout.connect(lambda: _start(starter))
    _TIMERS.append(starter)
    starter.start(delay_ms)
