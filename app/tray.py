"""托盘后台模式：无主窗口，托盘图标 + 每日定时检查 + 系统通知。"""

from __future__ import annotations

import sys
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, QThread, Signal, Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from qfluentwidgets import Action, FluentIcon as FIF, RoundMenu

from app import __version__
from app.services import store
from app.services.daily import run_daily_check


class DailyWorker(QThread):
    done = Signal(bool, str)

    def run(self):
        try:
            self.done.emit(*run_daily_check())
        except Exception as e:
            self.done.emit(False, f"每日检查异常：{e}")


def _tray_icon() -> QIcon:
    """托盘图标：与应用图标同一套品牌图，不再另画一个圆底团字。"""
    from app.ui.brand import app_icon

    return app_icon()


class TrayDaemon(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.window = None
        self.worker: DailyWorker | None = None

        self.tray = QSystemTrayIcon(_tray_icon())
        from app.ui.page import set_tip

        set_tip(self.tray, f"智慧团建管理员辅助 {__version__}（后台）")
        # Fluent 菜单：与窗口内其它菜单同一种圆角、间距和图标排版，
        # 不再用 QMenu 的系统原生样式（Windows 上是方形灰菜单）。
        menu = RoundMenu(parent=None)
        act_open = Action(FIF.HOME, "打开主界面")
        act_open.triggered.connect(self.open_window)
        act_now = Action(FIF.SYNC, "立即检查并通知")
        act_now.triggered.connect(self.check_now)
        act_quit = Action(FIF.POWER_BUTTON, "退出")
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_open)
        menu.addAction(act_now)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.menu = menu   # QSystemTrayIcon 不接管所有权，必须自己留着
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

        # 后台模式没有主窗口：点提醒就去建/叫出主界面
        from app.ui import bg_toast

        bg_toast.set_default_action(self.open_window)

        # 通知节奏：每天固定时刻或按小时间隔，每分钟检查一次是否到点。
        # 定时器只做轻量判断，真正取数与登录在 DailyWorker 里跑。
        self.timer = QTimer(self)
        self.timer.setInterval(60 * 1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        QTimer.singleShot(3000, self._tick)

    # ---------- 调度 ----------

    def _tick(self):
        from app.services import notify_schedule

        if notify_schedule.should_fire(datetime.now(), store.load()):
            self.check_now()

    # ---------- 动作 ----------

    def check_now(self):
        if self.worker and self.worker.isRunning():
            return
        self.worker = DailyWorker()
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _on_done(self, ok: bool, message: str):
        now = datetime.now()
        store.update(lambda cfg: (
            cfg.__setitem__("lastDailyAt", now.isoformat()),
            cfg.__setitem__("lastDailyOkDate", now.strftime("%Y-%m-%d")),
        ))
        from app.ui import bg_toast

        bg_toast.alert("团员情况" if ok else "每日检查失败", message,
                       tone="info" if ok else "error", tray=self.tray,
                       on_click=self.open_window)

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
    from PySide6.QtCore import Qt

    # 托盘里也能打开主窗口，内嵌网页同样要求共享 OpenGL 上下文
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    # 与主界面一致：分数缩放按实际比例渲染（托盘菜单/通知图标也跟着清晰）
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 与主界面共用单实例锁：已有实例时只把它的窗口叫出来，不再开第二个托盘
    from app.services.single_instance import SingleInstance

    guard = SingleInstance()
    if not guard.attach():
        return 0
    app.aboutToQuit.connect(guard.close)

    from app.setup import setup_app

    setup_app()
    daemon = TrayDaemon(app)
    guard.set_handler(lambda _command="": daemon.open_window())
    from app.services.update_check import check_on_startup

    check_on_startup()  # 后台模式只发系统通知，没有窗口可挂信息条
    # 抑制未使用告警
    _ = daemon
    return app.exec()
