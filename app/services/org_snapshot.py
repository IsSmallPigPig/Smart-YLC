"""首页概览：从配置缓存离线汇总，不请求网络。

数据源：accounts / memberCache / orgStatsCache / messagesCache / messagesRead。
memberCache 有数据就按成员现算；没有就用 orgStatsCache 里上次统计的结果，
保证刚启动没同步过也有一份可看的数字。
"""

from __future__ import annotations

from app.domain.check_rules import (
    build_fee_results,
    build_identity_results,
    build_report_results,
)
from app.domain.check_templates import default_identity_template, default_report_template
from app.domain.org_manage import record_from_dict
from app.domain.org_stats import summarize_members


def _unread_count(cfg: dict) -> int:
    marks = cfg.get("messagesRead") or {}
    count = 0
    for user, blob in (cfg.get("messagesCache") or {}).items():
        for message in blob.get("messages") or []:
            rid = message.get("receive_id")
            if rid and message.get("status") == 0 and f"{user}:{rid}" not in marks:
                count += 1
    return count


def snapshot(cfg: dict) -> dict:
    accounts = cfg.get("accounts") or []
    cache = cfg.get("memberCache") or {}
    stats = cfg.get("orgStatsCache") or {}

    total = uploaded = 0
    by_kind = {"identity": 0, "report": 0, "archive": 0, "fees": 0}

    if cache:
        for blob in cache.values():
            members = [record_from_dict(d) for d in (blob.get("members") or [])]
            results = (
                build_identity_results(members, default_identity_template())
                + build_report_results(members, default_report_template())
                + build_fee_results(members)
            )
            summary = summarize_members(members, results)
            total += summary.total
            uploaded += summary.uploaded
            for kind, value in summary.by_kind.items():
                by_kind[kind] = by_kind.get(kind, 0) + value
    else:
        for entry in stats.values():
            total += int(entry.get("total") or 0)
            uploaded += int(entry.get("uploaded") or 0)
            for kind, value in (entry.get("by_kind") or {}).items():
                if kind in by_kind:
                    by_kind[kind] += int(value or 0)

    applied = sum(int((entry.get("transfer") or {}).get("applied") or 0)
                  for entry in stats.values())
    updated = max((str(entry.get("updatedAt") or "") for entry in stats.values()),
                  default="")
    return {
        "total": total,
        "uploaded": uploaded,
        "by_kind": by_kind,
        "applied": applied,
        "branches": len(accounts),
        "unread": _unread_count(cfg),
        "updated": updated,
    }
