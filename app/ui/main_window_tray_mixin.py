"""MainWindow 的托盘驻留与通知行为 mixin：方法体从 main_window.py 原样提取。

这些方法调用的 _notice_center / _flush_bars / refresh_all / close 等都在
MainWindow 实例上，走 self 解析；MRO 里本 mixin 置前，无 __init__，不影响构造。
"""

from __future__ import annotations

import time

from PyQt5.QtCore import QTimer, Qt

class MainWindowTrayMixin:
    """托盘图标、悬浮通知、退出与立即同步。"""

    def _setup_tray(self):
        from PyQt5.QtWidgets import QSystemTrayIcon

        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        from qfluentwidgets import Action, FluentIcon as FIF, RoundMenu

        from app.ui.brand import app_icon

        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("智慧团委")
        # 与后台模式（app/tray.py）一致：Fluent 圆角菜单，不用系统原生的方形灰菜单。
        # QSystemTrayIcon 不接管菜单所有权，必须自己留住引用，否则菜单被回收、
        # 右键点了没反应。
        menu = RoundMenu(parent=None)
        act_show = Action(FIF.HOME, "显示主界面")
        act_show.triggered.connect(self._restore_from_tray)
        act_sync = Action(FIF.SYNC, "立即同步")
        act_sync.triggered.connect(self._sync_now)
        act_quit = Action(FIF.POWER_BUTTON, "退出")
        act_quit.triggered.connect(self._quit_app)
        menu.addAction(act_show)
        menu.addAction(act_sync)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray_menu = menu
        from app.tray import bind_tray_menu

        # 是否需要自己弹（见 bind_tray_menu）。macOS 上系统已经会弹，再自己弹
        # 一次就会同时出现两个菜单。
        self._tray_self_popup = bind_tray_menu(self.tray, menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        # 通知模块会按需临时建一个托盘图标；登记之后它改用这一个，并收起临时的。
        # 留着两个「智慧团委」时，用户点到的可能是没有点击回调的那一个。
        from app.services import notify

        notify.register_tray(self.tray)

    def _on_tray_activated(self, reason):
        from PyQt5.QtWidgets import QSystemTrayIcon

        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._restore_from_tray()
        elif (reason == QSystemTrayIcon.ActivationReason.Context
              and getattr(self, "_tray_self_popup", False)):
            # 只有没挂 contextMenu 的平台才自己弹（见 bind_tray_menu）
            from PyQt5.QtGui import QCursor

            self.tray_menu.exec(QCursor.pos())

    def _restore_from_tray(self):
        """从托盘回到主界面。

        先清掉最小化标记再显示：窗口被最小化后又缩进托盘时，单独调用
        showNormal() 有时只闪一下任务栏，窗口仍停在最小化状态。
        """
        self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized)
                            | Qt.WindowState.WindowActive)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _alert(self, title: str, body: str = "", action: str = "", tone: str = ""):
        """后台提醒：点一下回到主界面，并切到该处理的那一页。

        只走系统原生通道：macOS 系统通知（打包版），Windows 托盘气泡。
        原生通道不可用时不再弹应用内浮窗，第一次提醒失败时说清怎么开启。"""
        from app.ui import bg_toast

        delivered = bg_toast.alert(
            title, body, tone=tone, tray=getattr(self, "tray", None),
            on_click=lambda: self._open_alert(action),
        )
        if not delivered:
            self._hint_notification_channel()

    def _hint_notification_channel(self):
        """提醒没能送达时提示一次：没有原生通道就没有任何后台提醒。"""
        import sys

        if sys.platform != "darwin" or getattr(self, "_notify_hint_shown", False):
            return
        from app.services import macos_notify

        state = macos_notify.permission_state()
        if state == "ok":
            return
        self._notify_hint_shown = True
        from qfluentwidgets import (
            InfoBar,
            InfoBarIcon,
            InfoBarPosition,
            TransparentPushButton,
        )

        if state == "denied":
            content = (f"后台提醒不会弹出：在「系统设置 → 通知」里打开"
                       f"「{macos_notify.app_name()}」")
        else:
            content = ("后台提醒不会弹出：打包成 .app 运行才能使用系统通知"
                       f"（当前挂在「{macos_notify.app_name()}」名下）")
        bar = InfoBar(
            icon=InfoBarIcon.WARNING, title="系统通知不可用",
            content=content,
            orient=Qt.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP_RIGHT, duration=12000, parent=self,
        )
        btn = TransparentPushButton(text="去开启", parent=bar)
        btn.clicked.connect(lambda: (macos_notify.open_settings(), bar.close()))
        bar.addWidget(btn)
        bar.show()

    def _open_alert(self, action: str):
        self._restore_from_tray()
        if action:
            QTimer.singleShot(0, lambda: self.handle_notice_action(action))

    def _sync_now(self):
        self._last_sync_ts = time.monotonic()
        self.refresh_all(silent=True, fast=True)

    def _quit_app(self):
        """真退出：先收窗口与网页会话，再退事件循环（见 app.ui.quit）。

        只调 QApplication.quit() 的话，工作线程与 WebEngine 会把进程拖住——
        表现是托盘没了、窗口还在。"""
        if getattr(self, "tray", None) is not None:
            self.tray.hide()
        from app.ui.quit import quit_app

        quit_app(self)

