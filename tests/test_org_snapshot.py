"""首页概览快照：支部数量应按系统里实际不同的团支部计，而不是已配置账号数。"""

from app.services.org_snapshot import snapshot


def _cfg(accounts, members_by_user):
    return {
        "accounts": [{"userName": u} for u in accounts],
        "memberCache": {
            u: {"branch": u, "members": members}
            for u, members in members_by_user.items()
        },
        "messagesRead": {},
    }


def test_branches_distinct_org_name():
    cfg = _cfg(
        ["a1", "a2"],
        {
            "a1": [
                {"name": "张三", "mobile": "1", "org_name": "高一1班团支部"},
                {"name": "李四", "mobile": "2", "org_name": "高一1班团支部"},
            ],
            "a2": [
                {"name": "王五", "mobile": "3", "org_name": "高一2班团支部"},
            ],
        },
    )
    data = snapshot(cfg)
    assert data["branches"] == 2
    assert data["total"] == 3


def test_one_account_many_branches():
    # 一个账号覆盖多个团支部：支部数应等于不同团支部数，而不是 len(accounts)=1
    cfg = _cfg(
        ["admin"],
        {
            "admin": [
                {"name": "甲", "mobile": "1", "org_name": "一班团支部"},
                {"name": "乙", "mobile": "2", "org_name": "二班团支部"},
                {"name": "丙", "mobile": "3", "org_name": "三班团支部"},
            ],
        },
    )
    data = snapshot(cfg)
    assert data["branches"] == 3


def test_ignores_empty_org_name():
    cfg = _cfg(
        ["a1"],
        {
            "a1": [
                {"name": "甲", "mobile": "1", "org_name": ""},
                {"name": "乙", "mobile": "2", "org_name": "   "},
                {"name": "丙", "mobile": "3", "org_name": "一班团支部"},
            ],
        },
    )
    data = snapshot(cfg)
    assert data["branches"] == 1
