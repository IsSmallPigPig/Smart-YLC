"""组织状况的持久化：部分同步不能把没同步到的支部从统计里抹掉。"""

from __future__ import annotations

import os
import tempfile

import pytest
from PySide6.QtWidgets import QApplication, QWidget


@pytest.fixture()
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def isolated_store(monkeypatch):
    from app.services import store

    monkeypatch.setattr(store, "CONFIG_FILE",
                        os.path.join(tempfile.mkdtemp(prefix="gd-org-"), "config.json"))
    return store


def _member(i: int, branch: str, unpaid: float = 0.0) -> dict:
    return {"member_id": f"9{i:05d}", "name": f"团员{i}", "mobile": f"138{i:08d}",
            "real_name_status": "通过", "income": "学生无收入",
            "report_data_status": "审核通过", "ebook_status": "已上传",
            "unpaid_total": unpaid, "unpaid_months": 1 if unpaid else 0,
            "oid": "o", "org_name": branch}


def _cache(total, uploaded, by_kind, updated):
    return {"branch": "x", "total": total, "uploaded": uploaded, "by_kind": by_kind,
            "transfer": {"applied": 0}, "error_rows": [], "updatedAt": updated}


class _Window(QWidget):
    """只提供 OrgPage 需要的 settings_page.accounts。"""

    def __init__(self, accounts):
        super().__init__()
        class _S:
            pass
        self.settings_page = _S()
        self.settings_page.accounts = accounts


def test_partial_sync_keeps_other_branches(app, isolated_store):
    """后台 30 秒同步只拿到一个支部时，其余支部的统计必须留在页面上。

    之前的实现只汇总「本轮 memberCache 里的支部」，覆盖页面后总量从 52 掉到 2，
    看起来就是组织状况的数据丢了。"""
    store = isolated_store
    accounts = [{"userName": f"ssfz1240{i}", "password": "p", "remark": f"{i}班",
                 "health": {"status": "ok", "message": "", "checkedAt": ""}} for i in (1, 2, 3)]
    store.save({
        "themeMode": "dark", "accounts": accounts,
        "memberCache": {"ssfz12401": {"branch": "1班",
                                      "members": [_member(1, "1班"), _member(2, "1班", 12.0)]}},
        "orgStatsCache": {
            "ssfz12401": _cache(2, 2, {"identity": 0, "report": 0, "archive": 0, "fees": 1},
                                "2026-11-15T07:00:00"),
            "ssfz12402": _cache(30, 28, {"identity": 2, "report": 0, "archive": 0, "fees": 0},
                                "2026-11-15T06:00:00"),
            "ssfz12403": _cache(20, 20, {"identity": 0, "report": 0, "archive": 0, "fees": 0},
                                "2026-11-15T05:00:00"),
        },
        "messagesCache": {}, "messagesRead": {}, "secretaries": [], "rosters": {},
        "notifyMode": "interval", "notifyInterval": 4,
    })

    from app.ui.org_page import OrgPage

    page = OrgPage(_Window(accounts))
    page.resize(900, 700)
    page.show()
    for _ in range(5):
        app.processEvents()

    assert (page._total, page._uploaded) == (52, 50)   # 启动时按整份缓存恢复

    total, uploaded, by_kind, _rows = page._offline_snapshot()
    assert total == 52, "只同步了一个支部，其余支部被抹掉了"
    assert uploaded == 50
    assert by_kind["identity"] == 2
    assert sorted(store.load()["orgStatsCache"]) == ["ssfz12401", "ssfz12402", "ssfz12403"]


def test_empty_account_list_does_not_wipe_cache(app, isolated_store):
    """账号还没载入时不许拿空集合去清理缓存——那会把整份统计删光。"""
    store = isolated_store
    store.save({
        "themeMode": "dark", "accounts": [],
        "memberCache": {}, "messagesCache": {}, "read": {},
        "orgStatsCache": {"ssfz12401": _cache(10, 9, {"identity": 0, "report": 0,
                                                     "archive": 0, "fees": 0},
                                             "2026-11-15T07:00:00")},
        "notifyMode": "interval", "notifyInterval": 4,
    })

    from app.ui.org_page import OrgPage

    page = OrgPage(_Window([]))
    page._persist_account("ssfz12402", "2班", 5, 5,
                          {"identity": 0, "report": 0, "archive": 0, "fees": 0}, None, [])
    assert "ssfz12401" in store.load()["orgStatsCache"]
