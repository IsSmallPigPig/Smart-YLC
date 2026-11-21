"""开机自动检查更新（主窗口与托盘后台共用）。

检查只走直连，结果写入配置：发现新版本再发系统通知；已是最新或检查失败
不打扰用户，但会记录时间与原因，系统配置的「关于」里能看到上次结果。
下载走镜像源设置：自动（测速后选最快）、指定某个站、自定义站址。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from PyQt5.QtCore import QThread, QTimer, pyqtSignal as Signal

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
    """下载更新包：按镜像源设置排出尝试顺序，失败自动换下一个站。

    自动模式先测速再排队（十分钟内的测速结果直接复用），指定站点则它排第一，
    无论哪种，直连 GitHub 都留在序列里兜底。换站时发 fallback 说明上一个
    为什么不行，每个站最多试一次。
    """

    progress = Signal(int)
    fallback = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, info: updater.UpdateInfo, start_mode: str | None = None,
                 use_proxy: bool | None = None, parent=None):
        super().__init__(parent)
        self.info = info
        if use_proxy is not None:  # 兼容旧调用：True=自动，False=直连
            start_mode = updater.AUTO if use_proxy else "direct"
        self.start_mode = start_mode or mirror_mode()
        self.custom_prefix = custom_mirror()

    def _chain(self) -> list[dict]:
        try:
            order = updater.resolve_order(self.start_mode, self.custom_prefix)
        except Exception:      # 测速环节出错不该让下载直接失败，按清单顺序试
            order = list(updater.GATEWAYS)
        return order or list(updater.GATEWAYS)

    def run(self):
        errors = []
        chain = self._chain()
        for idx, gw in enumerate(chain):
            try:
                path = updater.download(self.info, gw, progress=self.progress.emit)
            except updater.UpdateError as e:
                errors.append(str(e))
                if idx + 1 < len(chain):
                    self.fallback.emit(str(e))
                continue
            except Exception as e:
                self.failed.emit(f"下载异常：{e}")
                return
            self.done.emit(path)
            return
        self.failed.emit("；".join(errors[-2:]) or "下载失败")


class MirrorProbeWorker(QThread):
    """并发测速全部镜像站，逐条把结果发回界面。

    单站最多等 PROBE_TIMEOUT 秒，二十多个站并发测，整轮不超过几秒；
    界面按收到的顺序回填"（845 ms）/（超时）/（不可用）"，不阻塞主线程。
    """

    measured = Signal(str, int)      # 站 key, 延迟毫秒
    unavailable = Signal(str, str)   # 站 key, 原因
    finished_all = Signal()

    def __init__(self, targets=None, parent=None):
        super().__init__(parent)
        self._targets = list(targets if targets is not None else updater.GATEWAYS)

    def run(self):
        try:
            ctx = updater._ssl_context()
        except Exception:      # 证书读不出来时交给逐个请求去报错
            ctx = None
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(updater.probe_mirror, gw, ctx): gw
                       for gw in self._targets}
            for future in as_completed(futures):
                gw = futures[future]
                try:
                    ms = future.result()
                except Exception as e:      # noqa: BLE001 - 一律记成不可用
                    reason = str(e) or "不可用"
                    updater.remember_probe(gw["key"], None, reason)
                    self.unavailable.emit(gw["key"], reason)
                else:
                    updater.remember_probe(gw["key"], ms)
                    self.measured.emit(gw["key"], ms)
        self.finished_all.emit()


# ---------------------------------------------------------------- 配置

def auto_check_enabled() -> bool:
    return bool(store.load().get("autoCheckUpdate", updater.DEFAULT_AUTO_CHECK))


def set_auto_check(enabled: bool):
    store.update(lambda cfg: cfg.__setitem__("autoCheckUpdate", bool(enabled)))


def mirror_mode() -> str:
    """当前镜像源选择：auto / 某个站的 key / custom；非法或旧值回退到自动。"""
    raw = str(store.load().get("updateProxy") or updater.DEFAULT_MODE)
    value = updater.LEGACY_KEYS.get(raw, raw)
    if value in (updater.AUTO, updater.CUSTOM):
        return value
    return value if updater.gateway_by_key(value) else updater.DEFAULT_MODE


def set_mirror_mode(value: str):
    store.update(lambda cfg: cfg.__setitem__("updateProxy", value))


def custom_mirror() -> str:
    """自定义镜像站址（已规范化）；没填过返回空串。"""
    return updater.normalize_prefix(str(store.load().get("updateCustomMirror") or ""))


def set_custom_mirror(prefix: str):
    value = updater.normalize_prefix(prefix)
    store.update(lambda cfg: cfg.__setitem__("updateCustomMirror", value))


def record(status: str, detail: str = ""):
    if status == "error":
        from app.services.app_log import error

        error("更新检查失败：%s", detail)
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
    elif status == "ready":
        tail = f"更新包 {c.get('detail', '')} 已就绪，等待重启"
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

        downloader = DownloadWorker(info, mirror_mode())
        _WORKERS.append(downloader)
        downloader.finished.connect(
            lambda: _WORKERS.remove(downloader) if downloader in _WORKERS else None)

        def _downloaded(path: str):
            # 下载完不自动安装退出：留给用户确认重启。主窗口有就弹常驻提示，
            # 没有（托盘后台）就只记状态，下次启动主窗口时再提示。
            from app.services import silent_update

            silent_update.save_pending_installer(path, info.version)
            record("ready", info.version)
            show = getattr(parent, "show_update_ready", None)
            if callable(show):
                show(path, info.version)

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
