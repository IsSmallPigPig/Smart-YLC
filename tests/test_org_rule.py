"""团支部编号规则 + 账号导入行解析 + 上传报到预检。"""

from __future__ import annotations

from datetime import date

from app.domain.org_rule import (
    OrgRule,
    cn_num,
    describe_code,
    parse_account_row,
    parse_class_code,
    school_year_start,
)
from app.domain.upload_routing import upload_block_reason

NOW = date(2026, 11, 14)  # 学年 2026：24 级 → 高三/初三


# ---------- 编号解析 ----------

def test_school_year_start():
    assert school_year_start(date(2026, 11, 14)) == 2026
    assert school_year_start(date(2026, 7, 1)) == 2025  # 7 月还算上一学年
    assert school_year_start(date(2026, 8, 1)) == 2026


def test_parse_5_digit_high_school():
    info = parse_class_code("12407", now=NOW)
    assert info.dept == "普通高中"
    assert info.grade == "高三"
    assert info.class_no == 7
    assert info.label == "普通高中高三七班"


def test_parse_4_digit_junior():
    info = parse_class_code("2407", now=NOW)
    assert info.dept == "初中部"
    assert info.grade == "初三"
    assert info.class_no == 7
    assert info.label == "初中部初三七班"


def test_parse_international_prefix():
    info = parse_class_code("22405", now=NOW)
    assert info.dept == "国际高中"
    assert info.grade == "高三"


def test_parse_grade_diff():
    assert parse_class_code("12601", now=NOW).grade == "高一"  # 26 级
    assert parse_class_code("12501", now=NOW).grade == "高二"  # 25 级
    assert parse_class_code("12301", now=NOW).grade == "已毕业"  # 23 级
    assert parse_class_code("12701", now=NOW) is None  # 入学年在未来


def test_parse_rejects_bad_codes():
    assert parse_class_code("92407", now=NOW) is None          # 前缀未配置
    assert parse_class_code("abc", now=NOW) is None            # 无数字
    assert parse_class_code("124", now=NOW) is None            # 位数不对
    assert describe_code("124", now=NOW) == ""


def test_extract_digits_from_account_name():
    info = parse_class_code("ssfz12407", now=NOW)
    assert info.label == "普通高中高三七班"


def test_custom_rule_prefixes():
    rule = OrgRule(prefixes={"3": "职业高中"}, no_prefix="初中部")
    info = parse_class_code("32402", rule, NOW)
    assert info.dept == "职业高中" and info.class_no == 2
    assert parse_class_code("12407", rule, NOW) is None  # 规则里没有前缀 1


def test_prefix_text_roundtrip():
    text = OrgRule.prefix_text({"1": "普通高中", "2": "国际高中"})
    assert OrgRule.parse_prefix_text(text) == {"1": "普通高中", "2": "国际高中"}
    assert OrgRule.parse_prefix_text("1＝普通高中，2＝国际高中") == {}  # 全角等号不支持，不静默


def test_cn_num():
    assert cn_num(7) == "七"
    assert cn_num(10) == "十"
    assert cn_num(11) == "十一"
    assert cn_num(21) == "二十一"
    assert cn_num(30) == "三十"
    assert cn_num(0) == "0" and cn_num(100) == "100"


# ---------- 账号导入行解析 ----------

def test_parse_row_new_format():
    assert parse_account_row(["普通高中高三七班", "ssfz12407", "pwd123"]) == ("ssfz12407", "pwd123", "普通高中高三七班")


def test_parse_row_old_format():
    assert parse_account_row(["ssfz12407", "pwd123", "高一3班"]) == ("ssfz12407", "pwd123", "高一3班")


def test_parse_row_new_empty_remark():
    # 备注列空 → 空串被 strip 掉后只剩两列，按旧格式识别，备注为空串
    assert parse_account_row(["", "ssfz12407", "pwd123"]) == ("ssfz12407", "pwd123", "")


def test_parse_row_numeric_account():
    assert parse_account_row(["12345678", "pwd1"]) == ("12345678", "pwd1", "")


def test_parse_row_rejects_bad():
    assert parse_account_row(["只有一列"]) is None
    assert parse_account_row(["", ""]) is None


# ---------- 上传报到预检 ----------

def test_upload_block_reason_unreported():
    for s in ("报到被撤回", "报到待审核", "报到被退回", "修改资料待审核", "待省级管理员审核"):
        assert upload_block_reason(s)
    assert "报到" in upload_block_reason("报到待审核")


def test_upload_block_reason_allows_uploaded_ready():
    for s in ("审核通过", "已报到,电子志愿书未上传", "电子志愿书待上传", "", None):
        assert upload_block_reason(s) == ""


# ---------- 上传预检：线上已审核通过（志愿书已上传）跳过重复上传 ----------

def test_upload_skip_reason_skips_uploaded_ebook():
    from app.domain.upload_routing import upload_skip_reason

    for e in ("通过", "已上传"):
        reason = upload_skip_reason("已报到,电子志愿书未上传", e)
        assert reason and "已有档案" in reason
    # 志愿书没通过、报到资料也没通过的 → 不拦
    for e in ("未上传", "-", "", None):
        assert upload_skip_reason("已报到,电子志愿书未上传", e) == ""
    # 未完成报到优先拦截
    assert "报到" in upload_skip_reason("报到待审核", "已上传")


def test_upload_skip_reason_report_approved_counts_as_archived():
    """报到资料审核通过 = 线上已有档案，不管志愿书状态都不再重复上传。"""
    from app.domain.upload_routing import upload_skip_reason

    for e in ("未上传", "-", "", None, "通过"):
        reason = upload_skip_reason("审核通过", e)
        assert reason.startswith("已有档案"), reason
    # 报到被退回时仍以「未完成报到」为准
    assert "未完成报到" in upload_skip_reason("报到被退回", "")
