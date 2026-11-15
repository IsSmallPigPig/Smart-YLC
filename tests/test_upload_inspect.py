"""上传前档案检查：自动改名、页数核对、按支部预组织。"""

from __future__ import annotations

import os

from app.domain.naming import Roster, RosterRow, analyze_file_name
from app.domain.upload_inspect import (MEMBER_MISSING, archived_index, group_by_branch,
                                       inspect, member_index, split_branch_label,
                                       summarize)

ACCOUNTS = [{"userName": "ssfz12407", "remark": "普通高中高三七班", "password": "x"}]


def roster():
    r = Roster("2026秋")
    r.rows.append(RosterRow("张三", "13800000001", {"class": "12407"}))
    r.rows.append(RosterRow("李四", "13800000002", {"class": "12407"}))
    return r


def standard_name(source: str) -> str:
    """从「姓名+手机号+入团志愿书」取出标准名；source 只用来标出错原因。"""
    name, mobile = source.split("+")[:2]
    analysis = analyze_file_name(f"{name}+{mobile}+入团志愿书.pdf")
    assert analysis.parsed, f"{source} 应能解析"
    return analysis.suggested_name


def make(tmp_path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4 test")
    return str(path)


def test_standard_name_routes_to_branch(tmp_path):
    name = standard_name("张三+13800000001")
    path = make(tmp_path, name)
    statuses = inspect([path], 4, ACCOUNTS, roster(), counter=lambda p: 4)
    assert len(statuses) == 1
    s = statuses[0]
    assert s.naming == "ok" and s.page == "ok"
    assert s.account == "ssfz12407"
    assert s.branch == "普通高中高三七班"
    assert s.usable and s.status_text == "正常"


def test_irregular_name_is_renamed_on_disk(tmp_path):
    target = standard_name("张三+13800000001")
    source = "张三-13800000001-入团志愿书.pdf"
    path = make(tmp_path, source)
    statuses = inspect([path], 0, ACCOUNTS, roster(), counter=lambda p: 4)
    s = statuses[0]
    assert s.naming == "fixed"
    assert s.renamed_from == source
    assert os.path.basename(s.path) == target
    assert not (tmp_path / source).exists()
    assert (tmp_path / target).exists()
    assert s.tone == "warn" and s.status_text == "已纠正"


def test_existing_target_blocks_rename(tmp_path):
    target = standard_name("张三+13800000001")
    make(tmp_path, target)
    source = make(tmp_path, "张三-13800000001-入团志愿书.pdf")
    s = inspect([source], 0, ACCOUNTS, roster(), counter=lambda p: 4)[0]
    assert s.naming == "error"
    assert "同名文件已存在" in s.problem
    assert os.path.exists(source)  # 原文件保留，不覆盖


def test_unparsable_name_is_error(tmp_path):
    path = make(tmp_path, "说明.pdf")
    s = inspect([path], 0, ACCOUNTS, roster(), counter=lambda p: 4)[0]
    assert s.naming == "error" and not s.usable
    assert s.problem


def test_page_mismatch_blocks_upload(tmp_path):
    name = standard_name("张三+13800000001")
    path = make(tmp_path, name)
    s = inspect([path], 4, ACCOUNTS, roster(), counter=lambda p: 3)[0]
    assert s.page == "error"
    assert "总页数 3 页，应为 4 页" in s.problem
    assert not s.usable


def test_page_check_skipped_without_expected(tmp_path):
    name = standard_name("张三+13800000001")
    path = make(tmp_path, name)
    s = inspect([path], 0, ACCOUNTS, roster(), counter=lambda p: 99)[0]
    assert s.page == "skip" and s.usable


def test_unroutable_member_is_error(tmp_path):
    name = standard_name("王五+13800000009")
    path = make(tmp_path, name)
    s = inspect([path], 0, ACCOUNTS, roster(), counter=lambda p: 4)[0]
    assert s.account == ""
    assert not s.usable
    assert s.problem


def test_group_and_summary(tmp_path):
    ok_path = make(tmp_path, standard_name("张三+13800000001"))
    bad_path = make(tmp_path, "说明.pdf")
    statuses = inspect([ok_path, bad_path], 0, ACCOUNTS, roster(), counter=lambda p: 4)
    groups = group_by_branch(statuses)
    assert "普通高中高三七班" in groups and "未归类" in groups
    stats = summarize(statuses)
    assert stats == {"total": 2, "ok": 1, "fixed": 0, "missing": 0,
                     "archive": 0, "error": 1, "branches": 1}


def _members(*pairs):
    return member_index({"ssfz12407": {"members": [
        {"name": n, "mobile": m} for n, m in pairs]}})


def test_branch_roster_missing_member_warns_but_keeps_upload(tmp_path):
    """支部名单里查不到只警告：文件仍可上传，状态标「缺团员」。"""
    name = standard_name("张三+13800000001")
    path = make(tmp_path, name)
    s = inspect([path], 0, ACCOUNTS, roster(), counter=lambda p: 4,
                members=_members(("李四", "13800000002")))[0]
    assert s.usable
    assert s.member == "missing"
    assert s.tone == "warn" and s.status_text == "缺团员"
    assert s.notice == MEMBER_MISSING
    assert not s.problem  # 不是拦截级错误


def test_branch_roster_match_marks_member_ok(tmp_path):
    name = standard_name("张三+13800000001")
    path = make(tmp_path, name)
    s = inspect([path], 0, ACCOUNTS, roster(), counter=lambda p: 4,
                members=_members(("张三", "13800000001")))[0]
    assert s.member == "ok" and s.status_text == "正常" and s.tone == "ok"


def test_branch_without_member_cache_is_not_flagged(tmp_path):
    """没同步过组织数据的支部不报警：缺数据 ≠ 缺团员。"""
    name = standard_name("张三+13800000001")
    path = make(tmp_path, name)
    s = inspect([path], 0, ACCOUNTS, roster(), counter=lambda p: 4,
                members=member_index({}))[0]
    assert s.member == "unknown" and s.status_text == "正常"


def test_branch_label_splits_stage_prefix():
    assert split_branch_label("普通高中高三十二班") == ("普通高中", "高三十二班")
    assert split_branch_label("高三十二班") == ("", "高三十二班")
    assert split_branch_label("初中九年级三班") == ("初中", "九年级三班")
    assert split_branch_label("") == ("", "")


def test_report_approved_member_marked_as_archived(tmp_path):
    """报到资料审核通过 = 已有档案：标出来，且不参与上传。"""
    cache = {"ssfz12407": {"members": [
        {"name": "张三", "mobile": "13800000001",
         "system": {"report_data_status": "审核通过"}},
        {"name": "李四", "mobile": "13800000002",
         "system": {"report_data_status": "报到待审核"}},
    ]}}
    archived = archived_index(cache)
    assert archived == {"ssfz12407": {("张三", "13800000001")}}

    zhang = make(tmp_path, standard_name("张三+13800000001"))
    li = make(tmp_path, standard_name("李四+13800000002"))
    statuses = inspect([zhang, li], 0, ACCOUNTS, roster(), counter=lambda p: 4,
                       members=member_index(cache), archived=archived)
    by_name = {s.name: s for s in statuses}

    z = by_name[os.path.basename(zhang)]
    assert z.archive and not z.usable
    assert z.status_text == "已有档案" and z.tone == "muted"
    assert "已有档案" in z.notice

    other = by_name[os.path.basename(li)]
    assert not other.archive and other.usable

    stats = summarize(statuses)
    assert stats["archive"] == 1 and stats["ok"] == 1 and stats["error"] == 0
