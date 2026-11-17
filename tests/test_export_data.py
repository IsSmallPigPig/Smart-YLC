"""导出数据：按班级归类、五类明细、分班与合并两种排法，始终落一个 xlsx。"""

from __future__ import annotations

import os

import pytest

from app.domain.check_rules import MemberRecord
from app.services import export_data


def member(mid, name, mobile, **kw):
    base = dict(member_id=mid, name=name, mobile=mobile,
                real_name_status="通过", income="职业为学生无收入",
                report_data_status="审核通过", ebook_status="已上传",
                unpaid_total=0.0, unpaid_months=0)
    base.update(kw)
    return base


def cfg():
    return {
        "accounts": [
            {"userName": "ssfz12407", "remark": "普通高中高三七班", "password": "x"},
            {"userName": "ssfz2407", "remark": "初中部初三七班", "password": "y"},
        ],
        "memberCache": {
            "ssfz12407": {
                "branch": "普通高中高三七班",
                "members": [
                    member("1", "张三", "13800000001", org_name="高三七班"),
                    member("2", "李四", "13800000002", real_name_status="未通过",
                           income="", report_data_status="审核未通过",
                           ebook_status="未上传", unpaid_total=12.0, unpaid_months=2,
                           org_name="高三七班"),
                    member("3", "王五", "13800000003",
                           report_data_status="电子志愿书待上传", ebook_status="未上传",
                           org_name="高三七班"),
                ],
            },
            "ssfz2407": {
                "branch": "初中部初三七班",
                "members": [member("4", "赵六", "13800000004", org_name="初三七班")],
            },
        },
        "orgStatsCache": {
            "ssfz12407": {
                "branch": "普通高中高三七班",
                "total": 3, "uploaded": 1,
                "by_kind": {"identity": 1, "report": 1, "archive": 1, "fees": 1},
                "transfer": {"applied": 2, "total": 3, "succeeded": 1,
                             "pending": 1, "returned": 0, "names": ["张三", "李四"]},
                "error_rows": [],
                "updatedAt": "2026-11-14T20:00:00",
            },
        },
    }


def test_dataset_groups_by_class_and_splits_kinds():
    data = export_data.load_dataset(cfg())
    # 分表依据是团员自己的所属组织（班级），不是账号备注
    assert list(data) == ["高三七班", "初三七班"]
    a = data["高三七班"]
    assert len(a.rows["members"]) == 3
    assert [r["姓名"] for r in a.rows["identity"]] == ["李四"]
    assert [r["姓名"] for r in a.rows["archive"]] == ["王五"]
    assert [r["姓名"] for r in a.rows["fee"]] == ["李四"]
    assert a.rows["fee"][0]["欠缴金额"] == 12.0
    assert a.rows["fee"][0]["欠缴月数"] == 2
    assert [r["姓名"] for r in a.rows["transfer"]] == ["张三", "李四"]
    assert data["初三七班"].rows["identity"] == []
    assert a.rows["members"][0]["班级"] == "高三七班"


def test_split_mode_makes_one_sheet_per_content():
    data = export_data.load_dataset(cfg())["高三七班"]
    sheets = export_data.build_sheets(data, ["members", "fee"], "split")
    assert list(sheets) == ["团员信息", "未缴名单"]
    assert sheets["团员信息"][0] == export_data.HEADERS["members"]


def test_single_mode_merges_everything_with_category_column():
    data = export_data.load_dataset(cfg())["高三七班"]
    sheets = export_data.build_sheets(
        data, ["members", "fee", "identity"], "single")
    assert list(sheets) == [export_data.SINGLE_SHEET]
    headers, rows = sheets[export_data.SINGLE_SHEET]
    assert headers == export_data.SINGLE_HEADERS
    assert {r["类别"] for r in rows} == {"所属团员信息表", "未缴费团员名单", "信息错误名单"}
    assert len(rows) == 3 + 1 + 1
    fee_row = next(r for r in rows if r["类别"] == "未缴费团员名单")
    assert "12.0 元" in fee_row["说明"] and "2 个月" in fee_row["说明"]


def test_class_sheet_merges_every_kind_into_one_table():
    """分班导出：一个班一张表，四类异常按人合并，只靠「异常」列区分。"""
    data = export_data.load_dataset(cfg())["高三七班"]
    headers, rows = export_data.build_class_sheet(
        data, ["members", "fee", "identity", "archive", "transfer"])
    assert headers == export_data.CLASS_HEADERS
    by_name = {r["姓名"]: r for r in rows}
    assert set(by_name) == {"张三", "李四", "王五"}     # 一人一行，不按类别重复
    assert "欠缴团费 12.0 元（2 个月）" in by_name["李四"]["异常"]
    assert "信息错误" in by_name["李四"]["异常"]
    assert "没有档案" in by_name["王五"]["异常"]
    assert "转接申请" in by_name["张三"]["异常"]
    # 团员信息只提供字段，不算异常
    assert by_name["张三"]["异常"].count("；") <= 1
    assert by_name["李四"]["手机号码"] == "13800000002"


def test_merged_export_writes_one_workbook(tmp_path):
    target = str(tmp_path / "全部.xlsx")
    result = export_data.run_export(
        cfg(), ["members", "identity"], "split", "merged", target)
    assert result == {"files": 1, "rows": 4 + 1, "branches": 2}

    from openpyxl import load_workbook

    wb = load_workbook(target)
    assert wb.sheetnames == ["团员信息", "错误名单"]
    ws = wb["错误名单"]
    assert ws.max_row == 2  # 表头 + 李四
    assert ws.cell(1, 1).value == "班级"
    assert ws.cell(2, 1).value == "高三七班"


def test_branch_export_writes_one_sheet_per_class(tmp_path):
    """分班导出：一个班一张表，勾的几类内容都并进同一张表。"""
    target = str(tmp_path / "分班.xlsx")
    result = export_data.run_export(
        cfg(), ["members", "fee", "identity"], "split", "branch", target)
    assert result == {"files": 1, "rows": 3 + 1 + 1 + 1, "branches": 2}

    from openpyxl import load_workbook

    wb = load_workbook(target)
    assert wb.sheetnames == ["高三七班", "初三七班"]
    ws = wb["高三七班"]
    assert [c.value for c in ws[1]] == export_data.CLASS_HEADERS
    assert ws.max_row == 4          # 表头 + 三个人
    assert "异常" == ws.cell(1, 9).value


def test_branch_export_ignores_presentation_mode(tmp_path):
    """呈现方式对分班导出不起作用：两种排法写出同一份结构。"""
    from openpyxl import load_workbook

    one = str(tmp_path / "a.xlsx")
    two = str(tmp_path / "b.xlsx")
    export_data.run_export(cfg(), ["members", "fee"], "split", "branch", one)
    export_data.run_export(cfg(), ["members", "fee"], "single", "branch", two)
    a, b = load_workbook(one), load_workbook(two)
    assert a.sheetnames == b.sheetnames == ["高三七班", "初三七班"]
    assert [c.value for c in a["高三七班"][1]] == [c.value for c in b["高三七班"][1]]


def test_empty_selection_and_missing_cache_raise():
    with pytest.raises(ValueError):
        export_data.run_export(cfg(), [], "split", "merged", "/tmp/x.xlsx")
    with pytest.raises(ValueError):
        export_data.run_export(
            {"accounts": [], "memberCache": {}, "orgStatsCache": {}},
            ["members"], "split", "merged", "/tmp/x.xlsx")


def test_stats_error_rows_used_when_member_cache_missing(tmp_path):
    cfg_ = {
        "accounts": [{"userName": "ssfz12407", "remark": "高三七班"}],
        "memberCache": {},
        "orgStatsCache": {
            "ssfz12407": {
                "branch": "高三七班",
                "error_rows": [
                    {"name": "李四", "mobile": "13800000002",
                     "kinds": "信息错误、欠缴团费", "problems": "实名未通过", "branch": "高三七班"},
                ],
                "transfer": {"names": ["张三"]},
            },
        },
    }
    data = export_data.load_dataset(cfg_)["高三七班"]
    assert [r["姓名"] for r in data.rows["identity"]] == ["李四"]
    assert [r["姓名"] for r in data.rows["fee"]] == ["李四"]
    assert [r["姓名"] for r in data.rows["transfer"]] == ["张三"]
    assert data.rows["members"] == []


def test_illegal_branch_name_is_sanitized(tmp_path):
    cfg_ = {
        "accounts": [{"userName": "ssfz1", "remark": '高三/七班:一'}],
        "memberCache": {"ssfz1": {"branch": '高三/七班:一', "members": [
            member("1", "张三", "13800000001")]}},
        "orgStatsCache": {},
    }
    target = str(tmp_path / "分班.xlsx")
    export_data.run_export(cfg_, ["members"], "split", "branch", target)
    from openpyxl import load_workbook

    assert load_workbook(target).sheetnames == ["高三七班一"]


def test_workbook_without_rows_still_has_a_sheet(tmp_path):
    target = str(tmp_path / "空.xlsx")
    export_data.save_workbook(target, {})
    assert os.path.isfile(target)

    from openpyxl import load_workbook

    assert load_workbook(target).sheetnames == ["导出数据"]


def test_control_chars_in_cells_do_not_break_export(tmp_path):
    """系统抓回来的姓名带控制字符时导出照常成功，不再报
    「<乱码> cannot be used in worksheets」。"""
    cfg_ = cfg()
    members = cfg_["memberCache"]["ssfz12407"]["members"]
    members[0]["name"] = "张\x07三\x00"      # BEL + NUL：openpyxl 直接拒绝写入
    members[1]["work_unit"] = "公\x1F司"
    target = str(tmp_path / "dirty.xlsx")
    result = export_data.run_export(cfg_, ["members"], "split", "merged", target)
    assert result["rows"] >= 2

    from openpyxl import load_workbook

    ws = load_workbook(target).active
    names = [row[1].value for row in ws.iter_rows(min_row=2)]
    assert "张三" in names


def test_control_chars_in_sheet_name_do_not_break_export(tmp_path):
    cfg_ = {
        "accounts": [{"userName": "ssfz1", "remark": "高三\x07(七)班"}],
        "memberCache": {"ssfz1": {"branch": "高三\x07(七)班", "fetchedAt": "", "members": [
            member("1", "张三", "13800000001")]}},
        "orgStatsCache": {},
    }
    target = str(tmp_path / "ctrl_sheet.xlsx")
    export_data.run_export(cfg_, ["members"], "split", "branch", target)
    from openpyxl import load_workbook

    assert load_workbook(target).sheetnames == ["高三(七)班"]


def test_clean_helper_only_touches_strings():
    from app.services.xlsx_clean import clean

    assert clean("a\x0b\x1fb") == "ab"
    assert clean(12.5) == 12.5
    assert clean(None) is None
