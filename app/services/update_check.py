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
    """下载更新包：从指定网关开始，失败自动换下一个，最后一个是直连。

    start_gateway 传网关 key（None = 用设置里选的默认）。跳过与起点重复的项，
    保证每个网关最多试一次；换网关时发 fallback 信号说明上一个为什么不行。
    """

    progress = Signal(int)
    fallback = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, info: updater.UpdateInfo, start_gateway: str | None = None,
                 use_proxy: bool | None = None, parent=None):
        super().__init__(parent)
        self.info = info
        if use_proxy is not None:  # 兼容旧调用：True=默认站，False=直连
            start_gateway = updater.DEFAULT_GATEWAY if use_proxy else "direct"
        self.start_gateway = start_gateway or updater.DEFAULT_GATEWAY

    def _chain(self) -> list[dict]:
        gws = list(updater.GATEWAYS)
        try:
            i = [g["key"] for g in gws].index(self.start_gateway)
        except ValueError:
            return gws
        return gws[i:] + gws[:i]

    def run(self):
        errors = []
        for idx, gw in enumerate(self._chain()):
            try:
                path = updater.download(self.info, gw, progress=self.progress.emit)
            except updater.UpdateError as e:
                errors.append(str(e))
                if idx + 1 < len(self._chain()):
                    self.fallback.emit(str(e))
                continue
            except Exception as e:
                self.failed.emit(f"下载异常：{e}")
                return
            self.done.emit(path)
            return
        self.failed.emit("；".join(errors[-2:]) or "下载失败")


# ---------------------------------------------------------------- 配置

def auto_check_enabled() -> bool:
    return bool(store.load().get("autoCheckUpdate", updater.DEFAULT_AUTO_CHECK))


def set_auto_check(enabled: bool):
    store.update(lambda cfg: cfg.__setitem__("autoCheckUpdate", bool(enabled)))


def gateway_mode() -> str:
    """当前选择的下载网关 key；非法或旧值回退到默认站。"""
    mode = store.load().get("updateProxy") or updater.DEFAULT_GATEWAY
    if mode == "ghproxy":       # 旧版本存过 "ghproxy"，映射到 gh-proxy
        return updater.DEFAULT_GATEWAY
    return mode if updater.gateway_by_key(mode) else updater.DEFAULT_GATEWAY


def set_gateway_mode(mode: str):
    store.update(lambda cfg: cfg.__setitem__("updateProxy", mode))


def record(status: str, detail: str = ""):
    store.update(lambda cfg: cfg.__setitem__("lastUpdateCheck", {
        "at": datetime.now().isoformat(timespec="minutes"),
        "status": status,
        "detail": detail,
        "version": updater.__version__,
    }))


def _notify_failure(title: str, message: str):
    """静默流程只在失败时打扰用户；成功留给安装结束后的重启提醒。"""
    from app.services.notify import send

    send(title, message[:180])


def _start_silent_install(path: str, version: str = "") -> bool:
    from app.services import silent_update

    try:
        silent_update.start(path)
    except Exception as e:
        record("error", f"启动静默安装失败：{e}")
        _notify_failure("更新未完成", f"更新包已下载，但无法静默安装：{e}")
        return False
    record("installing", version)
    return True


def last_check_text() -> str:
    c = store.load().get("lastUpdateCheck") or {}
    if not c.get("at"):
        return "尚未检查"
    when = str(c["at"])[5:16].replace("T", " ")
    status = c.get("status", "")
    if status == "latest":
        tail = "已是最新版本"
    elif status == "update":
        tail = f"发现新版本 {c.get('detail', '')}"
    elif status == "installing":
        tail = f"正在静默安装 {c.get('detail', '')}"
    elif status == "error":
        tail = f"检查失败：{c.get('detail', '')}"
    else:
        tail = status
    return f"上次检查 {when}，{tail}"


_WORKERS: list[QThread] = []


def check_on_startup(delay_ms: int = 2500, parent=None):
    """启动后延迟检查；发现新版本直接静默下载并安装，不弹下载或安装窗口。

    parent 只为兼容既有调用保留：成功路径不再往主窗口挂信息条，失败才发系统通知。
    """
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

        downloader = DownloadWorker(info, gateway_mode())
        _WORKERS.append(downloader)
        downloader.finished.connect(
            lambda: _WORKERS.remove(downloader) if downloader in _WORKERS else None)

        def _downloaded(path: str):
            _start_silent_install(path, info.version)

        def _download_failed(message: str):
            record("error", f"后台下载失败：{message[:150]}")
            _notify_failure("自动更新未完成", message)

        downloader.done.connect(_downloaded)
        downloader.failed.connect(_download_failed)
        downloader.start()

    def _failed(message: str):
        record("error", message[:180])

    worker.found.connect(_found)
    worker.failed.connect(_failed)
    QTimer.singleShot(delay_ms, worker.start)
