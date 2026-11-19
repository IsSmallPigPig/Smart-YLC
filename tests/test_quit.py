"""退出：托盘与主窗口共用一条路径。

`QApplication.quit()` 不会杀掉后台线程；这里先收窗口与网页会话，再退事件循环，
真进程里兜底 2 秒硬退。测试进程里不排硬退（会被当成 pytest 自己崩了）。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication, QWidget


@pytest.fixture()
def app():
    yield QApplication.instance() or QApplication([])


def test_quit_app_hides_window_and_closes_browser(app, monkeypatch):
    from app.ui import quit as quit_mod

    called = []
    monkeypatch.setattr(QApplication, "quit",
                        staticmethod(lambda: called.append("quit")))
    closed = []

    class Browser:
        def shutdown(self):
            closed.append(1)

    class Win(QWidget):
        browser_window = Browser()

    win = Win()
    win._really_quit = False
    win.show()
    quit_mod.quit_app(win)

    assert win._really_quit is True
    assert win.isHidden()
    assert closed == [1], "网页窗口的存储分区没有收掉"
    assert called == ["quit"]


def test_quit_app_without_window_still_quits(app, monkeypatch):
    from app.ui import quit as quit_mod

    called = []
    monkeypatch.setattr(QApplication, "quit",
                        staticmethod(lambda: called.append("quit")))
    quit_mod.quit_app(None)
    assert called == ["quit"]


def test_hard_exit_not_armed_under_pytest(monkeypatch):
    """pytest 进程里排硬退等于自杀：只验条件判断，不验线程。"""
    from app.ui import quit as quit_mod

    def boom(*_args, **_kwargs):
        raise AssertionError("测试进程里排了硬退线程")

    monkeypatch.setattr(quit_mod.threading, "Timer", boom)
    quit_mod._arm_hard_exit()
