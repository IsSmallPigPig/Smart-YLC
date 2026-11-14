"""文档检查扩展：错误团员名单构建（org_stats.build_error_rows）+ 页数核对（naming.page_verdict）。"""

from __future__ import annotations

from app.domain.check_rules import CheckResult, FieldDiff, MemberRecord
from app.domain.naming import NameAnalysis, analyze_file_name, apply_page_verdict, page_verdict
from app.domain.org_stats import build_error_rows


def _member(mid="m1", name="张三", mobile="13800000000", **kw):
    return MemberRecord(member_id=mid, name=name, mobile=mobile, **kw)


# ---------- build_error_rows ----------

def test_error_rows_collects_error_diffs_with_branch():
    m = _member()
    results = [
        CheckResult("identity", m, True, [FieldDiff("realNameStatus", "实名认证", "通过", "未通过", "实名认证 应为「通过」，实际为「未通过」", "error")]),
        CheckResult("report", m, True, [FieldDiff("reportDataStatus", "报到情况", "已报到", "未报到", "报到情况 应为「已报到」，实际为「未报到」", "warning")]),
    ]
    rows = build_error_rows(results, "高一3班团支部")
    assert len(rows) == 1
    r = rows[0]
    assert r.name == "张三" and r.mobile == "13800000000"
    assert r.kinds == "信息错误"  # 警告级差异不算错误
    assert "实名认证" in r.problems and "报到情况" not in r.problems
    assert r.branch == "高一3班团支部"


def test_error_rows_merges_kinds_and_dedups_problems():
    m = _member()
    results = [
        CheckResult("identity", m, True, [FieldDiff("a", "实名认证", "通过", "x", "实名认证未通过", "error")]),
        CheckResult("fees", m, True, [], 3.0, 1, "未交团费总额 3 元；累计未交 1 个月"),
    ]
    rows = build_error_rows(results, "12407")
    assert len(rows) == 1
    assert rows[0].kinds == "信息错误、欠缴团费"
    assert rows[0].problems.count("实名认证未通过") == 1


def test_error_rows_excludes_warning_only_members():
    m = _member(name="李四")
    results = [CheckResult("identity", m, True, [FieldDiff("occupation", "职业", "学生", "（空）", "职业 为空或未办理", "warning")])]
    assert build_error_rows(results, "b") == []


def test_error_rows_fees_use_basis():
    m = _member()
    results = [CheckResult("fees", m, True, [], 5, 2, "未交团费总额 5 元")]
    rows = build_error_rows(results, "12408")
    assert rows[0].kinds == "欠缴团费" and "5 元" in rows[0].problems


# ---------- page_verdict / apply_page_verdict ----------

def test_page_verdict_disabled_when_expected_zero():
    assert page_verdict(10, 0) is None
    assert page_verdict(None, 0) is None


def test_page_verdict_matches_and_mismatches():
    assert page_verdict(4, 4) is None
    assert page_verdict(None, 4) is not None  # 无法读取也算问题
    problem, reason = page_verdict(3, 4)
    assert "3 页" in problem and "4 页" in problem


def test_apply_page_verdict_marks_issue():
    a = analyze_ok()
    apply_page_verdict(a, ("总页数 3 页，应为 4 页", "请检查是否漏扫"))
    assert a.severity == "issue"
    assert "PAGE_COUNT_MISMATCH" in a.codes
    assert "总页数 3 页" in a.problem


def test_apply_page_verdict_none_keeps_ok():
    a = analyze_ok()
    apply_page_verdict(a, None)
    assert a.severity == "ok"


def analyze_ok() -> NameAnalysis:
    return analyze_file_name("张三+13800000000+入团志愿书.pdf")


# ---------- 没有档案（auditStatus 6/7）分类 ----------

def test_error_rows_archive_kind_for_no_file_status():
    """报到资料状态为「电子志愿书待上传/已报到,电子志愿书未上传」→ 归「没有档案」，不再标「没有报道」。"""
    from app.domain.org_stats import KIND_LABEL, report_kind
    m = _member(report_data_status="电子志愿书待上传", ebook_status="未上传")
    assert report_kind(m) == "archive"
    m6 = _member(report_data_status="已报到,电子志愿书未上传")
    assert report_kind(m6) == "archive"
    results = [CheckResult("report", m, True, [FieldDiff(
        "reportDataStatus", "报到资料状态", "审核通过", "电子志愿书待上传",
        "报到资料状态 应为「审核通过」，实际为「电子志愿书待上传」", "error")])]
    rows = build_error_rows(results, "12407")
    assert rows[0].kinds == KIND_LABEL["archive"] == "没有档案"


def test_error_rows_report_kind_for_truly_unreported():
    from app.domain.org_stats import report_kind
    m = _member(report_data_status="报到待审核")
    assert report_kind(m) == "report"
