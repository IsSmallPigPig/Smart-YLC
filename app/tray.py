"""托盘后台模式：无主窗口，托盘图标 + 每日定时检查 + 系统通知。"""

from __future__ import annotations

import sys
from datetime import datetime

from PyQt5.QtCore import QObject, QTimer, QThread, pyqtSignal as Signal, Qt
from PyQt5.QtGui import QGuiApplication, QIcon
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon

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
            from app.services.app_log import exception

            exception("托盘每日检查失败")
            self.done.emit(False, f"每日检查异常：{e}")


def _tray_icon() -> QIcon:
    """托盘图标：与应用图标同一套品牌图，不再另画一个圆底团字。"""
    from app.ui.brand import app_icon

    return app_icon()


def bind_tray_menu(tray, menu) -> bool:
    """把 Fluent 菜单挂到托盘图标上；返回「是否需要自己弹」。

    Windows 上 setContextMenu 会把弹出交给 Qt 的托盘私有实现（按系统上下文菜单
    定位、并改写菜单窗口属性），RoundMenu 的圆角、阴影、动画都不生效，看上去就是
    系统默认的方形灰菜单。所以 Windows 不挂，改由调用方在 activated 的 Context
    分支里自己 exec；macOS 只有 setContextMenu 这条通路会显示菜单（它不发 Context
    信号），必须挂。
    """
    if sys.platform == "win32":
        return True
    tray.setContextMenu(menu)
    return False


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
        # 是否需要自己弹菜单。macOS 上 setContextMenu 之后系统会弹一次，
        # activated 也仍会发 Context——不听这个开关就会同时出现两个菜单。
        self._self_popup = bind_tray_menu(self.tray, menu)
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
        elif (reason == QSystemTrayIcon.ActivationReason.Context
              and self._self_popup):
            self._popup_menu()

    def _popup_menu(self):
        """在光标处弹出 Fluent 菜单（只有没挂 contextMenu 的平台才需要）。"""
        from PyQt5.QtGui import QCursor

        self.menu.exec(QCursor.pos())

    def quit(self):
        """退出：先停定时器、收窗口，再退事件循环并兜底硬退（见 app.ui.quit）。"""
        self.timer.stop()
        self.tray.hide()
        from app.ui.quit import quit_app

        quit_app(self.window)


def run_tray() -> int:
    from PyQt5.QtCore import Qt

    # 托盘里也能打开主窗口，内嵌网页同样要求共享 OpenGL 上下文
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    # 与主界面一致：分数缩放按实际比例渲染（托盘菜单/通知图标也跟着清晰）
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 托盘常驻没有窗口，异常见不到任何提示；崩溃日志是唯一的现场证据
    from app.services.app_log import install_crash_handler

    install_crash_handler()

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
