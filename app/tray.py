"""托盘后台模式：无主窗口，托盘图标 + 每日定时检查 + 系统通知。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer, QThread, Signal, Qt
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from qfluentwidgets import setTheme, Theme

from app import __version__
from app.services import store
from app.services.daily import run_daily_check
from app.services.notify import send


class DailyWorker(QThread):
    done = Signal(bool, str)

    def run(self):
        try:
            self.done.emit(*run_daily_check())
        except Exception as e:
            self.done.emit(False, f"每日检查异常：{e}")


def _tray_icon() -> QIcon:
    """生成一个简洁的托盘图标（深色圆底 + 白色 团 字）。"""
    from PySide6.QtGui import QPainter, QColor, QFont

    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#3b82f6"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(4, 4, 56, 56)
    p.setPen(QColor("white"))
    f = QFont("PingFang SC", 28)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "团")
    p.end()
    return QIcon(pm)


class TrayDaemon(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.window = None
        self.worker: DailyWorker | None = None

        self.tray = QSystemTrayIcon(_tray_icon())
        self.tray.setToolTip(f"智慧团建管理员辅助 {__version__}（后台）")
        menu = QMenu()
        act_open = QAction("打开主界面")
        act_open.triggered.connect(self.open_window)
        act_now = QAction("立即检查并通知")
        act_now.triggered.connect(self.check_now)
        act_quit = QAction("退出")
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_open)
        menu.addAction(act_now)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

        # 每日定时：对准通知时刻（默认 08:00），触发后重新排下一个
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_now)
        self._schedule_next()
        # 启动时若已过今日通知点且当天未检查过，先补一次
        if self._missed_today():
            QTimer.singleShot(3000, self.check_now)

    # ---------- 调度 ----------

    def _notify_target(self) -> tuple[int, int]:
        hh, mm = store.notify_time(store.load()).split(":")
        return int(hh), int(mm)

    def _next_fire(self) -> datetime:
        hh, mm = self._notify_target()
        now = datetime.now()
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    def _schedule_next(self):
        target = self._next_fire()
        ms = int((target - datetime.now()).total_seconds() * 1000) + 1000
        self.timer.start(ms)

    def _missed_today(self) -> bool:
        """今天的通知时刻已过，且今天还没有成功检查过 → 补发。"""
        last_file = store.load().get("lastDailyOkDate")
        hh, mm = self._notify_target()
        now = datetime.now()
        return (now.hour, now.minute) >= (hh, mm) and last_file != now.strftime("%Y-%m-%d")

    # ---------- 动作 ----------

    def check_now(self):
        if self.worker and self.worker.isRunning():
            return
        self.worker = DailyWorker()
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _on_done(self, ok: bool, message: str):
        if ok:
            store.update(lambda cfg: cfg.__setitem__(
                "lastDailyOkDate", datetime.now().strftime("%Y-%m-%d")))
            send("团员情况", message, self.tray)
        else:
            send("每日检查失败", message, self.tray)
        self._schedule_next()

    def open_window(self):
        if self.window is None:
            from app.ui.main_window import MainWindow

            self.window = MainWindow()
        self.window.show()
        self.window.raise_()

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_window()

    def quit(self):
        self.tray.hide()
        self.app.quit()


def run_tray() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    from app.setup import setup_app

    setup_app()
    daemon = TrayDaemon(app)
    from app.services.update_check import check_on_startup

    check_on_startup()  # 后台模式只发系统通知，没有窗口可挂信息条
    # 抑制未使用告警
    _ = daemon
    return app.exec()
