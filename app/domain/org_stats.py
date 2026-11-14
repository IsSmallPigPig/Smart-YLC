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
    """错误团员名单一行：一个团员一项或多类错误级异常。"""
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
