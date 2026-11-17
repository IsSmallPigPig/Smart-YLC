"""组织关系转接（对照站点 RelationApi.js 实测）。

接口：GET /orgTransfer/all/applyList（旧网关 tuanapi.12355.net，会话 cookie 鉴权）
参数：page / rows / applicantType=1（组织发起） / primalOid（按转出支部过滤）
注意：响应 total 是站点占位值（0 会被前端替换成 99999999），翻页以「本页行数 < rows」为终止条件。

届别（毕业年份）约定：账号 graduateYear ≤ 当前年份 → 该支部已毕业，列为转出账号。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.constants import API_BASE_URL

AUDIT_STATUS = {
    1: "转出团支部待审核", 2: "转出团支部的上级待审核",
    3: "转入团支部待审核", 4: "转入团支部的上级待审核",
    5: "接转成功",
    6: "转出团支部退回", 7: "转出团支部的上级退回",
    8: "转入团支部退回", 9: "转入团支部的上级退回",
    10: "转出团支部同意", 11: "转出团支部的上级同意", 12: "转入团支部同意",
}
PENDING_MARKS = {1, 2, 3, 4, 10, 11, 12}
RETURNED_MARKS = {6, 7, 8, 9}
SUCCESS_MARK = 5


@dataclass
class TransferSummary:
    """一个支部的转接申请汇总（申请按团员去重，多次申请取最新状态）。"""
    applied: int = 0          # 已发起过转接申请的团员数（去重）
    total: int = 0            # 支部在册总团员数（由调用方填入）
    succeeded: int = 0
    pending: int = 0
    returned: int = 0
    names: list[str] = field(default_factory=list)   # 已申请的团员姓名

    @property
    def ratio(self) -> float:
        return self.applied / self.total if self.total else 0.0


def summarize_transfer(rows: list[dict]) -> TransferSummary:
    """纯函数：applyList 行 → 汇总。同一团员多条申请时取 auditStatus 最大值视为最新进展。"""
    latest: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("mid") or row.get("mName") or "")
        if not key:
            continue
        status = row.get("auditStatus")
        try:
            status = int(status)
        except (TypeError, ValueError):
            continue
        if key not in latest or status > latest[key]:
            latest[key] = status
    s = TransferSummary(applied=len(latest))
    for key, status in latest.items():
        if status == SUCCESS_MARK:
            s.succeeded += 1
        elif status in PENDING_MARKS:
            s.pending += 1
        elif status in RETURNED_MARKS:
            s.returned += 1
    s.names = sorted({str(r.get("mName") or "") for r in rows if isinstance(r, dict) and r.get("mName")})
    return s


def fetch_transfer_applies(client, oid: str, page_size: int = 100, max_pages: int = 30) -> TransferSummary:
    """拉取某支部（作为转出方）的全部转接申请并汇总。"""
    if not oid:
        return TransferSummary()
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        resp = client.http.get(
            API_BASE_URL + "/orgTransfer/all/applyList",
            params={"page": page, "rows": page_size, "applicantType": 1, "primalOid": oid},
            timeout=30,
        )
        data = resp.json()
        batch = data.get("rows") or []
        rows.extend(r for r in batch if isinstance(r, dict))
        if len(batch) < page_size:
            break
    return summarize_transfer(rows)


def parse_graduate_year(value) -> int | None:
    """届别字段 → 毕业年份（int）；空/非法返回 None。"""
    s = str(value or "").strip()
    if not s:
        return None
    try:
        year = int(float(s))
    except (TypeError, ValueError):
        return None
    return year if 2000 <= year <= 2100 else None


def is_graduated(acc: dict, today: date | None = None) -> bool:
    """届别 ≤ 当前年份 → 毕业应转出。未填届别不算毕业。"""
    year = parse_graduate_year(acc.get("graduateYear"))
    if year is None:
        return False
    return year <= (today or date.today()).year
