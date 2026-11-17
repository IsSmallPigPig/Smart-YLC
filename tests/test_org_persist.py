"""组织状况的持久化：部分同步不能把没同步到的支部从统计里抹掉。"""

from __future__ import annotations

import os
import tempfile

import pytest
from PyQt5.QtWidgets import QApplication, QWidget


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


def _drain(page, qapp, timeout: float = 5.0):
    """等页面的后台线程收尾，再把页面删掉。

    OrgPage 的离线重算跑在 CallbackWorker 上；用例结束时直接丢引用，
    QThread 会在下一个用例的事件循环里带着已死的槽投递，PyQt5 段错误。
    这里显式 wait + deleteLater，把生命周期收在本用例内。"""
    import time

    for w in list(getattr(page, "_offline_workers", []) or []):
        end = time.monotonic() + timeout
        while w.isRunning() and time.monotonic() < end:
            qapp.processEvents()
            time.sleep(0.005)
        w.wait(2000)
    page.deleteLater()
    qapp.processEvents()


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


def test_boot_computes_offline_when_only_members_cached(app, isolated_store):
    """有成员缓存但没有统计缓存时（首次启动、导入配置、换机恢复备份），
    组织状况必须立刻离线算出来，而不是停在 0 等 30 秒后的成员同步。

    此前 --demo 启动与导入备份都踩这一条：memberCache 齐了、orgStatsCache 是空，
    _restore_persisted 直接 return，页面一片「暂无数据」。"""
    import time

    store = isolated_store
    store.save({
        "accounts": [{"userName": "demo01", "password": "x", "remark": "高一1班"}],
        "memberCache": {"demo01": {
            "branch": "高一1班", "fetchedAt": "",
            "members": [_member(1, "高一1班"), _member(2, "高一1班", unpaid=2.0)]}},
        "orgStatsCache": {},
    })

    from app.ui.org_page import OrgPage

    page = OrgPage(_Window([{"userName": "demo01", "password": "x", "remark": "高一1班"}]))
    end = time.monotonic() + 5
    while time.monotonic() < end and page._total == 0:
        app.processEvents()
        time.sleep(0.01)
    assert page._total == 2, page._total
    assert page._uploaded == 2
    assert page._by_kind["fees"] == 1
    assert len(page._error_rows) == 1
    _drain(page, app)


def test_demo_data_counts_each_branch_once(app, isolated_store):
    """演示数据的统计要按账号逐支部计一次：orgStatsCache 用支部名做键、或与
    memberCache 同时存在时，汇总会把同一个支部算两遍（260 人变 520）。"""
    import time

    from app.services import demo_data

    cfg = demo_data.build_config()
    isolated_store.save(cfg)
    total_members = sum(len(b["members"]) for b in cfg["memberCache"].values())

    from app.ui.org_page import OrgPage

    page = OrgPage(_Window(cfg["accounts"]))
    end = time.monotonic() + 8
    while time.monotonic() < end and page._total == 0:
        app.processEvents()
        time.sleep(0.01)
    assert page._total == total_members, (page._total, total_members)
    # 异常人数必须真的存在，否则「统计全 0」这类假数据问题又会漏过去
    assert page._error_rows, "演示数据应当包含异常团员"
    assert sum(page._by_kind.values()) > 0
    _drain(page, app)
