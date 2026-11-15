"""后台提醒：浮窗能出现、点一下就执行动作并收起，多了按上限挤掉最旧的。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def _reset():
    from app.ui import bg_toast

    if bg_toast._host is not None:
        for toast in list(bg_toast._host.items):
            toast.dismiss()
        bg_toast._host.items.clear()
    bg_toast._host = None


def test_alert_shows_clickable_card(app):
    """点卡片执行动作并收起——后台提醒的点击是绑定的，不会落到别的应用。"""
    from app.ui import bg_toast

    _reset()
    hits = []
    assert bg_toast.alert("初中部初三七班 账号异常", "登录冷却：约 47 分钟后再试",
                          tone="error", on_click=lambda: hits.append("accounts:x"))

    host = bg_toast._host
    assert len(host.items) == 1
    toast = host.items[0]
    assert toast.isVisible()
    assert toast.title_lbl.text() == "初中部初三七班 账号异常"
    assert toast.body_lbl.text() == "登录冷却：约 47 分钟后再试"

    QTest.mouseClick(toast, Qt.MouseButton.LeftButton)
    QTest.qWait(50)
    assert hits == ["accounts:x"]
    assert host.items == []


def test_alert_keeps_at_most_three(app):
    from app.ui import bg_toast

    _reset()
    for i in range(5):
        bg_toast.alert(f"第 {i} 条", "同一批问题不该堆满屏幕")
    assert len(bg_toast._host.items) == 3
    assert [t.title_lbl.text() for t in bg_toast._host.items] == ["第 2 条", "第 3 条", "第 4 条"]


def test_alert_without_action_uses_default(app):
    """没有具体动作（如「已缩到托盘」）时，点它也要回到主界面。"""
    from app.ui import bg_toast

    _reset()
    restored = []
    bg_toast.set_default_action(lambda: restored.append("window"))
    bg_toast.alert("智慧团委", "已缩到托盘继续后台刷新")
    toast = bg_toast._host.items[0]
    QTest.mouseClick(toast, Qt.MouseButton.LeftButton)
    QTest.qWait(50)
    assert restored == ["window"]
    bg_toast.set_default_action(None)
