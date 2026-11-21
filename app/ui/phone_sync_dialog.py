"""手机同步弹窗：局域网直传（首选），中转作为兜底。

界面要解决的核心问题：让用户在两秒内知道「现在该怎么把手机上的 PDF 送进来」。
所以顺序是：状态 → 二维码/地址 → 收到的文件；不做没用的设置项。

局域网不可用（电脑没连网、或与手机不在同一网段）时，明确给出排查步骤，
并把云端中转作为备选说明写在这里——不假装直传可用。
"""

from __future__ import annotations

import io

from PyQt5.QtCore import (
    QMutex,
    QMutexLocker,
    QWaitCondition,
    Qt,
    QThread,
    QTimer,
    pyqtSignal as Signal,
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    StrongBodyLabel,
    ToolButton,
)

from app.services.lan_share import LanShare, Received, lan_addresses
from app.ui import palette as P
from app.ui.dialogs import FluentPanelDialog
from app.ui.page import theme_font


class _RelayWorker(QThread):
    """后台轮询中转站，把手机传来的档案拉回本地。

    联网长跑不能塞进 CallbackWorker（它的 parent 析构会等线程结束，界面会卡）。
    这里带 stop()/wait()，dialog 关闭时先停再退。周期等待用 QWaitCondition：
    QThread.wait() 是"等别的线程结束"，从自身调用属未定义行为，不能当 sleep 用。
    """

    got = Signal(object)      # receive_all 的汇总 dict
    failed = Signal(str)

    def __init__(self, config: dict, interval_ms: int = 5000, parent=None):
        super().__init__(parent)
        self._config = config
        self._interval = interval_ms
        self._mutex = QMutex()
        self._wakeup = QWaitCondition()
        self._stop = False

    def stop(self):
        # 置位并唤醒：run() 可能正睡在 _wakeup.wait 上，不等满一个周期就退。
        with QMutexLocker(self._mutex):
            self._stop = True
            self._wakeup.wakeAll()

    def _sleep_or_wake(self) -> bool:
        """等到下个周期；被 stop() 唤醒返回 False。调用前不持锁。"""
        with QMutexLocker(self._mutex):
            if self._stop:
                return False
            self._wakeup.wait(self._mutex, self._interval)
            return not self._stop

    def run(self):
        from app.services import relay_receive as relay

        cfg = self._config
        while True:
            try:
                # 不传 already：receive_all 内部 receive_pdf 会按 sha256 判重，
                # 重复文件返回 duplicate 状态，不会重复落盘
                summary = relay.receive_all(
                    cfg["server"], cfg["code"],
                    owner_key=cfg.get("owner_key", ""))
                names = summary["saved"] + summary["duplicates"]
                if names:
                    self.got.emit(summary)
            except Exception as exc:                 # noqa: BLE001 - 每轮失败不杀线程
                self.failed.emit(str(exc))
            if not self._sleep_or_wake():
                break


class PhoneSyncDialog(FluentPanelDialog):
    """手机 → 电脑的局域网接收弹窗。

    用 FluentPanelDialog（MessageBoxBase 子类）而不是顶层 QDialog：与主窗口同一套
    遮罩、圆角、居中与主题，不再出现带系统标题栏的独立深色窗口。沿用项目的全局
    约定——show() 非模态打开，绝不用 exec() 的嵌套事件循环。
    """

    file_received = Signal(str)      # 落盘路径
    _http_received = Signal(object)  # HTTP 线程 → GUI 线程的收件投递

    def __init__(self, parent=None, auto_start: bool = True):
        super().__init__(parent)
        self.widget.setMinimumWidth(480)
        # LanShare 的回调跑在 ThreadingHTTPServer 的请求线程（无 Qt 事件循环），
        # 那里建的 QTimer 永不触发；信号 emit 由 Qt 自动 queued 回 GUI 线程。
        self._http_received.connect(self._show_received)
        from app.services.cloud_backup import local_config_codes

        self.share = LanShare(on_received=self._on_received,
                              allowed_codes=local_config_codes())
        self._timer: QTimer | None = None
        self._relay_worker: _RelayWorker | None = None

        self.viewLayout.setSpacing(12)

        title = StrongBodyLabel("从手机传档案", self.widget)
        theme_font(title, P.FONT['title'], int(title.font().weight()))
        self.viewLayout.addWidget(title)

        self.state_label = BodyLabel("正在准备…", self.widget)
        self.state_label.setWordWrap(True)
        self.viewLayout.addWidget(self.state_label)

        # 二维码：手机扫一下就能打开上传页，不用手输 IP
        self.qr_label = QLabel(self.widget)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumHeight(220)
        self.viewLayout.addWidget(self.qr_label, 1)

        # 地址行：二维码扫不了时手输；两个图标按钮分别复制地址、打开同步文件夹
        addr_row = QWidget(self.widget)
        addr_lay = QHBoxLayout(addr_row)
        addr_lay.setContentsMargins(0, 0, 0, 0)
        addr_lay.setSpacing(8)
        self.addr_label = BodyLabel("—", addr_row)
        self.addr_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.copy_btn = ToolButton(FIF.COPY, addr_row)
        self.copy_btn.setToolTip("复制地址")
        self.copy_btn.clicked.connect(self._copy_address)
        self.folder_btn = ToolButton(FIF.FOLDER, addr_row)
        self.folder_btn.setToolTip("打开同步文件夹")
        self.folder_btn.clicked.connect(self._open_folder)
        addr_lay.addWidget(self.addr_label, 1)
        addr_lay.addWidget(self.copy_btn)
        addr_lay.addWidget(self.folder_btn)
        self.viewLayout.addWidget(addr_row)

        self.result_label = BodyLabel("", self.widget)
        self.result_label.setWordWrap(True)
        self.viewLayout.addWidget(self.result_label)

        # 底部复用 MessageBoxBase 的按钮区：主按钮切接收、次按钮关闭。
        # 断开 yesButton 默认的 accept——点「停止/开始接收」不能把弹窗关掉。
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._toggle)
        self.cancelButton.setText("关闭")
        # 兼容旧引用：开关按钮现在复用弹窗的 yesButton
        self.toggle_btn = self.yesButton

        # 无论点关闭、Esc 还是代码 reject，都先停掉 HTTP 服务
        self.finished.connect(self._cleanup)

        if auto_start:
            self._toggle()

    # ---- 开关 ----

    def _toggle(self):
        if self.share.running:
            self.share.stop()
            self._stop_timer()
            self._stop_relay()
            self.qr_label.clear()
            self.addr_label.setText("—")
            self.state_label.setText("已停止接收。点「开始接收」重新开始。")
            self.yesButton.setText("开始接收")
            return
        try:
            url = self.share.start()
        except OSError as exc:
            self.state_label.setText(f"无法开启：{exc}")
            return
        self.yesButton.setText("停止接收")
        if not url:
            # 没有局域网地址：有云端配置就自动走中转轮询，手机用流量也能传
            from app.services.relay_receive import local_relay_config

            relay_cfg = local_relay_config()
            if relay_cfg:
                self._start_relay(relay_cfg)
                return
            self.state_label.setText(
                "没有局域网地址，也没配云端档案。请让手机电脑连同一个 Wi-Fi，"
                "或在设置里配置云端备份作中转，再重新开始接收。")
            self.addr_label.setText("—")
            self.qr_label.clear()
            return
        self.state_label.setText(
            "手机与电脑连同一个 Wi-Fi，用 App 扫二维码配对后，"
            "自动传输手机上还没传过的 PDF。全程只在局域网内进行。")
        self.addr_label.setText(url)
        self._show_qr(url)
        self._start_timer()

    def _start_relay(self, cfg: dict):
        """无局域网时切到中转模式：后台轮询，收到的文件经 file_received 入列。"""
        self.state_label.setText(
            f"当前没有局域网地址，已切到中转模式。手机 App 用提取码 {cfg['code']} "
            "上传，电脑会自动取回，不用连同一个 Wi-Fi。")
        self.addr_label.setText(f"中转提取码：{cfg['code']}")
        self.qr_label.setText("中转模式：手机 App 里填上面的提取码即可上传")

        worker = _RelayWorker(cfg, parent=self)
        worker.got.connect(self._on_relay_got)
        worker.failed.connect(self._on_relay_failed)
        self._relay_worker = worker
        worker.start()

    def _stop_relay(self):
        if self._relay_worker is not None:
            self._relay_worker.stop()
            self._relay_worker.wait(2000)
            self._relay_worker = None

    def _on_relay_got(self, summary: dict):
        saved = summary.get("saved") or []
        if saved:
            self.result_label.setText(f"中转收到 {len(saved)} 份档案")
            for path in saved:
                self.file_received.emit(path)

    def _on_relay_failed(self, message: str):
        self.result_label.setText(f"中转接收出错：{message}")

    def _show_qr(self, url: str):
        try:
            import qrcode

            img = qrcode.make(url, box_size=6, border=2)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            pix = QPixmap()
            pix.loadFromData(buf.getvalue(), "PNG")
            self.qr_label.setPixmap(pix)
        except Exception:                    # 没装 qrcode 就退化成手输地址
            self.qr_label.setText("（未安装二维码组件，请手动输入下面的地址）")

    def _copy_address(self):
        text = self.addr_label.text()
        if text and text != "—":
            QApplication.clipboard().setText(text)
            self.result_label.setText("地址已复制，发到手机上打开即可。")

    def _open_folder(self):
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QDesktopServices

        from app.services.phone_sync import sync_dir

        QDesktopServices.openUrl(QUrl.fromLocalFile(sync_dir()))

    # ---- 收件回调 ----

    def _on_received(self, item: Received):
        # 回调在 HTTP 线程里；emit 让 Qt 把它排到 GUI 线程再更新界面。
        self._http_received.emit(item)

    def _show_received(self, item: Received):
        mark = "已收到" if item.status in ("ok", "duplicate", "need_rename") else "未接收"
        line = f"{mark}：{item.name}"
        if item.note:
            line += f"（{item.note}）"
        self.result_label.setText(line)
        if item.status in ("ok", "need_rename") and item.path:
            self.file_received.emit(item.path)

    # ---- 空闲自动停止 ----

    def _start_timer(self):
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._idle_check)
        self._timer.start(60_000)

    def _stop_timer(self):
        if self._timer is not None:
            self._timer.stop()

    def _idle_check(self):
        if self.share.expired():
            self.share.stop()
            self._stop_timer()
            self.state_label.setText("半小时没有收到文件，已自动停止接收。")
            self.yesButton.setText("开始接收")

    def _cleanup(self):
        """弹窗关闭时停服务，避免端口与后台线程留到下一次打开。"""
        self.share.stop()
        self._stop_timer()
        self._stop_relay()

    def closeEvent(self, event):
        # finished 未覆盖的场景（如窗口被外部销毁）也要停
        self._cleanup()
        super().closeEvent(event)


def lan_ready() -> tuple[bool, str]:
    """给页面判断用：现在能不能开局域网直传。"""
    addrs = lan_addresses()
    if addrs:
        return True, addrs[0]
    return False, ""
