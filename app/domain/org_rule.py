"""团支部编号规则（纯函数）：编号 → 学部 + 年级 + 班级，用于预填账号备注与树状筛选。

学校规则（学部前缀映射可在「系统配置」修改）：
  5 位编号 = 学部前缀(1位) + 入学年份后两位(2位) + 班号(2位)
    1 = 普通高中，2 = 国际高中；12407 → 普通高中 高三(24级) 七班
  4 位编号（无学部前缀）= 初中部；2407 → 初中部 初三(24级) 七班

年级按当前学年推算：学年起始年（8 月起算新学年）- 入学年
  0 → 高一/初一，1 → 高二/初二，2 → 高三/初三，≥3 → 已毕业。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

CODE_DIGITS_RE = re.compile(r"\d+")


@dataclass
class OrgRule:
    """学部前缀映射；无前缀编号归 no_prefix 学部。年级 2 位、班号 2 位固定。"""

    prefixes: dict[str, str] = field(default_factory=lambda: {"1": "普通高中", "2": "国际高中"})
    no_prefix: str = "初中部"

    @staticmethod
    def parse_prefix_text(text: str) -> dict[str, str]:
        """「1=普通高中，2=国际高中」→ {"1": "普通高中", "2": "国际高中"}（分隔符支持 ，,；;、 空格）。"""
        out: dict[str, str] = {}
        for part in re.split(r"[，,；;、\s]+", text or ""):
            if "=" not in part:
                continue
            k, _, v = part.partition("=")
            k, v = k.strip(), v.strip()
            if k and v:
                out[k] = v
        return out

    @staticmethod
    def prefix_text(prefixes: dict[str, str]) -> str:
        return "，".join(f"{k}={v}" for k, v in sorted(prefixes.items()))


@dataclass
class ClassInfo:
    dept: str        # 学部：普通高中 / 国际高中 / 初中部
    grade: str       # 高一…高三 / 初一…初三 / 已毕业
    class_no: int    # 班号（整数）
    label: str       # 完整备注名：普通高中高三七班 / 初中部初三七班

    @property
    def grade_key(self) -> str:
        return f"{self.dept}-{self.grade}"


_CN_NUM = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}


def cn_num(n: int) -> str:
    """班号 → 中文（1-99，如 7→七、21→二十一）。"""
    if not 1 <= n <= 99:
        return str(n)
    if n in _CN_NUM:
        return _CN_NUM[n]
    if n < 20:
        return "十" + _CN_NUM[n - 10]
    tens, ones = divmod(n, 10)
    return _CN_NUM[tens] + "十" + (_CN_NUM[ones] if ones else "")


def school_year_start(now: date | None = None) -> int:
    """当前学年起始年：8 月起算新学年（2026-11 → 2026）。"""
    d = now or date.today()
    return d.year if d.month >= 8 else d.year - 1


HIGH_GRADES = ("高一", "高二", "高三")
JUNIOR_GRADES = ("初一", "初二", "初三")


def _grade_label(enroll_year2: str, junior: bool, now: date | None) -> str:
    diff = school_year_start(now) - (2000 + int(enroll_year2))
    if diff < 0:
        return ""  # 入学年在未来，编号无效
    if diff >= 3:
        return "已毕业"
    return (JUNIOR_GRADES if junior else HIGH_GRADES)[diff]


def parse_class_code(code: str, rule: OrgRule | None = None, now: date | None = None) -> ClassInfo | None:
    """编号 → 学部/年级/班级；位数不符、前缀未知、入学年无效时返回 None。

    前缀只认配置里映射的学部：5 位编号按前缀分流，4 位编号归无前缀学部。"""
    rule = rule or OrgRule()
    digits = "".join(CODE_DIGITS_RE.findall(code or ""))
    if len(digits) == 1 + 2 + 2:
        dept = rule.prefixes.get(digits[0])
        if not dept:
            return None
        body = digits[1:]
        junior = False
    elif len(digits) == 2 + 2:
        dept = rule.no_prefix
        body = digits
        junior = True
    else:
        return None
    grade = _grade_label(body[:2], junior, now)
    if not grade:
        return None
    class_no = int(body[2:])
    return ClassInfo(dept, grade, class_no, f"{dept}{grade}{cn_num(class_no)}班")


def describe_code(code: str, rule: OrgRule | None = None, now: date | None = None) -> str:
    """编号 → 备注名（如「普通高中高三七班」）；解析失败返回空串。"""
    info = parse_class_code(code, rule, now)
    return info.label if info else ""


# ---------- 账号导入行解析（备注第一列、账号第二列、密码第三列） ----------

ACCOUNT_RE = re.compile(r"^[A-Za-z]{0,6}\d{3,}$")  # 如 ssfz12407 / 12407


def parse_account_row(cells: list[str]) -> tuple[str, str, str] | None:
    """从一行单元格里识别 (账号, 密码, 备注)，识别失败返回 None。

    新格式：备注、账号、密码；兼容旧格式：账号、密码、备注。
    判据：能匹配账号样式（≤6 个字母开头 + ≥3 位数字）的列是账号，其后一列是密码；
    多个候选时取数字串最长的一对（账号号段远长于普通密码），
    仍并列时 3 列以上按新格式取第 2 列、2 列按旧格式取第 1 列。"""
    cells = [str(c or "").strip() for c in cells]
    if len(cells) < 2 or not any(cells):
        return None
    pairs = [(i, i + 1) for i in range(len(cells) - 1)
             if ACCOUNT_RE.fullmatch(cells[i]) and cells[i + 1]]
    if pairs:
        best = max(len(CODE_DIGITS_RE.findall(cells[i])[0]) for i, _ in pairs) if pairs else 0
        pairs = [(i, j) for i, j in pairs
                 if len(CODE_DIGITS_RE.findall(cells[i])[0]) == best] or pairs
        i, j = (pairs[0] if len(cells) == 2 else
                (pairs[0] if len(pairs) == 1 else
                 next((p for p in pairs if p[0] == (0 if len(cells) == 2 else 1)), pairs[0])))
        remark = next((c for k, c in enumerate(cells) if k not in (i, j)), "")
        return cells[i], cells[j], remark
    # 没有账号样式列：3 列以上按新格式（备注/账号/密码），2 列按旧格式（账号/密码）
    if len(cells) >= 3:
        remark, account, password = cells[0], cells[1], cells[2]
        return (account, password, remark) if account and password else None
    if cells[0] and cells[1]:
        return cells[0], cells[1], ""
    return None
