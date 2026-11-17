"""学校自定义编号规则：迁移、通用字段、诊断、导入行识别、支部排序。"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.org_manage import build_branches
from app.domain.org_rule import (
    DEFAULT_LABEL,
    OrgRule,
    RuleError,
    default_rule_text,
    explain_code,
    parse_account_row,
    parse_rule_text,
    sample_codes,
)

NOW = date(2026, 11, 14)


def rule(text: str, **kw) -> OrgRule:
    patterns, errors = parse_rule_text(text)
    assert not errors, errors
    return OrgRule(patterns=patterns, **kw)


def label(text: str, code: str, **kw) -> str:
    info, why = explain_code(code, rule(text, **kw), NOW)
    assert info is not None, why
    return info.label


# ---------- 默认规则与旧配置 ----------

def test_default_text_keeps_legacy_behaviour():
    r = rule(default_rule_text())
    assert label(default_rule_text(), "12407") == "普通高中高三七班"
    assert label(default_rule_text(), "2407") == "初中部初三七班"
    assert label(default_rule_text(), "ssfz12601") == "普通高中高一一班"
    assert explain_code("92407", r, NOW)[0] is None  # 未知前缀不被截成 2407
    assert parse_rule_text(default_rule_text())[1] == []


def test_old_config_migrates_without_losing_prefixes():
    cfg = {"orgRule": {"prefixes": {"3": "职业高中", "10": "附属中学"},
                       "noPrefix": "初中部"}}
    r = OrgRule.from_config(cfg)
    assert [p.template for p in r.effective_patterns()] == [
        "10{yy}{cc}", "3{yy}{cc}", "{yy}{cc}"]
    assert label(r.rule_text(), "32402") == "职业高中高三二班"
    assert explain_code("12407", r, NOW)[0] is None
    assert label(r.rule_text(), "2407") == "初中部初三七班"


def test_config_roundtrip_preserves_every_option():
    r = rule("X{yyyy}{cc}|小学部|小一,小二,小三,小四,小五,小六",
             century=1900, year_start_month=9, label_template="{dept} {grade} {class2}班",
             account_pattern=r"(?P<code>X\d{6})", row_pattern=r"^[A-Z]\d{6}$",
             samples="X202407, X202307")
    again = OrgRule.from_config({"orgRule": r.to_config()})
    assert again.rule_text() == r.rule_text()
    assert (again.century, again.year_start_month) == (1900, 9)
    assert again.label_template == "{dept} {grade} {class2}班"
    assert again.account_pattern == r"(?P<code>X\d{6})"
    assert again.row_pattern == r"^[A-Z]\d{6}$"
    assert sample_codes(again) == ["X202407", "X202307"]


def test_broken_config_raises_instead_of_overwriting():
    with pytest.raises(RuleError) as e:
        OrgRule.from_config({"orgRule": {"rules": ["1{yy}{cc}|普通高中|高一", "{xx}|坏|初一"]}})
    assert "第 2 行" in str(e.value) and "未知占位符" in str(e.value)
    with pytest.raises(RuleError):
        OrgRule.from_config({"orgRule": {"rules": []}})


# ---------- 不同学校的编号结构 ----------

def test_six_year_primary_school_with_four_digit_year():
    text = "X{yyyy}{cc}|小学部|小一,小二,小三,小四,小五,小六"
    assert label(text, "X202407") == "小学部小三七班"
    assert label(text, "X202107") == "小学部小六七班"
    assert label(text, "X202007") == "小学部已毕业七班"
    info, why = explain_code("X202707", rule(text), NOW)
    assert info is None and "晚于当前学年" in why


def test_class_before_grade_sequence():
    text = "{cc}{g}|初中部|七年级,八年级,九年级"
    assert label(text, "071") == "初中部七年级七班"
    assert label(text, "073") == "初中部九年级七班"
    info, why = explain_code("074", rule(text), NOW)
    assert info is None and "超出" in why


def test_letter_prefix_and_ignored_digits():
    text = "G{yy}{cc}{n}|国际部|高一,高二,高三"
    assert label(text, "G24071") == "国际部高三七班"
    assert label(text, "gdG24071") == "国际部高三七班"
    assert label(text, "ss-G2407-9") == "国际部高三七班"


def test_account_pattern_extracts_code_from_middle():
    text = "S{yy}{cc}|实验部|高一,高二,高三"
    r = rule(text, account_pattern=r"-(?P<code>S\d{4})-")
    info, why = explain_code("stu-S2407-abc", r, NOW)
    assert info is not None and info.label == "实验部高三七班"
    info, why = explain_code("stu-2407-abc", r, NOW)
    assert info is None and "没有匹配" in why


def test_custom_label_century_and_year_start_month():
    r = rule("{yy}{cc}|初中部|初一,初二,初三",
             label_template="{dept} {grade} {class2}班 {code}",
             year_start_month=9)
    info, why = explain_code("2407", r, date(2026, 8, 1))  # 9 月起算，仍属 2025 学年
    assert info is not None and info.grade == "初二"
    info, why = explain_code("2407", r, date(2026, 9, 1))
    assert info.label == "初中部 初三 07班 2407"

    older = rule("{yy}{cc}|初中部|初一,初二,初三", century=1900)
    assert explain_code("2407", older, NOW)[0].grade == "已毕业"  # 24 按 1924 年读


def test_grade_index_reflects_custom_grade_names():
    r = rule("X{yyyy}{cc}|小学部|小一,小二,小三,小四,小五,小六")
    assert r.grade_order()["小一"] == 0
    assert r.grade_order()["小六"] == 5
    assert r.grade_order()["已毕业"] == 99
    info, _ = explain_code("X202407", r, NOW)
    assert info.grade_index == 2


# ---------- 错误诊断 ----------

def test_rule_text_reports_each_bad_line():
    text = ("1{yy}{cc}|普通高中|高一,高二,高三\n"
            "{xx}|坏规则|初一\n"
            "2{yy}|缺班号|高一\n"
            "3{yy}{cc}||高一\n"
            "4{yy}{cc}|缺年级|\n"
            "一行没有分隔符")
    patterns, errors = parse_rule_text(text)
    assert len(patterns) == 1
    joined = "；".join(errors)
    for needle in ("第 2 行", "第 3 行", "第 4 行", "第 5 行", "第 6 行",
                   "未知占位符", "缺少班号", "学部名称为空", "年级名称不能为空", "需要 3 段"):
        assert needle in joined, needle


def test_explain_code_names_the_failure():
    r = rule(default_rule_text())
    for code, needle in (("92407", "没有匹配任何规则"),
                         ("12701", "晚于当前学年"),
                         ("abc", "没有能按规则识别的编号"),
                         ("", "编号为空")):
        info, why = explain_code(code, r, NOW)
        assert info is None and needle in why, (code, why)


def test_invalid_regex_rejected_at_load():
    with pytest.raises(RuleError):
        OrgRule.from_config({"orgRule": {"rules": default_rule_text().splitlines(),
                                        "accountPattern": "("}})


# ---------- 下游消费 ----------

def test_account_row_can_use_school_pattern():
    r = rule("G{yy}{cc}|国际部|高一,高二,高三", row_pattern=r"^[A-Z]{1,3}-G\d{4}$")
    assert parse_account_row(["国际部高三七班", "gd-G2407", "pwd"], r.account_row_re()) == (
        "gd-G2407", "pwd", "国际部高三七班")


def test_branch_sorting_follows_custom_grade_order():
    from app.domain.org_manage import MemberView

    r = rule("X{yyyy}{cc}|小学部|小一,小二,小三,小四,小五,小六")
    rows = [MemberView("甲", "1", cls="X202107"),
            MemberView("乙", "2", cls="X202407"),
            MemberView("丙", "3", cls="X202607")]
    nodes = build_branches(rows, [], rule=r)
    assert [n.grade for n in nodes] == ["小一", "小三", "小六"]
    assert [n.label for n in nodes][1] == "小学部小三七班"


def test_rule_change_updates_generated_remarks_only(monkeypatch):
    from types import SimpleNamespace

    from app.ui import settings_page as mod

    old = OrgRule.from_config({"orgRule": {"prefixes": {"1": "普通高中", "2": "国际高中"},
                                          "noPrefix": "初中部"}})
    new = rule("1{yy}{cc}|附中|高一,高二,高三")
    accounts = [
        {"userName": "ssfz12407", "remark": "普通高中高三七班"},  # 旧规则生成
        {"userName": "ssfz22405", "remark": "我自己填的"},          # 手填
        {"userName": "ssfz12601", "remark": ""},                    # 空
    ]
    monkeypatch.setattr(mod.store, "update", lambda fn: None)
    fake = SimpleNamespace(accounts=accounts, _rule=new)
    assert mod.SettingsPage._repair_remarks(fake, old) == 2
    assert accounts[0]["remark"] == "附中高三七班"
    assert accounts[1]["remark"] == "我自己填的"
    assert accounts[2]["remark"] == "附中高一一班"


def test_rule_change_keeps_remark_when_new_rule_cannot_parse(monkeypatch):
    from types import SimpleNamespace

    from app.ui import settings_page as mod

    old = OrgRule.from_config({"orgRule": {"prefixes": {"1": "普通高中"}}})
    accounts = [{"userName": "ssfz12407", "remark": "普通高中高三七班"}]
    monkeypatch.setattr(mod.store, "update", lambda fn: None)
    fake = SimpleNamespace(accounts=accounts,
                           _rule=rule("X{yyyy}{cc}|小学部|小一"))
    assert mod.SettingsPage._repair_remarks(fake, old) == 0
    assert accounts[0]["remark"] == "普通高中高三七班"
