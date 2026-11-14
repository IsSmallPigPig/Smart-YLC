"""开机自动检查更新（主窗口与托盘后台共用）。

检查只走直连，结果写入配置：发现新版本再发系统通知；已是最新或检查失败
不打扰用户，但会记录时间与原因，系统配置的「关于」里能看到上次结果。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QThread, QTimer, Signal

from app.services import store, updater


class CheckWorker(QThread):
    found = Signal(object)  # updater.UpdateInfo | None
    failed = Signal(str)

    def run(self):
        try:
            self.found.emit(updater.check_update())
        except Exception as e:
            self.failed.emit(str(e))


class DownloadWorker(QThread):
    """下载更新包。ghproxy 加速失败时明确报告原因并自动回退直连。"""

    progress = Signal(int)
    fallback = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, info: updater.UpdateInfo, use_proxy: bool, parent=None):
        super().__init__(parent)
        self.info = info
        self.use_proxy = use_proxy

    def run(self):
        try:
            path = updater.download(self.info, self.use_proxy, progress=self.progress.emit)
        except updater.UpdateError as e:
            if not self.use_proxy:
                self.failed.emit(str(e))
                return
            self.fallback.emit(str(e))
            try:
                path = updater.download(self.info, False, progress=self.progress.emit)
            except updater.UpdateError as e2:
                self.failed.emit(str(e2))
                return
        except Exception as e:
            self.failed.emit(f"下载异常：{e}")
            return
        self.done.emit(path)


# ---------------------------------------------------------------- 配置

def auto_check_enabled() -> bool:
    return bool(store.load().get("autoCheckUpdate", updater.DEFAULT_AUTO_CHECK))


def set_auto_check(enabled: bool):
    store.update(lambda cfg: cfg.__setitem__("autoCheckUpdate", bool(enabled)))


def proxy_mode() -> str:
    mode = store.load().get("updateProxy") or updater.DEFAULT_PROXY
    return mode if mode in ("ghproxy", "direct") else updater.DEFAULT_PROXY


def set_proxy_mode(mode: str):
    store.update(lambda cfg: cfg.__setitem__("updateProxy", mode))


def record(status: str, detail: str = ""):
    store.update(lambda cfg: cfg.__setitem__("lastUpdateCheck", {
        "at": datetime.now().isoformat(timespec="minutes"),
        "status": status,
        "detail": detail,
        "version": updater.__version__,
    }))


def last_check_text() -> str:
    c = store.load().get("lastUpdateCheck") or {}
    if not c.get("at"):
        return "尚未检查过更新"
    when = str(c["at"])[5:16].replace("T", " ")
    status = c.get("status", "")
    if status == "latest":
        tail = "已是最新版本"
    elif status == "update":
        tail = f"发现新版本 {c.get('detail', '')}"
    elif status == "error":
        tail = f"检查失败：{c.get('detail', '')}"
    else:
        tail = status
    return f"上次检查 {when}，{tail}"


_WORKERS: list[QThread] = []


def check_on_startup(delay_ms: int = 2500, parent=None):
    """启动后延迟静默检查；parent 传主窗口时额外显示一条不抢占焦点的提示。"""
    try:
        if not auto_check_enabled():
            return
    except Exception:
        return

    worker = CheckWorker()
    _WORKERS.append(worker)
    worker.finished.connect(lambda: _WORKERS.remove(worker) if worker in _WORKERS else None)

    def _found(info):
        if info is None:
            record("latest")
            return
        record("update", info.version)
        from app.services.notify import send

        send("智慧团委发现新版本",
             f"{info.version} 已发布，打开「系统配置 - 关于」即可下载")
        if parent is not None:
            try:
                from qfluentwidgets import InfoBar

                InfoBar.info("发现新版本", f"{info.version} 已发布，可在系统配置中下载更新",
                             duration=8000, parent=parent)
            except Exception:
                pass

    def _failed(message: str):
        record("error", message[:180])

    worker.found.connect(_found)
    worker.failed.connect(_failed)
    QTimer.singleShot(delay_ms, worker.start)
