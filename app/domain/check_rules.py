"""检查规则（移植自 check-rules.ts）：报到身份 / 报到情况 / 团员缴费三项比对。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NOT_EMPTY_EXCLUSIONS = {"暂无", "无", "-"}


@dataclass
class MemberRecord:
    member_id: str = ""
    name: str = ""
    mobile: str = ""
    id_type: str = ""
    real_name_status: str = ""
    political_status: str = ""
    full_time_education: str = ""
    occupation: str = ""
    income: str = ""
    work_unit: str = ""
    join_date: str = ""
    report_apply_at: str = ""
    report_audit_at: str = ""
    profile_modify_at: str = ""
    profile_modify_audit_at: str = ""
    report_data_status: str = ""
    ebook_status: str = ""
    ecard_photo_status: str = ""
    award_status: str = ""
    unpaid_total: float = 0.0
    unpaid_months: int = 0
    oid: str = ""
    org_name: str = ""

    def value_of(self, key: str) -> str:
        if key == "unpaidTotal":
            return str(int(self.unpaid_total)) if self.unpaid_total == int(self.unpaid_total) else str(self.unpaid_total)
        if key == "unpaidMonths":
            return str(self.unpaid_months)
        return getattr(self, key) or ""


@dataclass
class TemplateField:
    key: str
    label: str
    expected: str = ""
    match: str = "exact"  # exact | notEmpty | contains | regex | numeric
    enabled: bool = True


@dataclass
class CheckTemplate:
    kind: str  # identity | report
    title: str
    fields: list[TemplateField] = field(default_factory=list)


@dataclass
class FieldDiff:
    key: str
    label: str
    expected: str
    actual: str
    reason: str


@dataclass
class CheckResult:
    kind: str
    member: MemberRecord
    abnormal: bool
    diffs: list[FieldDiff] = field(default_factory=list)
    amount: float = 0.0
    months: int = 0
    basis: str = ""


@dataclass
class Diagnostic:
    level: str
    scope: str
    target: str
    code: str
    reason: str


def _matches(actual: str, f: TemplateField) -> bool:
    if f.match == "notEmpty":
        return len(actual.strip()) > 0 and actual.strip() not in NOT_EMPTY_EXCLUSIONS
    if f.match == "contains":
        return f.expected in actual
    if f.match == "regex":
        try:
            return re.search(f.expected, actual) is not None
        except re.error:
            return False
    if f.match == "numeric":
        try:
            return float(actual) == float(f.expected)
        except (ValueError, TypeError):
            return False
    return actual.strip() == f.expected.strip()


def compare_with_template(member: MemberRecord, template: CheckTemplate) -> list[FieldDiff]:
    """逐字段比对模板，返回差异明细；不修改成员记录。"""
    diffs: list[FieldDiff] = []
    for f in template.fields:
        if not f.enabled:
            continue
        actual = member.value_of(f.key)
        if _matches(actual, f):
            continue
        expected = "非空且已办理" if f.match == "notEmpty" else f.expected
        reason = (
            f"{f.label} 为空或未办理"
            if f.match == "notEmpty"
            else f"{f.label} 应为「{f.expected}」，实际为「{actual or '空'}」"
        )
        diffs.append(FieldDiff(f.key, f.label, expected, actual or "（空）", reason))
    return diffs


@dataclass
class UnpaidVerdict:
    abnormal: bool
    amount: float
    months: int
    basis: str


def is_unpaid(member: MemberRecord) -> UnpaidVerdict:
    """欠缴：未交团费总额 > 0 或 累计未交月份 > 0。"""
    amount = member.unpaid_total or 0
    months = member.unpaid_months or 0
    abnormal = amount > 0 or months > 0
    parts: list[str] = []
    if amount > 0:
        parts.append(f"未交团费总额 {amount:g} 元")
    if months > 0:
        parts.append(f"累计未交 {months} 个月")
    return UnpaidVerdict(abnormal, amount, months, "；".join(parts) if abnormal else "无欠缴")


def _abnormal_only(kind: str, members: list[MemberRecord], template: CheckTemplate) -> list[CheckResult]:
    out: list[CheckResult] = []
    for m in members:
        diffs = compare_with_template(m, template)
        if diffs:
            out.append(CheckResult(kind, m, True, diffs))
    return out


def build_identity_results(members: list[MemberRecord], template: CheckTemplate) -> list[CheckResult]:
    return _abnormal_only("identity", members, template)


def build_report_results(members: list[MemberRecord], template: CheckTemplate) -> list[CheckResult]:
    return _abnormal_only("report", members, template)


def build_fee_results(members: list[MemberRecord]) -> list[CheckResult]:
    out: list[CheckResult] = []
    for m in members:
        v = is_unpaid(m)
        if v.abnormal:
            out.append(CheckResult("fees", m, True, [], v.amount, v.months, v.basis))
    return out


@dataclass
class RosterCrossCheck:
    matched_count: int
    detected: bool
    orgs: list[str]
    message: str
    diagnostics: list[Diagnostic]


def cross_check_roster(
    members: list[MemberRecord],
    roster,
    scope_mode: str,
    scope_oids: list[str],
) -> RosterCrossCheck:
    """预设团员交叉校验：按 姓名+手机号 匹配。一条都没匹配上 → 名单/账号可能不匹配。"""
    if not roster or not roster.rows:
        return RosterCrossCheck(
            0, False, [], "未配置预设团员名单，跳过名单交叉校验",
            [Diagnostic("info", "org", "全部团支部" if scope_mode == "all" else ",".join(scope_oids),
                        "NO_MEMBER_FROM_ROSTER",
                        "系统配置中没有预设团员名单，无法判断是否匹配；建议在「系统配置」导入团员名单")],
        )

    preset = {f"{r.name}::{r.mobile}" for r in roster.rows}
    by_org: dict[str, int] = {}
    matched = 0
    for m in members:
        if f"{m.name}::{m.mobile}" not in preset:
            continue
        matched += 1
        k = m.org_name or m.oid or "未知团支部"
        by_org[k] = by_org.get(k, 0) + 1

    detected = matched == 0
    diagnostics: list[Diagnostic] = []
    target = "全部团支部" if scope_mode == "all" else ",".join(scope_oids)
    if detected:
        diagnostics.append(Diagnostic("error", "org", target, "NO_MEMBER_FROM_ROSTER",
            f"查询到的 {len(members)} 名团员中，没有任何一名属于系统配置中预设的 {len(roster.rows)} 人名单；请核对名单、团支部账号与查询范围"))
    for org, n in by_org.items():
        diagnostics.append(Diagnostic("info", "org", org, "NO_MEMBER_FROM_ROSTER", f"匹配到预设团员 {n} 人"))

    msg = (
        f"指定范围内没有查到任何预设团员（共查询 {len(members)} 人）。名单、团支部账号或查询范围可能不匹配，请管理员核对。"
        if detected
        else f"共匹配到预设团员 {matched} 人，分布于 {len(by_org)} 个团支部"
    )
    return RosterCrossCheck(matched, detected, list(by_org.keys()), msg, diagnostics)


def build_daily_summary(
    members: list[MemberRecord],
    results: list[CheckResult],
    cross: RosterCrossCheck,
) -> str:
    """每日通知用的摘要（纯函数，可单测）。"""
    by_kind = {"identity": 0, "report": 0, "fees": 0}
    for r in results:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    unpaid_amount = sum(r.amount for r in results if r.kind == "fees")
    parts = [
        f"在册团员 {len(members)} 人",
        f"报到身份异常 {by_kind['identity']}",
        f"报到情况异常 {by_kind['report']}",
        f"欠缴 {by_kind['fees']} 人（{unpaid_amount:g} 元）" if by_kind["fees"] else "团费无欠缴",
    ]
    if cross.detected:
        parts.append("⚠️ 未匹配到任何预设团员，请核对名单与账号")
    return "；".join(parts)
