"""组织管理数据层 + 团支书名单导入测试。"""

from app.domain.check_rules import MemberRecord
from app.domain.org_manage import (
    MemberView,
    build_branches,
    branch_label_for,
    filter_members,
)
from app.services.excel import import_secretaries


def _local(name, mobile, cls):
    return MemberView(name, mobile, cls=cls)


def test_branch_label_for():
    label, dept, grade = branch_label_for("12407")
    assert label == "普通高中高三七班" and dept == "普通高中" and grade == "高三"
    label2, dept2, grade2 = branch_label_for("2407")
    assert label2 == "初中部初三七班" and dept2 == "初中部" and grade2 == "初三"
    assert branch_label_for("abc")[1] == ""


def test_build_branches_merge():
    local = [
        _local("张一涵", "13800000001", "12407"),
        _local("刘恒瑞", "13800000002", "12407"),
        _local("王五", "13800000003", "2407"),
    ]
    sys_groups = [
        ("普通高中高三七班", [
            MemberRecord(name="张一涵", mobile="13800000001", org_name="高一3班团支部"),
            MemberRecord(name="李四", mobile="13900000004", org_name="高一3班团支部"),
        ]),
        ("别的支部", [MemberRecord(name="赵六", mobile="13900000005")]),
    ]
    nodes = build_branches(local, sys_groups)
    by_label = {n.label: n for n in nodes}
    assert set(by_label) == {"普通高中高三七班", "初中部初三七班", "别的支部"}

    zs = by_label["普通高中高三七班"]
    z = next(m for m in zs.members if m.name == "张一涵")
    assert z.source == "both" and z.system is not None and z.cls == "12407"
    assert by_label["初中部初三七班"].members[0].source == "local"
    other = by_label["别的支部"]
    assert other.members[0].source == "system" and other.dept == "其他"


def test_branches_sorted():
    local = [_local("甲", "13800000001", "12408"), _local("乙", "13800000002", "12407"),
             _local("丙", "13800000003", "2407")]
    nodes = build_branches(local, [])
    labels = [n.label for n in nodes]
    assert labels.index("初中部初三七班") < labels.index("普通高中高三七班")
    assert labels.index("普通高中高三七班") < labels.index("普通高中高三八班")


def test_secretary_match():
    local = [_local("甲", "13800000001", "12407")]
    secs = [{"branch": "12407", "name": "陈支书", "mobile": "13700000000"}]
    nodes = build_branches(local, [], secs)
    assert nodes[0].secretary_text == "团支书：陈支书 13700000000"
    nodes2 = build_branches(local, [], [{"branch": "普通高中高三七班", "name": "李支书"}])
    assert "李支书" in nodes2[0].secretary_text


def test_filter_members():
    local = [
        _local("张一涵", "13800000001", "12407"),
        _local("王五", "13800000003", "2407"),
    ]
    nodes = build_branches(local, [("普通高中高三七班", [MemberRecord(name="张一涵", mobile="13800000001")])])
    # 仅显示系统名单：王五（仅本地）被过滤
    out = filter_members(nodes, system_only=True)
    names = [m.name for n in out for m in n.members]
    assert names == ["张一涵"]
    # 搜索手机号
    out2 = filter_members(nodes, search="13800000003")
    assert [m.name for n in out2 for m in n.members] == ["王五"]
    # 搜索姓名片段
    out3 = filter_members(nodes, search="一涵")
    assert [m.name for n in out3 for m in n.members] == ["张一涵"]


def test_import_secretaries(tmp_path):
    from openpyxl import Workbook

    p = tmp_path / "sec.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["支部", "团支书", "手机号码"])
    ws.append(["12407", "陈支书", "13700000000"])
    ws.append(["普通高中高三七班", "李支书", ""])
    ws.append(["", "缺支部的行", ""])  # 会被跳过并抛错
    wb.save(p)
    try:
        import_secretaries(str(p))
        raise AssertionError("缺支部行应抛 ValueError")
    except ValueError as e:
        assert "缺支部" in str(e)

    p2 = tmp_path / "sec2.xlsx"
    wb2 = Workbook()
    ws2 = wb2.active
    ws2.append(["支部", "团支书", "手机号码"])
    ws2.append(["12407", "陈支书", "13700000000"])
    ws2.append(["普通高中高三七班", "李支书", "13811111111"])
    wb2.save(p2)
    rows = import_secretaries(str(p2))
    assert rows == [
        {"branch": "12407", "name": "陈支书", "mobile": "13700000000"},
        {"branch": "普通高中高三七班", "name": "李支书", "mobile": "13811111111"},
    ]
