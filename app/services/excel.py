"""团员名单导入（openpyxl）：列名按别名表宽松匹配。"""

from __future__ import annotations

import re

from app.domain.naming import Roster, RosterRow

MOBILE_RE = re.compile(r"1[3-9]\d{9}")
NAME_KEYS = {"姓名", "name", "成员姓名", "团员姓名", "学生姓名"}
MOBILE_KEYS = {"手机号码", "手机号", "mobile", "phone", "联系电话", "电话"}


def import_roster(path: str, roster_id: str = "imported") -> Roster:
    """从 xlsx 导入名单；找不到姓名列时抛 ValueError（不静默）。"""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError("表格为空")

    header = [str(c or "").strip() for c in rows[0]]
    name_col = next((i for i, h in enumerate(header) if h in NAME_KEYS), None)
    mobile_col = next((i for i, h in enumerate(header) if h in MOBILE_KEYS), None)
    if name_col is None or mobile_col is None:
        raise ValueError(f"表头缺少「姓名」或「手机号码」列，实际表头：{header}")

    out = Roster(roster_id)
    skipped: list[str] = []
    for r in rows[1:]:
        if r is None or name_col >= len(r):
            continue
        name = str(r[name_col] or "").strip()
        if not name:
            continue
        mobile_raw = str(r[mobile_col] or "").strip() if mobile_col < len(r) else ""
        m = MOBILE_RE.search(mobile_raw)
        if not m:
            skipped.append(name)
            continue
        out.rows.append(RosterRow(name, m.group(0)))
    wb.close()
    if skipped:
        # 明确告知被过滤的行，绝不静默丢弃
        raise ValueError(f"以下 {len(skipped)} 行手机号无效被跳过：{'、'.join(skipped[:8])}{'…' if len(skipped) > 8 else ''}")
    if not out.rows:
        raise ValueError("名单里没有有效数据行")
    return out
