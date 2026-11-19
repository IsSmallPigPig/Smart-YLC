"""退出程序的唯一入口：主窗口与托盘共用。

`QApplication.quit()` 只结束事件循环，不保证进程退出——同步用的工作线程、
WebEngine 的 profile 线程、每日检查线程都会把它拖着。从托盘点「退出」时表现
就是：托盘图标没了，主窗口还挂在屏幕上，进程也不结束。

所以退出按三步走：先收窗口（藏起来、关掉网页窗口的存储分区），再退事件循环；
2 秒内还没退干净就直接硬退。硬退只在真进程里排程——测试进程里会把它当成
pytest 自己崩了。
"""

from __future__ import annotations

import os
import sys
import threading

from PyQt5.QtWidgets import QApplication

HARD_EXIT_DELAY = 2.0


def quit_app(window=None) -> None:
    """退出应用。window 传主窗口时连带收掉它的网页窗口。"""
    if window is not None:
        window._really_quit = True
        browser = getattr(window, "browser_window", None)
        if browser is not None:
            browser.shutdown()
        window.hide()

    app = QApplication.instance()
    if app is not None:
        app.quit()

    _arm_hard_exit()


def _arm_hard_exit(delay: float = HARD_EXIT_DELAY) -> None:
    if "pytest" in sys.modules:
        return
    timer = threading.Timer(delay, os._exit, args=(0,))
    timer.daemon = True
    timer.start()
