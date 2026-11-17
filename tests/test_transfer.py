"""组织关系转接：申请汇总、翻页拉取、届别判定测试。"""

from datetime import date

from app.domain.transfer import (
    TransferSummary,
    is_graduated,
    parse_graduate_year,
    summarize_transfer,
)


def test_summarize_dedup_by_member():
    rows = [
        {"mid": 1, "mName": "张一涵", "auditStatus": 5},
        {"mid": 2, "mName": "李四", "auditStatus": 1},
        {"mid": 1, "mName": "张一涵", "auditStatus": 2},  # 同一人两条，取最新（5）
        {"mid": 2, "mName": "李四", "auditStatus": 6},    # 最新为退回(6)
    ]
    s = summarize_transfer(rows)
    assert s.applied == 2
    assert s.succeeded == 1
    assert s.pending == 0
    assert s.returned == 1
    assert sorted(s.names) == ["张一涵", "李四"]


def test_summarize_ignores_invalid_rows():
    s = summarize_transfer([None, "x", {}, {"mName": "王五", "auditStatus": "abc"}])
    assert s.applied == 0


def test_ratio_zero_total():
    s = TransferSummary(applied=3, total=0)
    assert s.ratio == 0.0
    s.total = 12
    assert abs(s.ratio - 0.25) < 1e-9


def test_graduate_year_parsing():
    assert parse_graduate_year("2023") == 2023
    assert parse_graduate_year(2026) == 2026
    assert parse_graduate_year("abc") is None
    assert parse_graduate_year("") is None
    assert parse_graduate_year("999") is None


def test_is_graduated():
    today = date(2026, 11, 14)
    assert is_graduated({"graduateYear": "2023"}, today)
    assert is_graduated({"graduateYear": 2026}, today)  # 当年应届也算
    assert not is_graduated({"graduateYear": 2027}, today)
    assert not is_graduated({}, today)  # 未填届别不算毕业
