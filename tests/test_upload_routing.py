"""按团支部分流路由（纯函数）。"""

from app.domain.naming import Roster, RosterRow
from app.domain.upload_routing import (
    account_for_class,
    digits_of,
    resolve_file_targets,
    roster_class_lookup,
)


def test_digits_of():
    assert digits_of("12407") == "12407"
    assert digits_of("高三3班 12407") == "12407"
    assert digits_of("无数字") == ""
    # 多段数字取最长
    assert digits_of("2026届 12407班") == "12407"


def test_account_for_class():
    accounts = ["ssfz12407", "ssfz12408"]
    assert account_for_class("12407", accounts) == "ssfz12407"
    assert account_for_class("高三3班(12408)", accounts) == "ssfz12408"
    assert account_for_class("99999", accounts) is None
    assert account_for_class("", accounts) is None
    # 精确等于编号的账号也命中
    assert account_for_class("12409", ["12409"]) == "12409"


def test_roster_class_lookup():
    r = Roster("r", [
        RosterRow("张三", "13800000000", {"class": "12407"}),
        RosterRow("李四", "13900000000", None),
    ])
    lookup = roster_class_lookup(r)
    assert lookup[("张三", "13800000000")] == "12407"
    assert ("李四", "13900000000") not in lookup
    assert roster_class_lookup(None) == {}


def test_resolve_targets():
    lookup = roster_class_lookup(Roster("r", [RosterRow("张三", "13800000000", {"class": "12407"})]))
    accounts = ["ssfz12407"]
    parsed = {"张三+13800000000+入团志愿书.pdf": ("张三", "13800000000")}

    targets = resolve_file_targets(list(parsed), parsed, accounts, lookup)
    assert targets[0].account == "ssfz12407" and targets[0].cls == "12407"
    assert targets[0].problem == ""

    # 名单里没有此人 → 提示缺班级
    parsed2 = {"王五+13700000000+入团志愿书.pdf": ("王五", "13700000000")}
    t2 = resolve_file_targets(list(parsed2), parsed2, accounts, lookup)
    assert "班级" in t2[0].problem

    # 命名解析失败 → 提示先修正命名
    t3 = resolve_file_targets(["bad.pdf"], {}, accounts, lookup)
    assert "命名" in t3[0].problem
