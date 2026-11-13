"""组织状况统计（纯函数）。"""

from app.domain.check_rules import CheckResult, MemberRecord
from app.domain.org_stats import is_uploaded, summarize_members


def member(**kw) -> MemberRecord:
    base = dict(member_id="m1", name="张三", mobile="13800000000", ebook_status="未上传")
    base.update(kw)
    return MemberRecord(**base)


def test_is_uploaded():
    assert is_uploaded(member(ebook_status="已上传"))
    assert not is_uploaded(member(ebook_status="未上传"))
    assert not is_uploaded(member(ebook_status=""))


def test_summarize_counts_abnormal_member_once():
    m1 = member(ebook_status="已上传")
    m2 = member(member_id="m2", name="李四", mobile="13900000000", unpaid_total=3.5)
    members = [m1, m2]
    results = [
        CheckResult("identity", m2, False, []),
        CheckResult("fees", m2, False, [], 3.5, 1, "欠缴"),
        CheckResult("report", m1, True, []),
    ]
    s = summarize_members(members, results)
    assert s.total == 2 and s.uploaded == 1 and s.abnormal == 1
    assert abs(s.ratio - 0.5) < 1e-9


def test_empty():
    s = summarize_members([], [])
    assert s.total == 0 and s.ratio == 0.0


def test_summarize_by_kind_dedup():
    m1 = member()
    m2 = member(member_id="m2", name="李四", mobile="13900000000", unpaid_total=3.5)
    results = [
        CheckResult("identity", m2, True, []),
        CheckResult("fees", m2, True, [], 3.5, 1, "欠缴"),
    ]
    s = summarize_members([m1, m2], results)
    assert s.abnormal == 1  # 一人多项异常只计一次
    assert s.by_kind["identity"] == 1 and s.by_kind["fees"] == 1 and s.by_kind["report"] == 0
