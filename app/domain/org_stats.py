"""组织状况统计（纯函数）：上传进度比值 + 异常团员按类别汇总。

已上传判据：电子志愿书状态（ebook_status）包含「已上传」；
异常团员 = 报到身份（信息错误）/ 报到情况（没有报道）/ 团员缴费（欠缴团费）任一不通过。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.check_rules import CheckResult, MemberRecord

UPLOADED_MARK = "已上传"

KIND_LABEL = {
    "identity": "信息错误",
    "report": "没有报道",
    "fees": "欠缴团费",
}


def is_uploaded(member: MemberRecord) -> bool:
    return UPLOADED_MARK in member.ebook_status


@dataclass
class OrgStats:
    total: int = 0
    uploaded: int = 0
    abnormal: int = 0
    by_kind: dict[str, int] = field(default_factory=lambda: {"identity": 0, "report": 0, "fees": 0})

    @property
    def ratio(self) -> float:
        return self.uploaded / self.total if self.total else 0.0


def summarize_members(members: list[MemberRecord], results: list[CheckResult]) -> OrgStats:
    """按团员去重统计异常（一个团员多项异常只计一次；分类计数按类别各计一次）。

    只统计错误级结果（CheckResult.is_error）；职业未填等警告级不进仪表盘。"""
    by_ids: dict[str, set[str]] = {"identity": set(), "report": set(), "fees": set()}
    for r in results:
        if r.is_error and r.kind in by_ids:
            by_ids[r.kind].add(r.member.member_id)
    abnormal = len(set().union(*by_ids.values())) if members else 0
    return OrgStats(
        total=len(members),
        uploaded=sum(1 for m in members if is_uploaded(m)),
        abnormal=abnormal,
        by_kind={k: len(v) for k, v in by_ids.items()},
    )
