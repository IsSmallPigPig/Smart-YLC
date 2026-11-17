"""启动公告：源格式、代理回退、已读记录与弹窗行为。"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PAYLOAD = json.dumps({
    "version": 1,
    "announcements": [
        {"id": "a1", "title": "版本更新", "date": "2026-11-17",
         "items": ["修复启动闪退", "公告改走代理"]},
        {"id": "a2", "title": "维护通知", "items": ["11 月 3 日维护"]},
    ],
}, ensure_ascii=False).encode("utf-8")


def _patch_read(monkeypatch, script):
    """按调用顺序返回内容或抛错，同时记下请求过的 URL。"""
    from app.services import updater

    calls = []

    def _read(url, timeout):
        calls.append((url, timeout))
        result = script[len(calls) - 1] if len(calls) <= len(script) else script[-1]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(updater, "_read", _read)
    return calls


def test_parse_announcements_keeps_order_and_items():
    from app.services.announcements import parse_announcements

    items = parse_announcements(PAYLOAD)
    assert [(a.id, a.title, a.date) for a in items] == [
        ("a1", "版本更新", "2026-11-17"), ("a2", "维护通知", "")]
    assert items[0].items == ("修复启动闪退", "公告改走代理")
    assert parse_announcements(b'{"version":1,"announcements":[]}') == []


@pytest.mark.parametrize("raw", [
    b"not json",
    b"[]",
    b'{"version":2,"announcements":[]}',
    b'{"version":1}',
    b'{"version":1,"announcements":[{"id":"x","title":"t","items":[]}]}',
    b'{"version":1,"announcements":[{"title":"t","items":["a"]}]}',
    b'{"version":1,"announcements":[{"id":"x","title":"t","items":[1,2]}]}',
])
def test_parse_rejects_bad_files_with_reason(raw):
    from app.services.announcements import AnnouncementError, parse_announcements

    with pytest.raises(AnnouncementError):
        parse_announcements(raw)


def test_fetch_tries_proxies_before_direct(monkeypatch):
    from app.services import announcements, updater

    calls = _patch_read(monkeypatch, [updater.UpdateError("代理不通"), PAYLOAD])
    monkeypatch.setattr(updater, "ranked_gateways", lambda: [
        {"key": "direct", "label": "直连", "prefix": ""},
        {"key": "p1", "label": "P1", "prefix": "https://p1/"},
    ])
    items = announcements.fetch_announcements(url="https://raw.test/a.json")

    assert calls[0][0] == "https://p1/https://raw.test/a.json"
    assert calls[1][0] == "https://raw.test/a.json"
    assert [a.id for a in items] == ["a1", "a2"]


def test_fetch_reports_all_channel_failures(monkeypatch):
    from app.services import announcements, updater

    _patch_read(monkeypatch, [updater.UpdateError("代理 502"),
                              updater.UpdateError("直连超时")])
    with pytest.raises(announcements.AnnouncementError, match="502.*超时"):
        announcements.fetch_announcements(gateways=[
            {"key": "p1", "label": "P1", "prefix": "https://p1/"},
            updater.DIRECT,
        ])


def test_seen_state_only_marks_named_ids(monkeypatch, tmp_path):
    from app.services import announcements, store

    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save({"announcements": {"seenIds": ["old"], "other": 1}})
    items = announcements.parse_announcements(PAYLOAD)

    assert [a.id for a in announcements.pending(items)] == ["a1", "a2"]
    announcements.mark_seen(["a1"])
    assert announcements.seen_ids() == {"old", "a1"}
    assert [a.id for a in announcements.pending(items)] == ["a2"]
    assert store.load()["announcements"]["other"] == 1  # 不覆盖同组其它字段


def test_dialog_confirm_marks_read(monkeypatch, tmp_path):
    from app.services import announcements, store
    from app.ui.announcement_dialog import show_pending

    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save({})
    from PyQt5.QtWidgets import QWidget

    host = QWidget()
    host.resize(900, 600)
    item = announcements.Announcement("a1", "版本更新", ("修复启动闪退", "公告改走代理"))
    queue = show_pending(host, [item])
    dlg = queue.current

    assert "1. 修复启动闪退" in dlg.body.toPlainText()
    assert "2. 公告改走代理" in dlg.body.toPlainText()
    assert dlg.yesButton.text() == "确定" and dlg.cancelButton.text() == "取消"
    from PyQt5.QtTest import QTest

    QTest.qWait(250)  # 等 fluent 淡入动画走完
    dlg.accept()
    assert announcements.seen_ids() == {"a1"}


def test_dialog_cancel_keeps_unread_and_shows_next(monkeypatch, tmp_path):
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QWidget

    from app.services import announcements, store
    from app.ui.announcement_dialog import show_pending

    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save({})
    host = QWidget()
    host.resize(900, 600)
    items = [announcements.Announcement("a1", "第一条", ("内容一",)),
             announcements.Announcement("a2", "第二条", ("内容二",))]
    queue = show_pending(host, items)
    QTest.qWait(250)  # 等 fluent 淡入动画走完，别在特效中途销毁弹窗
    queue.current.reject()
    QTest.qWait(400)
    assert queue.current is not None and queue.current.item.id == "a2"
    queue.current.accept()
    QTest.qWait(400)
    assert announcements.seen_ids() == {"a2"}  # 取消的第一条下次还会弹
    assert queue.current is None


def test_check_on_login_fetches_once(monkeypatch):
    """启动只拉一次：重复调用（例如托盘唤醒）不会叠请求或叠弹窗。"""
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QWidget

    from app.services import announcement_check, announcements

    calls = []
    monkeypatch.setattr(announcements, "fetch_announcements",
                        lambda: calls.append(1) or [])
    host = QWidget()
    announcement_check.check_on_login(host, delay_ms=0)
    announcement_check.check_on_login(host, delay_ms=0)
    QTest.qWait(200)
    assert calls == [1]
