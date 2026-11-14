"""调用方账号过滤：异常账号不得参与站点调用，未检测账号放行。"""

from __future__ import annotations

from app.domain.account_health import excluded_note, split_accounts


def _acc(user, status=None):
    a = {"userName": user, "password": "p", "remark": "", "graduateYear": ""}
    if status is not None:
        a["health"] = {"status": status, "message": "", "checkedAt": ""}
    return a


def test_split_excludes_marked_abnormal_only():
    accounts = [
        _acc("a1", "ok"),
        _acc("a2", "cooldown"),
        _acc("a3", "failed"),
        _acc("a4", "unknown"),
        _acc("a5"),  # 从未测活（无 health 字段）
        _acc("a6", "captcha"),
        _acc("a7", "network"),
    ]
    usable, excluded = split_accounts(accounts)
    assert [a["userName"] for a in usable] == ["a1", "a4", "a5"]
    assert [a["userName"] for a in excluded] == ["a2", "a3", "a6", "a7"]


def test_excluded_note_names_reason():
    note = excluded_note([_acc("a2", "cooldown"), _acc("a3", "failed")])
    assert "a2（登录冷却" in note and "a3（密码错误）" in note
    assert excluded_note([]) == ""
