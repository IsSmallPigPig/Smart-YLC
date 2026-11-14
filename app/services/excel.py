"""团员名单导入（openpyxl）：列名按别名表宽松匹配，支持班级列与模板生成。"""

from __future__ import annotations

import re

from app.domain.naming import Roster, RosterRow

MOBILE_RE = re.compile(r"1[3-9]\d{9}")
NAME_KEYS = {"姓名", "name", "成员姓名", "团员姓名", "学生姓名"}
MOBILE_KEYS = {"手机号码", "手机号", "mobile", "phone", "联系电话", "电话"}
CLASS_KEYS = {"班级", "班级编号", "团支部编号", "支部", "支部编号", "class"}

TEMPLATE_HEADERS = ["班级（团支部编号，如 12407）", "姓名"]


def import_roster(path: str, roster_id: str = "imported") -> Roster:
    """从 xlsx 导入名单；找不到姓名列时抛 ValueError（不静默）。

    手机号列可选（模板只需要班级和姓名）：有合法手机号就带上，没有则存空串，
    不再因为缺手机号丢行。班级列可选。"""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError("表格为空")

    header = [str(c or "").strip() for c in rows[0]]
    name_col = next((i for i, h in enumerate(header) if h in NAME_KEYS), None)
    if name_col is None:
        raise ValueError(f"表头缺少「姓名」列，实际表头：{header}")
    mobile_col = next((i for i, h in enumerate(header) if h in MOBILE_KEYS), None)
    class_col = next((i for i, h in enumerate(header) if h in CLASS_KEYS), None)

    out = Roster(roster_id)
    for r in rows[1:]:
        if r is None or name_col >= len(r):
            continue
        name = str(r[name_col] or "").strip()
        if not name:
            continue
        mobile = ""
        if mobile_col is not None and mobile_col < len(r):
            m = MOBILE_RE.search(str(r[mobile_col] or ""))
            if m:
                mobile = m.group(0)
        extra = None
        if class_col is not None and class_col < len(r) and r[class_col] is not None:
            cls = str(r[class_col]).strip()
            if cls:
                extra = {"class": cls}
        out.rows.append(RosterRow(name, mobile, extra))
    wb.close()
    if not out.rows:
        raise ValueError("名单里没有有效数据行")
    return out


def save_roster_template(path: str) -> int:
    """生成名单模板 xlsx（班级 + 姓名 两列），返回写入的示例行数。供「下载模板」按钮使用。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "团员名单"
    ws.append(TEMPLATE_HEADERS)
    ws.append(["12407", "张三"])
    ws.append(["12408", "李四"])
    for col, width in zip("AB", (30, 14)):
        ws.column_dimensions[col].width = width
    wb.save(path)
    return 2


ERROR_ROW_HEADERS = ["姓名", "手机号码", "类别", "问题", "支部"]

SECRETARY_NAME_KEYS = {"团支书", "团支书姓名", "书记", "姓名", "name"}
SECRETARY_BRANCH_KEYS = CLASS_KEYS | {"支部名称", "团支部", "班级名称"}


def import_secretaries(path: str) -> list[dict]:
    """从 xlsx 导入团支书名单 → [{"branch": 支部编号或名称, "name": 姓名, "mobile": 手机}]。

    需要「支部/班级」与「团支书/姓名」两列；手机号列可选。跳过缺支部或缺姓名的行时抛 ValueError。"""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        raise ValueError("表格为空")

    header = [str(c or "").strip() for c in rows[0]]
    branch_col = next((i for i, h in enumerate(header) if h in SECRETARY_BRANCH_KEYS), None)
    name_col = next((i for i, h in enumerate(header) if h in SECRETARY_NAME_KEYS), None)
    mobile_col = next((i for i, h in enumerate(header) if h in MOBILE_KEYS), None)
    if branch_col is None or name_col is None:
        raise ValueError(f"表头缺少「支部/班级」或「团支书/姓名」列，实际表头：{header}")

    out: list[dict] = []
    skipped: list[str] = []
    for r in rows[1:]:
        if r is None:
            continue
        branch = str(r[branch_col]).strip() if branch_col < len(r) and r[branch_col] else ""
        name = str(r[name_col]).strip() if name_col < len(r) and r[name_col] else ""
        if not branch or not name:
            skipped.append(f"第{len(out) + 2}行")
            continue
        mobile = ""
        if mobile_col is not None and mobile_col < len(r) and r[mobile_col] is not None:
            m = MOBILE_RE.search(str(r[mobile_col]))
            mobile = m.group(0) if m else ""
        out.append({"branch": branch, "name": name, "mobile": mobile})
    if skipped:
        raise ValueError(f"以下行缺支部或团支书姓名被跳过：{'、'.join(skipped[:8])}{'…' if len(skipped) > 8 else ''}")
    if not out:
        raise ValueError("名单里没有有效数据行")
    return out


def save_account_template(path: str) -> int:
    """生成账号导入模板 xlsx（列：账号 / 密码 / 备注 / 届别，后两列可留空），返回示例行数。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "团支部账号"
    ws.append(["账号", "密码", "备注", "届别"])
    ws.append(["ssfz12407", "-abc12345", "普通高中高三七班", "2026"])
    ws.append(["ssfz12401", "-abc12345", "", ""])  # 备注与届别可留空：备注按编号规则自动预填
    for col, width in zip("ABCD", (16, 16, 22, 10)):
        ws.column_dimensions[col].width = width
    wb.save(path)
    return 2


def save_error_rows(path: str, rows) -> int:
    """导出错误团员名单 xlsx（rows: ErrorMemberRow 可迭代），返回写入行数。"""
    from openpyxl import Workbook

    items = list(rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "错误团员名单"
    ws.append(ERROR_ROW_HEADERS)
    for r in items:
        ws.append([r.name, r.mobile, r.kinds, r.problems, r.branch])
    for col, width in zip("ABCDE", (14, 18, 16, 60, 22)):
        ws.column_dimensions[col].width = width
    wb.save(path)
    return len(items)
