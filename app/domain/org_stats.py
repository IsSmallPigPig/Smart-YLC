"""组织状况统计（纯函数）：上传进度比值 + 异常团员汇总。

已上传判据：电子志愿书状态（ebook_status）包含「已上传」；
异常团员 = 报到身份 / 报到情况 / 团员缴费任一检查项不通过。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.check_rules import CheckResult, MemberRecord

UPLOADED_MARK = "已上传"


def is_uploaded(member: MemberRecord) -> bool:
    return UPLOADED_MARK in member.ebook_status


@dataclass
class OrgStats:
    total: int = 0
    uploaded: int = 0
    abnormal: int = 0

    @property
    def ratio(self) -> float:
        return self.uploaded / self.total if self.total else 0.0


def summarize_members(members: list[MemberRecord], results: list[CheckResult]) -> OrgStats:
    """按团员去重统计异常（一个团员多项异常只计一次）。"""
    bad_ids = {r.member.member_id for r in results if r.abnormal}
    return OrgStats(
        total=len(members),
        uploaded=sum(1 for m in members if is_uploaded(m)),
        abnormal=len(bad_ids),
    )
