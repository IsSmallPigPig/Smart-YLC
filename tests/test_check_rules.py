import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.domain.check_rules import (
    CheckTemplate,
    MemberRecord,
    TemplateField,
    build_fee_results,
    compare_with_template,
    cross_check_roster,
    is_unpaid,
)
from app.domain.naming import Roster, RosterRow


def member(**kw) -> MemberRecord:
    base = dict(name="张三", mobile="13800000000", unpaid_total=0, unpaid_months=0)
    base.update(kw)
    return MemberRecord(**base)


def test_not_empty():
    t = CheckTemplate("identity", "t", [TemplateField("real_name_status", "实名认证", "", "notEmpty")])
    assert compare_with_template(member(real_name_status="已认证"), t) == []
    diffs = compare_with_template(member(real_name_status="暂无"), t)
    assert len(diffs) == 1 and diffs[0].expected == "非空且已办理"


def test_contains_and_exact():
    t = CheckTemplate("t", "t", [
        TemplateField("ebook_status", "电子志愿书", "已上传", "contains"),
        TemplateField("political_status", "政治面貌", "共青团员", "exact"),
    ])
    assert compare_with_template(member(ebook_status="已上传待审核", political_status="共青团员"), t) == []
    diffs = compare_with_template(member(ebook_status="未上传", political_status="群众"), t)
    assert {d.key for d in diffs} == {"ebook_status", "political_status"}


def test_numeric():
    t = CheckTemplate("t", "t", [TemplateField("unpaidTotal", "欠费", "0", "numeric")])
    assert compare_with_template(member(unpaid_total=0), t) == []
    assert len(compare_with_template(member(unpaid_total=7), t)) == 1


def test_unpaid_verdict():
    assert is_unpaid(member()).abnormal is False
    v = is_unpaid(member(unpaid_total=5, unpaid_months=2))
    assert v.abnormal and v.amount == 5 and v.months == 2
    assert "未交团费总额 5 元" in v.basis and "累计未交 2 个月" in v.basis


def test_fee_results():
    rs = build_fee_results([member(), member(name="李四", unpaid_total=1)])
    assert len(rs) == 1 and rs[0].member.name == "李四"


def test_cross_check_no_roster():
    r = cross_check_roster([member()], None, "all", [])
    assert r.detected is False and "跳过名单交叉校验" in r.message


def test_cross_check_detected():
    roster = Roster("r", [RosterRow("王五", "13700000000")])
    r = cross_check_roster([member(), member(name="李四")], roster, "all", [])
    assert r.detected is True and r.matched_count == 0
    assert any(d.level == "error" for d in r.diagnostics)


def test_cross_check_match():
    roster = Roster("r", [RosterRow("张三", "13800000000")])
    r = cross_check_roster([member(org_name="高一3班")], roster, "all", [])
    assert r.matched_count == 1 and r.orgs == ["高一3班"]
