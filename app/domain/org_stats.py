"""组织状况统计（纯函数）：上传进度比值 + 异常团员按类别汇总。

已上传判据：电子志愿书状态（ebook_status）包含「已上传」；
异常团员 = 报到身份（信息错误）/ 报到情况（没有报道、没有档案）/ 团员缴费（欠缴团费）任一不通过。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.check_rules import CheckResult, MemberRecord

UPLOADED_MARK = "已上传"

# 站点 auditStatus=6「已报到,电子志愿书未上传」/ 7「电子志愿书待上传」：
# 人已报到，缺的是志愿书档案，不是没报到 —— 单独归类，
# 避免把这类团员误标成「没有报道」
NO_FILE_STATUSES = {"电子志愿书待上传", "已报到,电子志愿书未上传"}

KIND_LABEL = {
    "identity": "信息错误",
    "report": "没有报道",
    "archive": "没有档案",
    "fees": "欠缴团费",
}


def report_kind(member: MemberRecord) -> str:
    """报到情况异常的细分：报到资料状态属于「已报到但缺志愿书档案」→ 没有档案。"""
    return ("archive" if (member.report_data_status or "").strip() in NO_FILE_STATUSES
            else "report")


def is_uploaded(member: MemberRecord) -> bool:
    return UPLOADED_MARK in member.ebook_status


@dataclass
class OrgStats:
    total: int = 0
    uploaded: int = 0
    abnormal: int = 0
    by_kind: dict[str, int] = field(default_factory=lambda: {"identity": 0, "report": 0, "archive": 0, "fees": 0})

    @property
    def ratio(self) -> float:
        return self.uploaded / self.total if self.total else 0.0


@dataclass
class ErrorMemberRow:
    """异常团员名单一行：一个团员一项或多类错误级异常。"""
    name: str
    mobile: str
    kinds: str  # 类别，多个用「、」连接
    problems: str  # 问题明细，多条用「；」连接
    branch: str  # 所属支部（账号备注或账号名）


def build_error_rows(results: list[CheckResult], branch: str) -> list[ErrorMemberRow]:
    """从检查结果提取错误级异常团员名单（按团员去重合并；警告级不进名单）。

    支部取产生该批数据的账号备注（无备注回退账号名），保证导出表格能按支部区分。"""
    by_member: dict[str, dict] = {}
    for r in results:
        if r.kind == "fees":
            if not r.abnormal:
                continue
            problems = [r.basis or "欠缴团费"]
        else:
            problems = [d.reason for d in r.diffs if d.severity == "error"]
            if not problems:
                continue  # 仅警告级（如职业未填）不算错误
        m = r.member
        e = by_member.setdefault(m.member_id or f"{m.name}::{m.mobile}", {
            "name": m.name, "mobile": m.mobile, "kinds": [], "problems": [],
        })
        e["kinds"].append(KIND_LABEL.get(r.kind, r.kind) if r.kind != "report"
                          else KIND_LABEL[report_kind(m)])
        e["problems"].extend(problems)
    return [
        ErrorMemberRow(
            name=e["name"], mobile=e["mobile"],
            kinds="、".join(dict.fromkeys(e["kinds"])),
            problems="；".join(dict.fromkeys(e["problems"])),
            branch=branch,
        )
        for e in by_member.values()
    ]


def summarize_members(members: list[MemberRecord], results: list[CheckResult]) -> OrgStats:
    """按团员去重统计异常（一个团员多项异常只计一次；分类计数按类别各计一次）。

    只统计错误级结果（CheckResult.is_error）；职业未填等警告级不进仪表盘。"""
    by_ids: dict[str, set[str]] = {"identity": set(), "report": set(), "archive": set(), "fees": set()}
    for r in results:
        if r.is_error and r.kind in by_ids:
            kind = report_kind(r.member) if r.kind == "report" else r.kind
            by_ids[kind].add(r.member.member_id)
    abnormal = len(set().union(*by_ids.values())) if members else 0
    return OrgStats(
        total=len(members),
        uploaded=sum(1 for m in members if is_uploaded(m)),
        abnormal=abnormal,
        by_kind={k: len(v) for k, v in by_ids.items()},
    )


# ---------- 概览卡口径 ----------

def unpaid_amount(results: list[CheckResult]) -> float:
    """应补缴团费总额：所有欠缴团员的未交金额之和。"""
    return round(sum(r.amount for r in results if r.kind == "fees" and r.abnormal), 2)


def merge_totals(system_total: int, local_total: int) -> tuple[int, int]:
    """概览圆环的总人数与「没有报道」人数。

    本地名单（系统配置里导入的 xlsx）与线上系统的人数经常不等：有的班在线上多了
    转入的新团员，有的班本地名单更全（线上还没同步）。规则取两者较大者作分母，
    差额记作「没有报道」——线上多出来的人说明本地登记缺人，本地多出来的人说明
    系统里还没报到。两者相等时差额为 0。

    返回 (总数, 没有报道人数)。
    """
    system_total = max(int(system_total), 0)
    local_total = max(int(local_total), 0)
    total = max(system_total, local_total)
    return total, max(0, total - system_total)


# ---------- 概览卡：辅助指标 ----------

ECARD_MARK = "已采集"


def ecard_missing(members: list[MemberRecord]) -> int:
    """未采集电子团员证照片的人数。

    检查模板里这一项不算异常（站点自己也不标红），但它挡着团员证下发，
    单独计数给管理员一个可推进的活儿。"""
    return sum(1 for m in members if ECARD_MARK not in (m.ecard_photo_status or ""))


def duplicate_mobiles(members: list[MemberRecord]) -> int:
    """手机号重复的人数（重复组里除第一个人之外的都算）。

    同一个人被两个支部收录、或家长手机号被复用，都会导致报到与通知发错人。"""
    seen: dict[str, int] = {}
    for m in members:
        mobile = (m.mobile or "").strip()
        if mobile:
            seen[mobile] = seen.get(mobile, 0) + 1
    return sum(count - 1 for count in seen.values() if count > 1)


def unpaid_months_total(members: list[MemberRecord]) -> int:
    """欠缴月数合计：衡量补缴工作量，比人数更能反映要收多少个月的账。"""
    return sum(int(m.unpaid_months or 0) for m in members)


def oldest_unpaid(members: list[MemberRecord]) -> int:
    """单个团员最长欠缴月数：用来判断有没有拖了很久的账。"""
    return max((int(m.unpaid_months or 0) for m in members), default=0)


def upload_progress(members: list[MemberRecord]) -> tuple[int, int]:
    """(已上传, 总数)：档案上传进度。"""
    return sum(1 for m in members if is_uploaded(m)), len(members)


def member_kinds(members: list[MemberRecord],
                 results: list[CheckResult]) -> dict[str, int]:
    """四类异常各自的人数（与 summarize_members 同口径，供卡片直接取）。"""
    return summarize_members(members, results).by_kind
