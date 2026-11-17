"""后台提醒只走系统原生通道：不允许应用内浮窗，送达与否以原生通道的返回为准。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def test_macos_uses_native_only(app, monkeypatch):
    """macOS 交给 UNUserNotificationCenter：返回 True 即送达，回调随通知带走。"""
    from app.services import macos_notify
    from app.ui import bg_toast

    calls = []
    monkeypatch.setattr(bg_toast.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(macos_notify, "send",
                        lambda t, b, on_click=None: calls.append((t, b, on_click)) or True)
    assert bg_toast.alert("初中部初三七班 账号异常", "登录冷却：约 47 分钟后再试",
                          tone="error", on_click=lambda: None) is True
    assert calls[0][0] == "初中部初三七班 账号异常"
    assert calls[0][2] is not None


def test_macos_denied_returns_false_without_fallback(app, monkeypatch):
    """权限被关时返回 False——绝不退化成应用内浮窗，由调用方提示去开启。"""
    from app.services import macos_notify
    from app.ui import bg_toast

    monkeypatch.setattr(bg_toast.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(macos_notify, "send", lambda *a, **k: False)
    assert bg_toast.alert("标题", "正文") is False
    # 模块里不再存在浮窗宿主
    assert not hasattr(bg_toast, "_host")
    assert not hasattr(bg_toast, "Toast")


def test_windows_uses_tray_bubble(app, monkeypatch):
    """Windows/Linux 走托盘气泡，默认动作随通知绑定。"""
    from app.services import notify
    from app.ui import bg_toast

    calls = []
    monkeypatch.setattr(bg_toast.platform, "system", lambda: "Windows")
    monkeypatch.setattr(notify, "send",
                        lambda t, b, tray=None, on_click=None:
                        calls.append((t, b, tray, on_click)) or True)
    opened = []
    bg_toast.set_default_action(lambda: opened.append("window"))
    assert bg_toast.alert("智慧团委", "已缩到托盘继续后台刷新") is True
    assert calls[0][3] is not None
    calls[0][3]()
    assert opened == ["window"]
    bg_toast.set_default_action(None)


def test_native_exception_is_not_swallowed_into_a_toast(app, monkeypatch):
    """原生通道抛异常按未送达处理（返回 False），不造浮窗兜底。"""
    from app.services import macos_notify
    from app.ui import bg_toast

    def _boom(*a, **k):
        raise RuntimeError("center unavailable")

    monkeypatch.setattr(bg_toast.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(macos_notify, "send", _boom)
    assert bg_toast.alert("标题", "正文") is False
