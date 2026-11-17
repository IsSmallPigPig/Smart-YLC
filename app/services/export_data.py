"""数据导出：五类内容 × 分班或合并。

数据全部来自配置缓存，不请求网络：
  memberCache[account]   = {branch, fetchedAt, members: [MemberRecord dict]}
  orgStatsCache[account] = {branch, total, uploaded, by_kind, transfer, error_rows, updatedAt}

内容键：
  members   所属团员信息表
  fee       未缴费团员名单
  archive   无档案团员名单
  transfer  转接申请名单
  identity  信息错误名单

分班导出（scope=branch）：一个班一张子表，团员信息与各类异常都在同一张表里，
每人一行，「异常」列写清他具体是哪一类问题（多项用「；」隔开）。一个账号可能
覆盖多个班，所以分表的依据是团员自己的所属组织，不是账号备注。

合并导出（scope=merged）：所有班合在一起，再按呈现方式排：
  split   每个选项一张子表
  single  全部内容合进一张表，多一列「类别」
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.xlsx_clean import ILLEGAL_XLSX_CHARS, clean, clean_row

from app.domain.check_rules import (
    MemberRecord,
    build_fee_results,
    build_identity_results,
    build_report_results,
)
from app.domain.check_templates import default_identity_template, default_report_template
from app.domain.org_manage import record_from_dict
from app.domain.org_stats import report_kind

def default_name(title: str, ext: str, *options: str) -> str:
    """导出文件的默认名：功能 + 选项 + 生成时间。

    例如「导出数据-分班-每项一表-团员信息-20261115-0205.xlsx」，
    存下来的文件自己就能说明是什么、怎么导的、什么时候导的。"""
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    parts = [title, *[str(o) for o in options if o], stamp]
    return "-".join(parts) + ext


CONTENT_KEYS = ("members", "fee", "archive", "transfer", "identity")
CONTENT_LABELS = {
    "members": "团员信息",
    "fee": "未缴名单",
    "archive": "无档名单",
    "transfer": "转接名单",
    "identity": "错误名单",
}
CONTENT_TITLES = {
    "members": "所属团员信息表",
    "fee": "未缴费团员名单",
    "archive": "无档案团员名单",
    "transfer": "转接申请名单",
    "identity": "信息错误名单",
}

HEADERS = {
    "members": ["班级", "姓名", "手机号码", "团员编号", "报到身份", "报到情况",
                "电子志愿书", "入团时间", "所属组织"],
    "fee": ["班级", "姓名", "手机号码", "欠缴金额", "欠缴月数", "说明"],
    "archive": ["班级", "姓名", "手机号码", "报到情况", "说明"],
    "transfer": ["班级", "姓名", "申请状态"],
    "identity": ["班级", "姓名", "手机号码", "说明"],
}
SINGLE_HEADERS = ["班级", "类别", "姓名", "手机号码", "说明"]
SINGLE_SHEET = "全部信息"
"""分班导出的统一表头：团员信息与四类异常共用一张表，靠「异常」列区分。"""
CLASS_HEADERS = ["班级", "姓名", "手机号码", "团员编号", "报到身份", "报到情况",
                 "电子志愿书", "入团时间", "异常"]


@dataclass
class BranchData:
    branch: str
    rows: dict[str, list[dict]] = field(
        default_factory=lambda: {key: [] for key in CONTENT_KEYS})

    def count(self, keys) -> int:
        return sum(len(self.rows.get(k) or []) for k in keys)


# ------------------------------------------------------------------ 数据组装

def _class_of(member: MemberRecord, fallback: str) -> str:
    """团员属于哪个班：系统里的「所属组织」，没有就退回账号备注。"""
    return (getattr(member, "org_name", "") or "").strip() or fallback


def _member_rows(members: list[MemberRecord], cls: str) -> list[dict]:
    return [{
        "班级": cls,
        "姓名": m.name,
        "手机号码": m.mobile,
        "团员编号": m.member_id,
        "报到身份": m.real_name_status,
        "报到情况": m.report_data_status,
        "电子志愿书": m.ebook_status,
        "入团时间": m.join_date,
        "所属组织": m.org_name,
    } for m in members]


def _error_rows(results, cls: str):
    """把三项检查结果拆成信息错误、无档案、未缴费三份明细，每人每类只一行。"""
    identity: dict[str, dict] = {}
    archive: dict[str, dict] = {}
    fee: dict[str, dict] = {}

    for r in results:
        m = r.member
        key = m.member_id or f"{m.name}::{m.mobile}"
        if r.kind == "fees":
            if r.abnormal:
                fee[key] = {
                    "班级": cls, "姓名": m.name, "手机号码": m.mobile,
                    "欠缴金额": round(float(m.unpaid_total or 0), 2),
                    "欠缴月数": int(m.unpaid_months or 0),
                    "说明": r.basis or "欠缴团费",
                }
            continue
        if not r.is_error:
            continue
        problems = [d.reason for d in r.diffs if d.severity == "error"]
        if not problems:
            continue
        kind = report_kind(m) if r.kind == "report" else r.kind
        bucket = archive if kind == "archive" else identity
        row = bucket.setdefault(key, {
            "班级": cls, "姓名": m.name, "手机号码": m.mobile,
            "报到情况": m.report_data_status, "说明": [],
        })
        row["说明"].extend(problems)

    for bucket in (identity, archive):
        for row in bucket.values():
            row["说明"] = "；".join(dict.fromkeys(row["说明"]))
    identity_rows = [{"班级": r["班级"], "姓名": r["姓名"], "手机号码": r["手机号码"],
                      "说明": r["说明"]} for r in identity.values()]
    return identity_rows, list(archive.values()), list(fee.values())


def _fill_from_stats(data: BranchData, stats: dict, branch: str):
    """没有成员缓存时用组织状况的错误名单兜底，至少错误类数据不丢。"""
    for e in stats.get("error_rows") or []:
        kinds = str(e.get("kinds") or "")
        base = {"班级": branch, "姓名": e.get("name") or "",
                "手机号码": e.get("mobile") or ""}
        problems = str(e.get("problems") or "")
        if "信息错误" in kinds:
            data.rows["identity"].append({**base, "说明": problems})
        if "没有档案" in kinds:
            data.rows["archive"].append({**base, "报到情况": "", "说明": problems})
        if "欠缴" in kinds:
            data.rows["fee"].append({**base, "欠缴金额": "", "欠缴月数": "",
                                     "说明": problems})


def load_dataset(cfg: dict) -> dict[str, BranchData]:
    """按班级汇总可导出数据。

    班级取团员自己的「所属组织」，一个账号覆盖多个班时各回各的班；取不到才
    退回账号备注。转接申请只有姓名，按本账号的成员表把姓名对回班级，对不上
    就落在账号备注那个班。
    """
    accounts = list(cfg.get("accounts") or [])
    cache = cfg.get("memberCache") or {}
    stats = cfg.get("orgStatsCache") or {}

    branch_of: dict[str, str] = {}
    order: list[str] = []
    for acc in accounts:
        user = str(acc.get("userName") or "")
        if not user:
            continue
        branch_of[user] = str(acc.get("remark") or "").strip() or user
        order.append(user)
    for user in cache:
        if user not in branch_of:
            branch_of[user] = str((cache.get(user) or {}).get("branch") or user)
            order.append(user)

    dataset: dict[str, BranchData] = {}
    for user in order:
        blob = cache.get(user) or {}
        fallback = str(blob.get("branch") or branch_of.get(user) or user).strip() or user
        members = [record_from_dict(d) for d in (blob.get("members") or [])]

        by_class: dict[str, list[MemberRecord]] = {}
        for m in members:
            by_class.setdefault(_class_of(m, fallback), []).append(m)

        if by_class:
            for cls, group in by_class.items():
                data = dataset.setdefault(cls, BranchData(cls))
                data.rows["members"].extend(_member_rows(group, cls))
                results = (
                    build_identity_results(group, default_identity_template())
                    + build_report_results(group, default_report_template())
                    + build_fee_results(group)
                )
                identity, archive, fee = _error_rows(results, cls)
                data.rows["identity"].extend(identity)
                data.rows["archive"].extend(archive)
                data.rows["fee"].extend(fee)
        else:
            _fill_from_stats(dataset.setdefault(fallback, BranchData(fallback)),
                             stats.get(user) or {}, fallback)

        owner_of = {m.name: cls for cls, group in by_class.items() for m in group}
        default_cls = next(iter(by_class), fallback) if by_class else fallback
        transfer = (stats.get(user) or {}).get("transfer") or {}
        for name in transfer.get("names") or []:
            cls = owner_of.get(str(name)) or default_cls
            dataset.setdefault(cls, BranchData(cls)).rows["transfer"].append(
                {"班级": cls, "姓名": name, "申请状态": "已申请"})
    return dataset


def merge_dataset(dataset: dict[str, BranchData]) -> BranchData:
    merged = BranchData("全部班级")
    for data in dataset.values():
        for key in CONTENT_KEYS:
            merged.rows[key].extend(data.rows[key])
    return merged


# ------------------------------------------------------------------ 表结构

def _note_of(key: str, row: dict) -> str:
    if key == "members":
        parts = []
        if row.get("报到情况"):
            parts.append(f"报到情况 {row['报到情况']}")
        if row.get("电子志愿书"):
            parts.append(f"志愿书 {row['电子志愿书']}")
        return "；".join(parts)
    if key == "fee":
        money = row.get("欠缴金额", "")
        months = row.get("欠缴月数", "")
        head = f"{money} 元 / {months} 个月" if money != "" else ""
        return "；".join(x for x in (head, str(row.get("说明") or "")) if x)
    if key == "transfer":
        return str(row.get("申请状态") or "")
    return str(row.get("说明") or "")


def _class_note(key: str, row: dict) -> str:
    """这一行在班表「异常」列里的文字：先说清是哪一类问题，再接系统给的原因。

    团员信息本身不算异常，它只提供姓名/编号等字段。同类原因不重复写：
    欠缴类的说明常常就是「欠缴团费」四个字，跟标题句是同一句话。
    """
    if key == "members":
        return ""
    if key == "fee":
        money, months = row.get("欠缴金额", ""), row.get("欠缴月数", "")
        head = (f"欠缴团费 {money} 元（{months} 个月）" if money != ""
                else "欠缴团费")
    elif key == "archive":
        head = "没有档案"
    elif key == "identity":
        head = "信息错误"
    elif key == "transfer":
        return f"转接申请：{row.get('申请状态') or '已申请'}"
    else:
        return str(row.get("说明") or "")

    detail = str(row.get("说明") or "")
    parts = [head]
    if detail and detail not in head and head not in detail:
        parts.append(detail)
    return "；".join(dict.fromkeys(parts))


def build_sheets(data: BranchData, contents, mode: str = "split"):
    """合并导出用 → {表名: (表头, 行)}；single 模式只有一张「全部信息」。"""
    keys = [k for k in CONTENT_KEYS if k in set(contents or ())]
    if mode == "single":
        rows = []
        for key in keys:
            for row in data.rows[key]:
                rows.append({
                    "班级": row.get("班级", data.branch),
                    "类别": CONTENT_TITLES[key],
                    "姓名": row.get("姓名", ""),
                    "手机号码": row.get("手机号码", ""),
                    "说明": _note_of(key, row),
                })
        return {SINGLE_SHEET: (SINGLE_HEADERS, rows)}

    sheets = {}
    for key in keys:
        sheets[CONTENT_LABELS[key]] = (HEADERS[key], data.rows[key])
    return sheets


def _person_key(row: dict) -> str:
    """同一个人在不同内容里的统一键：优先姓名+手机号，其次团员编号。

    团员信息带编号和手机号，异常清单只带姓名与手机号、转接申请只有姓名，
    键不统一的话同一个人会在班表里出现好几行。"""
    name = str(row.get("姓名") or "")
    mobile = str(row.get("手机号码") or "")
    if name and mobile:
        return f"{name}::{mobile}"
    return str(row.get("团员编号") or "") or name


def build_class_sheet(data: BranchData, contents) -> tuple[list[str], list[dict]]:
    """分班导出用：一个班一张表，团员信息与各类异常按人合成一行。

    同一个人既在团员信息里、又在未缴费名单里时只出现一行：字段取并集，
    「异常」列把几类问题串起来（欠缴金额、无档案原因、信息错误项、转接状态）。
    """
    keys = [k for k in CONTENT_KEYS if k in set(contents or ())]
    people: dict[str, dict] = {}
    order: list[str] = []

    def _lookup(row: dict):
        """→ (键, 已有行或 None)。转接申请只有姓名，唯一同名的人就是他。"""
        key = _person_key(row)
        if key in people:
            return key, people[key]
        if not row.get("团员编号") and not row.get("手机号码"):
            name = str(row.get("姓名") or "")
            matches = [k for k, p in people.items() if p["姓名"] == name]
            if len(matches) == 1:
                return matches[0], people[matches[0]]
        return key, None

    for key in keys:
        for row in data.rows.get(key) or []:
            ident, person = _lookup(row)
            if person is None:
                person = {"班级": data.branch, "姓名": row.get("姓名", ""),
                          "手机号码": row.get("手机号码", ""), "团员编号": "",
                          "报到身份": "", "报到情况": "", "电子志愿书": "",
                          "入团时间": "", "异常": []}
                people[ident] = person
                order.append(ident)
            for field_name in ("团员编号", "手机号码", "报到身份", "报到情况",
                               "电子志愿书", "入团时间"):
                if not person[field_name] and row.get(field_name):
                    person[field_name] = row[field_name]
            note = _class_note(key, row)
            if note and note not in person["异常"]:
                person["异常"].append(note)

    rows = []
    for ident in order:
        person = people[ident]
        person["异常"] = "；".join(person["异常"])
        rows.append(person)
    return CLASS_HEADERS, rows


# ------------------------------------------------------------------ 写盘

_ILLEGAL_SHEET = re.compile(r"[\\/*?:\[\]]")


def _safe_sheet(title: str) -> str:
    # 控制字符先剥掉：班级名同样可能带脏字符，会原样进工作表名
    cleaned = ILLEGAL_XLSX_CHARS.sub("", str(title))
    return _ILLEGAL_SHEET.sub("", cleaned)[:31] or "数据"


def save_workbook(path: str, sheets: dict) -> int:
    """写一个 xlsx，返回写入的数据行数。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    total = 0
    for title, (headers, rows) in sheets.items():
        ws = wb.create_sheet(_safe_sheet(title))
        ws.append([clean(h) for h in headers])
        for row in rows:
            ws.append(clean_row(row, headers))
        total += len(rows)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        ws.freeze_panes = "A2"
        for idx, head in enumerate(headers, 1):
            width = max(len(str(head)) * 2 + 4, 12)
            for row in rows[:300]:
                width = max(width, min(len(str(row.get(head, ""))) + 4, 48))
            ws.column_dimensions[get_column_letter(idx)].width = width
    if not wb.sheetnames:
        ws = wb.create_sheet("导出数据")
        ws.append(["说明"])
        ws.append(["没有可导出的数据"])
    wb.save(path)
    return total


def _unique_sheet(title: str, used: set[str]) -> str:
    """Excel 工作表名最长 31 字符；同名时加序号，不静默覆盖。"""
    base = _safe_sheet(title)
    name, n = base, 1
    while name in used:
        n += 1
        suffix = f"({n})"
        name = base[:31 - len(suffix)] + suffix
    used.add(name)
    return name


def build_export_sheets(dataset: dict[str, BranchData], contents,
                        mode: str = "split", scope: str = "merged") -> dict:
    """把班级数据排成一个工作簿的子表。

    branch  一个班一张子表，团员信息与各类异常都在表内（呈现方式不影响分班导出）
    merged + split  每项内容一张子表（班级列在同一张表里）
    merged + single 全部内容合成一张，多一列「类别」
    """
    if scope != "branch":
        return build_sheets(merge_dataset(dataset), contents, mode)

    sheets: dict = {}
    used: set[str] = set()
    for cls, data in dataset.items():
        headers, rows = build_class_sheet(data, contents)
        if not rows:      # 勾选的内容在这个班没有数据，不建空表
            continue
        sheets[_unique_sheet(cls, used)] = (headers, rows)
    return sheets


def run_export(cfg: dict, contents, mode: str = "split", scope: str = "merged",
               target: str = "") -> dict:
    """统一入口：返回 {files, rows, branches}；参数不合法或没有数据时抛 ValueError。"""
    keys = [k for k in CONTENT_KEYS if k in set(contents or ())]
    if not keys:
        raise ValueError("至少选择一项导出内容")
    if not target:
        raise ValueError("没有选择保存位置")
    dataset = load_dataset(cfg)
    if not dataset:
        raise ValueError("还没有成员缓存，请先在系统配置同步数据")
    rows = sum(data.count(keys) for data in dataset.values())
    save_workbook(target, build_export_sheets(dataset, keys, mode, scope))
    return {"files": 1, "rows": rows, "branches": len(dataset)}
