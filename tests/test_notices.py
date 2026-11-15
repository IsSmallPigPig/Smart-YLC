"""通知中心与公告面板：去重语义、更新不重复触发、面板行复用。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.services.notices import Notice, NoticeCenter


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def test_upsert_dedupes_and_signals_only_new(app):
    center = NoticeCenter()
    fresh: list[str] = []
    changed: list[int] = []
    center.new_issue.connect(lambda n: fresh.append(n.key))
    center.changed.connect(lambda: changed.append(1))

    notice = Notice(key="account:a", title="账号 a：密码错误")
    assert center.upsert(notice) is True
    assert center.upsert(Notice(key="account:a", title="账号 a：密码错误")) is False
    assert fresh == ["account:a"]        # 同一问题只报一次
    assert len(changed) == 1             # 内容没变不触发重绘
    assert center.issue_count() == 1


def test_update_is_not_a_new_issue(app):
    center = NoticeCenter()
    fresh: list[str] = []
    center.new_issue.connect(lambda n: fresh.append(n.key))

    center.upsert(Notice(key="k", title="一"))
    center.upsert(Notice(key="k", title="二"))
    assert fresh == ["k"]                 # 更新不算新问题
    assert center.get("k").title == "二"


def test_replace_prefix_clears_stale(app):
    center = NoticeCenter()
    center.replace_prefix("account:", [Notice(key="account:a", title="a"),
                                       Notice(key="account:b", title="b")])
    assert center.issue_count() == 2
    center.replace_prefix("account:", [Notice(key="account:b", title="b")])
    assert center.get("account:a") is None
    assert center.get("account:b") is not None
    assert center.issue_count() == 1


def test_panel_reuses_rows_and_empties_cleanly(app):
    from app.ui.notice_panel import NoticePanel

    panel = NoticePanel()
    panel.set_notices([Notice(key="a", title="一")])
    row = panel._rows["a"]
    panel.set_notices([Notice(key="a", title="二")])
    assert panel._rows["a"] is row        # 同一行复用，不整块重建（不闪烁）
    panel.set_notices([])
    assert panel._rows == {}
