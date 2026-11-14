"""系统通知的跨平台行为：Windows 走托盘气泡，图标必须是 Qt 自己的枚举。"""

from __future__ import annotations


class FakeTray:
    def __init__(self):
        self.calls = []

    def showMessage(self, *args):
        self.calls.append(args)


def test_send_passes_message_icon_to_tray(monkeypatch):
    """曾经把 qfluentwidgets 的 InfoBarIcon 传进 showMessage，Windows 上每次
    通知都 TypeError 然后被吞掉——钉住参数类型，别再回退。"""
    from PySide6.QtWidgets import QSystemTrayIcon

    from app.services import notify

    monkeypatch.setattr(notify.platform, "system", lambda: "Windows")
    tray = FakeTray()

    assert notify.send("有 2 条新站内信", "内容", tray=tray) is True
    assert len(tray.calls) == 1
    title, body, icon, msecs = tray.calls[0]
    assert title == "有 2 条新站内信" and body == "内容"
    assert icon is QSystemTrayIcon.MessageIcon.Information
    assert msecs == 8000


def test_send_without_tray_fails_softly(monkeypatch):
    """拿不到托盘图标时返回 False，由调用方决定要不要提示，不抛异常。"""
    from app.services import notify

    monkeypatch.setattr(notify.platform, "system", lambda: "Windows")
    monkeypatch.setattr(notify, "_fallback_tray", lambda: None)

    assert notify.send("标题", "正文") is False
