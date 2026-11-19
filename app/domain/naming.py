"""命名检查纯函数（移植自 naming.ts）：单个文件名判定 + 报告汇总，不发网络、不碰磁盘。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.constants import (
    FULLWIDTH_MINUS,
    FULLWIDTH_PLUS,
    ILLEGAL_CHARS_RE,
    LIMITS,
    MOBILE_RE,
    NAME_SUFFIX,
    PDF_EXT_RE,
)

MAX_BATCH = LIMITS["MAX_UPLOAD_PER_BATCH"]

SPACE_RE = re.compile(r"[\s\u3000]", re.UNICODE)
# 从微信/QQ/邮件附件存下来的文件名常带零宽字符与方向控制符：界面上完全看不见，
# 却会让「姓名+手机号」的三段式解析失败，报「文件名无法解析」而用户一头雾水。
_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", re.UNICODE)
# 全角数字：中文输入法下「１３８００１３８０００」很常见，逐个映射成半角
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
DIGIT_RE = re.compile(r"\d", re.UNICODE)


@dataclass
class RosterRow:
    name: str
    mobile: str
    extra: dict | None = None


@dataclass
class Roster:
    id: str
    rows: list[RosterRow] = field(default_factory=list)


@dataclass
class Diagnostic:
    level: str  # 'error' | 'warn' | 'info'
    scope: str  # 'file' | 'member' | 'org' | 'parse'
    target: str
    code: str
    reason: str


@dataclass
class NameAnalysis:
    original_name: str
    codes: list[str]
    severity: str  # 'ok' | 'warn' | 'issue'
    problem: str
    suggested_name: str
    reason: str
    parsed: tuple[str, str] | None  # (name, mobile)
    roster_match: bool | None = None
    batch_index: int = 0


def _normalize_width(s: str) -> str:
    return s.replace(FULLWIDTH_PLUS, "+").replace(FULLWIDTH_MINUS, "-")


def analyze_file_name(original_name: str, allow_alternate_separator: bool = False) -> NameAnalysis:
    codes: list[str] = []
    problems: list[str] = []
    reasons: list[str] = []

    # 先归一化再判：NFC 让 macOS/iPhone 存下的分解式字符与名单对得上，
    # 去掉不可见字符，全角数字转半角
    raw = unicodedata.normalize("NFC", _INVISIBLE_RE.sub("", original_name))
    raw = raw.translate(_FULLWIDTH_DIGITS)
    has_pdf_ext = bool(PDF_EXT_RE.search(raw))
    base = PDF_EXT_RE.sub("", raw) if has_pdf_ext else raw
    normalized = _normalize_width(base)
    cleaned = SPACE_RE.sub("", normalized)

    if not has_pdf_ext:
        codes.append("EXT_NOT_PDF")
        problems.append("不是 .pdf 文件")
        reasons.append("站点 PC 端只接受 PDF")
    if base != normalized:
        codes.append("FULLWIDTH_SEPARATOR")
        problems.append("使用了全角 ＋ / －")
        reasons.append("全角符号会导致系统按文件名匹配团员失败")
    if ILLEGAL_CHARS_RE.search(cleaned):
        codes.append("ILLEGAL_CHAR")
        problems.append('含非法字符 \\ / : * ? " < > |')
        reasons.append("文件名非法字符无法上传")
    if SPACE_RE.search(base):
        codes.append("HAS_SPACE")
        problems.append("含空格")
        reasons.append("空格会破坏「姓名+手机号+入团志愿书」解析")

    plus_parts = cleaned.split("+")
    minus_parts = cleaned.split("-")
    used_plus = len(plus_parts) == 3
    used_minus = len(minus_parts) == 3
    mixed = len(plus_parts) > 1 and len(minus_parts) > 1
    parts: list[str] | None = None

    if mixed and len(plus_parts) == 2:
        # 形如 姓名＋手机号-入团志愿书
        tail = plus_parts[1].split("-")
        if len(tail) == 2:
            parts = [plus_parts[0], tail[0], tail[1]]
    if not parts and used_plus:
        parts = plus_parts
    if not parts and used_minus:
        parts = minus_parts

    if mixed:
        codes.append("MIXED_SEPARATOR")
        problems.append("分隔符混用")
        reasons.append("两个分隔符必须一致，统一用半角 +")
    if used_minus and not used_plus:
        if allow_alternate_separator:
            problems.append("使用半角 -（站点可上传）")
            reasons.append("上传说明规定用 +，建议统一")
        else:
            codes.append("ALTERNATE_SEPARATOR")
            problems.append("分隔符不是半角 +")
            reasons.append("按本站上传说明，应使用 姓名+手机号码+入团志愿书.pdf")

    parsed: tuple[str, str] | None = None
    if not parts:
        codes.append("STRUCTURE_MISMATCH")
        problems.append("不是「姓名+手机号码+入团志愿书」三段式")
        reasons.append("无法解析团员姓名与手机号")
    else:
        name, mobile, suffix = parts
        if suffix != NAME_SUFFIX:
            codes.append("SUFFIX_MISMATCH")
            problems.append("后缀不是「入团志愿书」")
            reasons.append(f"应为 {NAME_SUFFIX}，实际为「{suffix or '空'}」")
        if not MOBILE_RE.fullmatch(mobile):
            codes.append("MOBILE_INVALID")
            problems.append("手机号不是 11 位有效号码")
            reasons.append(f"应为 1[3-9] 开头的 11 位数字，实际为「{mobile or '空'}」")
        if not name or ILLEGAL_CHARS_RE.search(name) or DIGIT_RE.search(name):
            codes.append("NAME_INVALID")
            problems.append("姓名段为空或含非法内容")
            reasons.append("姓名段应为中文姓名，不含数字与符号")
        if suffix == NAME_SUFFIX and MOBILE_RE.fullmatch(mobile) and name and not DIGIT_RE.search(name):
            parsed = (name, mobile)

    suggested = f"{parsed[0]}+{parsed[1]}+{NAME_SUFFIX}.pdf" if parsed else f"{cleaned or '未命名'}.pdf"
    hard_issue = len(codes) > 0
    severity = "issue" if hard_issue else ("warn" if (used_minus and not used_plus) else "ok")
    if severity == "ok":
        codes.append("OK")

    return NameAnalysis(
        original_name=raw,
        codes=codes,
        severity=severity,
        problem="；".join(problems) if problems else "无",
        suggested_name=suggested,
        reason="；".join(reasons) if reasons else "符合上传说明",
        parsed=parsed,
    )


def chunk_list(items: list, size: int = MAX_BATCH) -> list[list]:
    if size <= 0:
        raise ValueError("size must be > 0")
    return [items[i : i + size] for i in range(0, len(items), size)]


# ---------- 文档检查：命名之外，再核对 PDF 总页数 ----------

def page_verdict(pages: int | None, expected: int) -> tuple[str, str] | None:
    """页数核对：返回 (problem, reason)；页数正确或未启用（expected<=0）返回 None。

    pages=None 表示 PDF 无法读取（损坏或非 PDF 内容），同样判为问题。"""
    if expected <= 0:
        return None
    if pages is None:
        return "无法读取 PDF 页数", "文件可能损坏或不是有效 PDF，请重新导出后再上传"
    if pages != expected:
        return f"总页数 {pages} 页，应为 {expected} 页", f"志愿书应完整 {expected} 页，请检查是否漏扫或多扫"
    return None


def apply_page_verdict(analysis: NameAnalysis, verdict: tuple[str, str] | None) -> NameAnalysis:
    """把页数核对结果并入命名分析（就地修改，与重复/名单检查同一追加风格）。"""
    if verdict is None:
        return analysis
    problem, reason = verdict
    analysis.codes.append("PAGE_COUNT_MISMATCH")
    analysis.severity = "issue"
    analysis.problem = ("" if analysis.problem == "无" else analysis.problem + "；") + problem
    analysis.reason = ("" if analysis.reason == "符合上传说明" else analysis.reason + "；") + reason
    return analysis


def _key(name: str, mobile: str) -> str:
    return f"{name}::{mobile}"


@dataclass
class NamingSummary:
    total: int
    ok: int
    with_issues: int
    duplicates: int
    missing_files: list[str]
    extra_files: list[str]
    batches: int


@dataclass
class NamingReport:
    scan_dir: str
    scanned_at: str
    roster_id: str | None
    issues: list[NameAnalysis]
    summary: NamingSummary
    batches: list[list[NameAnalysis]]
    diagnostics: list[Diagnostic]


def build_naming_report(
    scan_dir: str,
    issues: list[NameAnalysis],
    roster: Roster | None = None,
) -> NamingReport:
    """重复检测 + 名单交叉 + ≤50 分批；被排除的项都会带诊断原因。"""
    diagnostics: list[Diagnostic] = []
    seen: dict[str, list[NameAnalysis]] = {}

    for issue in issues:
        if not issue.parsed:
            continue
        seen.setdefault(_key(*issue.parsed), []).append(issue)

    for k, lst in seen.items():
        if len(lst) < 2:
            continue
        for issue in lst:
            issue.codes.append("DUPLICATE_MEMBER")
            issue.severity = "issue"
            issue.problem = ("" if issue.problem == "无" else issue.problem + "；") + f"同一团员出现 {len(lst)} 份文件"
            issue.reason = ("" if issue.reason == "符合上传说明" else issue.reason + "；") + "每位团员有且只有一份正式入团志愿书"
        diagnostics.append(
            Diagnostic(
                "warn", "file", k, "ROSTER_NAME_MOBILE_MISMATCH",
                f"同一姓名+手机号匹配到 {len(lst)} 个文件，需人工确认保留哪一份",
            )
        )

    extra_files: list[str] = []
    missing_files: list[str] = []

    if roster is not None:
        by_name: dict[str, list[RosterRow]] = {}
        for row in roster.rows:
            by_name.setdefault(row.name, []).append(row)
        file_keys = set(seen.keys())

        for issue in issues:
            if not issue.parsed:
                continue
            name, mobile = issue.parsed
            candidates = by_name.get(name, [])
            exact = next((r for r in candidates if r.mobile == mobile), None)
            if exact:
                issue.roster_match = True
                continue
            issue.roster_match = False
            if candidates:
                issue.codes.append("ROSTER_NAME_MOBILE_MISMATCH")
                issue.severity = "issue"
                issue.problem = ("" if issue.problem == "无" else issue.problem + "；") + "名单中同名但手机号不一致"
                issue.reason += "；名单里该姓名对应 " + " / ".join(c.mobile for c in candidates)
                diagnostics.append(
                    Diagnostic(
                        "warn", "member", _key(name, mobile), "ROSTER_NAME_MOBILE_MISMATCH",
                        f"名单中「{name}」的手机号是 {' / '.join(c.mobile for c in candidates)}，文件里是 {mobile}",
                    )
                )
            else:
                issue.codes.append("FILE_NOT_ON_ROSTER")
                issue.severity = "issue"
                issue.problem = ("" if issue.problem == "无" else issue.problem + "；") + "不在预设团员名单中"
                issue.reason += "；名单里查无此人"
                extra_files.append(issue.original_name)
                diagnostics.append(
                    Diagnostic("warn", "file", issue.original_name, "FILE_NOT_ON_ROSTER", "文件对应团员不在预设名单中，上传前请确认")
                )

        for row in roster.rows:
            if _key(row.name, row.mobile) not in file_keys:
                missing_files.append(f"{row.name}+{row.mobile}+{NAME_SUFFIX}.pdf")
                diagnostics.append(
                    Diagnostic("info", "member", _key(row.name, row.mobile), "ROSTER_MISSING_FILE", "名单中有该团员，但目录里没有对应志愿书文件")
                )

    batches = chunk_list(issues)
    for i, batch in enumerate(batches, start=1):
        for issue in batch:
            issue.batch_index = i

    return NamingReport(
        scan_dir=scan_dir,
        scanned_at=datetime.now(timezone.utc).isoformat(),
        roster_id=roster.id if roster else None,
        issues=issues,
        summary=NamingSummary(
            total=len(issues),
            ok=sum(1 for i in issues if i.severity == "ok"),
            with_issues=sum(1 for i in issues if i.severity != "ok"),
            duplicates=sum(1 for lst in seen.values() if len(lst) > 1),
            missing_files=missing_files,
            extra_files=extra_files,
            batches=len(batches),
        ),
        batches=batches,
        diagnostics=diagnostics,
    )
