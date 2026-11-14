"""团支部编号规则（纯函数）：学号或账号 → 学部 + 年级 + 班级。

不同学校的编号结构不一样，规则由用户配置，一行一条：

    编号模式|学部|年级名称

编号模式由字面量和占位符组成：
    {yy} 入学年两位    {yyyy} 入学年四位    {g} 年级序号一位    {gg} 年级序号两位
    {c} {cc} {ccc} 班号    {n} {nn} {nnn} 忽略位
年级名称用逗号分隔，个数就是学制，例如 高一,高二,高三 或 七年级,八年级,九年级。
规则按顺序匹配，长编号优先；命中后按该行的年级名称生成备注。

内置默认规则等价于旧版的 5 位与 4 位编号：
    1{yy}{cc}|普通高中|高一,高二,高三
    2{yy}{cc}|国际高中|高一,高二,高三
    {yy}{cc}|初中部|初一,初二,初三

账号里的编号默认按规则模板从末尾做最长匹配，ssfz12407 → 12407，
gdG2407 → G2407；编号若在账号中间，可配 accountPattern 正则并用 code 命名组。

旧配置 {"prefixes": {...}, "noPrefix": "..."} 会自动转成上述文本，数据不丢。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

CODE_DIGITS_RE = re.compile(r"\d+")
ACCOUNT_RE = re.compile(r"^[A-Za-z]{0,6}\d{3,}$")

HIGH_GRADES = ("高一", "高二", "高三")
JUNIOR_GRADES = ("初一", "初二", "初三")
DEFAULT_PREFIXES = {"1": "普通高中", "2": "国际高中"}
DEFAULT_NO_PREFIX = "初中部"
DEFAULT_LABEL = "{dept}{grade}{classCn}班"
DEFAULT_SAMPLES = "12407, 2407"
GRADUATED_LABEL = "已毕业"

# 占位符 → 匹配宽度；字面量按原文精确匹配
TOKEN_WIDTH = {
    "yy": 2, "yyyy": 4,
    "g": 1, "gg": 2,
    "c": 1, "cc": 2, "ccc": 3,
    "n": 1, "nn": 2, "nnn": 3,
}
ANY_TOKEN_RE = re.compile(r"\{([^{}]*)\}")
SPLIT_RE = re.compile(r"[,，、;；\s]+")
SAMPLE_SPLIT_RE = re.compile(r"[,，;；\s]+")


class RuleError(ValueError):
    """规则文本或配置有误，带行号与原因，供界面直接显示。"""


@dataclass(frozen=True)
class CodePattern:
    """一条编号规则：模板、学部名、年级名称表、可选的备注模板。"""

    template: str
    dept: str
    grades: tuple[str, ...] = ()
    label: str = ""

    @property
    def compiled(self) -> tuple[re.Pattern, tuple[tuple[str, int], ...]]:
        return compile_template(self.template)

    @property
    def width(self) -> int:
        _, tokens = self.compiled
        return len(ANY_TOKEN_RE.sub("", self.template)) + sum(w for _, w in tokens)

    @property
    def regex(self) -> re.Pattern:
        return self.compiled[0]

    @property
    def tail_regex(self) -> re.Pattern:
        """末尾锚定版，用来从 ss-12407 这类账号里取出 12407。"""
        return re.compile(self.regex.pattern[1:])

    @property
    def tokens(self) -> tuple[tuple[str, int], ...]:
        return self.compiled[1]

    def to_row(self) -> str:
        row = f"{self.template}|{self.dept}|{','.join(self.grades)}"
        return f"{row}|{self.label}" if self.label else row


@lru_cache(maxsize=256)
def compile_template(template: str) -> tuple[re.Pattern, tuple[tuple[str, int], ...]]:
    """模板 → (整串匹配正则, 按出现顺序的字段表)。未知占位符直接报错。"""
    parts: list[str] = []
    tokens: list[tuple[str, int]] = []
    pos = 0
    for m in ANY_TOKEN_RE.finditer(template):
        parts.append(re.escape(template[pos:m.start()]))
        name = m.group(1)
        if name not in TOKEN_WIDTH:
            raise RuleError(f"未知占位符 {{{name}}}")
        width = TOKEN_WIDTH[name]
        parts.append(rf"(\d{{{width}}})")
        tokens.append((name, width))
        pos = m.end()
    parts.append(re.escape(template[pos:]))
    return re.compile("^" + "".join(parts) + "$"), tuple(tokens)


def default_patterns() -> list[CodePattern]:
    return [
        CodePattern("1{yy}{cc}", "普通高中", HIGH_GRADES),
        CodePattern("2{yy}{cc}", "国际高中", HIGH_GRADES),
        CodePattern("{yy}{cc}", DEFAULT_NO_PREFIX, JUNIOR_GRADES),
    ]


def default_rule_text() -> str:
    return "\n".join(p.to_row() for p in default_patterns())


def sample_codes(rule: "OrgRule") -> list[str]:
    """示例编号输入框 → 编号列表，供预览与规则编辑器共用。"""
    return [s for s in SAMPLE_SPLIT_RE.split(rule.samples or "") if s]


def parse_rule_text(text: str) -> tuple[list[CodePattern], list[str]]:
    """规则文本 → (规则列表, 逐行错误)。错误带行号，不静默跳过任何一行。"""
    patterns: list[CodePattern] = []
    errors: list[str] = []
    for lineno, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) not in (3, 4):
            errors.append(f"第 {lineno} 行需要 3 段，用竖线分隔编号模式、学部、年级名称")
            continue
        template, dept, grade_text = parts[0], parts[1], parts[2]
        label = parts[3] if len(parts) == 4 else ""
        if not dept:
            errors.append(f"第 {lineno} 行学部名称为空")
            continue
        try:
            _, tokens = compile_template(template)
        except RuleError as e:
            errors.append(f"第 {lineno} 行 {e}")
            continue
        if not any(t[0] in ("c", "cc", "ccc") for t in tokens):
            errors.append(f"第 {lineno} 行缺少班号占位符，如 {{cc}}")
            continue
        grades = tuple(g for g in SPLIT_RE.split(grade_text) if g)
        if any(t[0] in ("yy", "yyyy", "g", "gg") for t in tokens) and not grades:
            errors.append(f"第 {lineno} 行含年份或年级序号，年级名称不能为空")
            continue
        patterns.append(CodePattern(template, dept, grades, label))
    return patterns, errors


@dataclass
class OrgRule:
    """一整套学校的编号规则。

    patterns 为空时按 prefixes/no_prefix 生成旧版规则，保证老配置、老调用方
    （OrgRule(prefixes=..., no_prefix=...)）行为不变。
    """

    patterns: list[CodePattern] = field(default_factory=list)
    prefixes: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_PREFIXES))
    no_prefix: str = DEFAULT_NO_PREFIX
    century: int = 2000
    year_start_month: int = 8
    label_template: str = DEFAULT_LABEL
    account_pattern: str = ""
    row_pattern: str = ""
    samples: str = DEFAULT_SAMPLES
    graduated_label: str = GRADUATED_LABEL

    # 旧版界面用过这两个静态 helper，保留以便外部调用与配置回退
    @staticmethod
    def parse_prefix_text(text: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for part in SPLIT_RE.split(text or ""):
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

    def effective_patterns(self) -> list[CodePattern]:
        if self.patterns:
            return self.patterns
        rows = [CodePattern(f"{k}{{yy}}{{cc}}", v, HIGH_GRADES)
                for k, v in sorted(self.prefixes.items())]
        if self.no_prefix:
            rows.append(CodePattern("{yy}{cc}", self.no_prefix, JUNIOR_GRADES))
        return rows

    def rule_text(self) -> str:
        return "\n".join(p.to_row() for p in self.effective_patterns())

    def grade_order(self) -> dict[str, int]:
        out: dict[str, int] = {self.graduated_label: 99}
        for p in self.effective_patterns():
            for i, g in enumerate(p.grades):
                out.setdefault(g, i)
        return out

    def account_row_re(self) -> re.Pattern:
        return re.compile(self.row_pattern) if self.row_pattern else ACCOUNT_RE

    def to_config(self) -> dict:
        return {
            "version": 2,
            "rules": self.rule_text().splitlines(),
            "century": self.century,
            "yearStartMonth": self.year_start_month,
            "label": self.label_template,
            "accountPattern": self.account_pattern,
            "rowPattern": self.row_pattern,
            "samples": self.samples,
            "prefixes": dict(self.prefixes),
            "noPrefix": self.no_prefix,
        }

    @classmethod
    def from_config(cls, cfg: dict | None) -> "OrgRule":
        o = (cfg or {}).get("orgRule") or {}
        if not isinstance(o, dict):
            raise RuleError("orgRule 配置不是对象")
        if "rules" not in o:
            return cls(prefixes=o.get("prefixes") or dict(DEFAULT_PREFIXES),
                       no_prefix=o.get("noPrefix", DEFAULT_NO_PREFIX))
        rows = o.get("rules") or []
        text = "\n".join(str(r) for r in rows) if isinstance(rows, (list, tuple)) else str(rows)
        patterns, errors = parse_rule_text(text)
        if errors:
            raise RuleError("编号规则有误：" + "；".join(errors))
        if not patterns:
            raise RuleError("编号规则为空，至少写一条")
        rule = cls(
            patterns=patterns,
            prefixes=o.get("prefixes") or dict(DEFAULT_PREFIXES),
            no_prefix=o.get("noPrefix", DEFAULT_NO_PREFIX),
            century=_as_int(o.get("century"), 2000, 1900, 2200, "世纪基准"),
            year_start_month=_as_int(o.get("yearStartMonth"), 8, 1, 12, "学年起始月"),
            label_template=str(o.get("label") or DEFAULT_LABEL),
            account_pattern=str(o.get("accountPattern") or ""),
            row_pattern=str(o.get("rowPattern") or ""),
            samples=str(o.get("samples") or DEFAULT_SAMPLES),
            graduated_label=str(o.get("graduatedLabel") or GRADUATED_LABEL),
        )
        _check_regex(rule.account_pattern, "账号提取正则")
        _check_regex(rule.row_pattern, "账号列识别正则")
        return rule


def _as_int(value, default: int, low: int, high: int, name: str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if not low <= n <= high:
        raise RuleError(f"{name} 需在 {low} 到 {high} 之间")
    return n


def _check_regex(pattern: str, name: str) -> None:
    if not pattern:
        return
    try:
        re.compile(pattern)
    except re.error as e:
        raise RuleError(f"{name}无效：{e}") from e


@dataclass
class ClassInfo:
    dept: str
    grade: str
    class_no: int
    label: str
    grade_index: int = 0

    @property
    def grade_key(self) -> str:
        return f"{self.dept}-{self.grade}"


_CN_NUM = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六",
           7: "七", 8: "八", 9: "九", 10: "十"}


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


def school_year_start(now: date | None = None, month: int = 8) -> int:
    """当前学年起始年：默认 8 月起算新学年（2026-11 → 2026）。"""
    d = now or date.today()
    return d.year if d.month >= month else d.year - 1


def _extract_code(raw: str, patterns: list[CodePattern], account_pattern: str) -> tuple[str, str]:
    """账号 → 编号。默认按规则模板从末尾做最长匹配，兼容字母账号前缀。"""
    if account_pattern:
        m = re.search(account_pattern, raw)
        if not m:
            return "", "账号提取正则没有匹配"
        rx = re.compile(account_pattern)
        if "code" in rx.groupindex and m.group("code"):
            return m.group("code"), ""
        return (m.group(1) if m.groups() else m.group(0)), ""

    for p in patterns:
        if p.regex.fullmatch(raw):
            return raw, ""
    # 纯数字必须整体匹配：不然 92407 会被截掉未知前缀，误判成 2407
    if raw.isdigit():
        return "", f"编号 {raw} 没有匹配任何规则"
    for p in sorted(patterns, key=lambda x: -x.width):
        m = p.tail_regex.search(raw)
        if m:
            return m.group(0), ""
    # 账号里用连字符等分隔编号时，去掉分隔符再取一次末尾匹配
    cleaned = re.sub(r"[^0-9A-Za-z]", "", raw)
    if cleaned != raw:
        for p in sorted(patterns, key=lambda x: -x.width):
            m = p.tail_regex.search(cleaned)
            if m:
                return m.group(0), ""
    digits = "".join(CODE_DIGITS_RE.findall(raw))
    if any(p.regex.fullmatch(digits) for p in patterns):
        return digits, ""
    return "", f"账号 {raw} 里没有能按规则识别的编号"


def _format_label(template: str, dept: str, grade: str, class_no: int,
                  code: str, enroll: str) -> str:
    repl = {
        "{dept}": dept,
        "{grade}": grade,
        "{class}": str(class_no),
        "{classCn}": cn_num(class_no),
        "{class2}": f"{class_no:02d}",
        "{code}": code,
        "{enroll}": enroll,
    }
    out = template
    for key, value in repl.items():
        out = out.replace(key, value)
    return out.strip()


def explain_code(code: str, rule: OrgRule | None = None,
                 now: date | None = None) -> tuple[ClassInfo | None, str]:
    """解析编号，失败时返回明确原因（供规则编辑器和表格诊断使用）。"""
    rule = rule or OrgRule()
    patterns = rule.effective_patterns()
    if not patterns:
        return None, "没有配置编号规则"
    raw = str(code or "").strip()
    if not raw:
        return None, "编号为空"
    candidate, reason = _extract_code(raw, patterns, rule.account_pattern)
    if not candidate:
        return None, reason

    for idx in sorted(range(len(patterns)), key=lambda i: -patterns[i].width):
        p = patterns[idx]
        m = p.regex.fullmatch(candidate)
        if not m:
            continue
        enroll = ""
        grade_seq: int | None = None
        class_no: int | None = None
        for (name, _), value in zip(p.tokens, m.groups()):
            if name in ("yy", "yyyy") and not enroll:
                enroll = value
            elif name in ("g", "gg") and grade_seq is None:
                grade_seq = int(value)
            elif name in ("c", "cc", "ccc") and class_no is None:
                class_no = int(value)
        if class_no is None:
            return None, f"第 {idx + 1} 行规则没有读到班号"
        grade = ""
        grade_index = 0
        if grade_seq is not None:
            if not 1 <= grade_seq <= len(p.grades):
                return None, f"年级序号 {grade_seq} 超出第 {idx + 1} 行的年级范围"
            grade = p.grades[grade_seq - 1]
            grade_index = grade_seq - 1
        elif enroll:
            year = int(enroll) if len(enroll) == 4 else rule.century + int(enroll)
            start = school_year_start(now, rule.year_start_month)
            diff = start - year
            if diff < 0:
                return None, f"入学年 {year} 晚于当前学年 {start}"
            if diff >= len(p.grades):
                grade = rule.graduated_label
                grade_index = len(p.grades)
            else:
                grade = p.grades[diff]
                grade_index = diff
        label = _format_label(p.label or rule.label_template, p.dept, grade,
                              class_no, candidate, enroll)
        return ClassInfo(p.dept, grade, class_no, label, grade_index), ""
    return None, f"编号 {candidate} 没有匹配任何规则"


def parse_class_code(code: str, rule: OrgRule | None = None,
                     now: date | None = None) -> ClassInfo | None:
    """编号 → 学部/年级/班级；不匹配或年份无效时返回 None。"""
    return explain_code(code, rule, now)[0]


def describe_code(code: str, rule: OrgRule | None = None,
                  now: date | None = None) -> str:
    """编号 → 备注名（如「普通高中高三七班」）；解析失败返回空串。"""
    info = parse_class_code(code, rule, now)
    return info.label if info else ""


# ---------- 账号导入行解析（备注第一列、账号第二列、密码第三列） ----------

def parse_account_row(cells: list[str], account_re: re.Pattern | None = None) -> tuple[str, str, str] | None:
    """从一行单元格里识别 (账号, 密码, 备注)，识别失败返回 None。

    新格式：备注、账号、密码；兼容旧格式：账号、密码、备注。
    判据：能匹配账号样式（≤6 个字母开头 + ≥3 位数字，或学校自配的
    rowPattern）的列是账号，其后一列是密码；多个候选时取数字串最长的一对，
    仍并列时 3 列以上按新格式取第 2 列、2 列按旧格式取第 1 列。"""
    rx = account_re or ACCOUNT_RE
    cells = [str(c or "").strip() for c in cells]
    if len(cells) < 2 or not any(cells):
        return None
    pairs = [(i, i + 1) for i in range(len(cells) - 1)
             if rx.fullmatch(cells[i]) and cells[i + 1]]
    if pairs:
        best = max(len(CODE_DIGITS_RE.findall(cells[i])[0]) for i, _ in pairs) if pairs else 0
        pairs = [(i, j) for i, j in pairs
                 if len(CODE_DIGITS_RE.findall(cells[i])[0]) == best] or pairs
        i, j = (pairs[0] if len(cells) == 2 else
                (pairs[0] if len(pairs) == 1 else
                 next((p for p in pairs if p[0] == 1), pairs[0])))
        remark = next((c for k, c in enumerate(cells) if k not in (i, j)), "")
        return cells[i], cells[j], remark
    # 没有账号样式列：3 列以上按新格式（备注/账号/密码），2 列按旧格式（账号/密码）
    if len(cells) >= 3:
        remark, account, password = cells[0], cells[1], cells[2]
        return (account, password, remark) if account and password else None
    if cells[0] and cells[1]:
        return cells[0], cells[1], ""
    return None
