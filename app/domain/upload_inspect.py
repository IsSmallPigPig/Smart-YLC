"""上传前的档案检查：命名规范化、页数核对、支部团员核对、按团支部预组织。

- 命名：能解析出姓名与手机号的，直接改成标准名；解析不出的保留原样并标错误
- 页数：预期页数 > 0 时核对，不符或读不出都标错误，上传前拦下
- 团员：用本地缓存的支部成员名单核对（姓名+手机号），查不到只警告、不拦上传——
  名单可能是放学籍之后才变动，拦下反而挡住该交的档案
- 路由：按名单班级与账号编号预分配到团支部，解析不到标错误
改名为磁盘操作，只在「目标名不存在」时执行，不覆盖已有文件。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.domain.naming import analyze_file_name, page_verdict
from app.domain.upload_routing import resolve_file_targets, roster_class_lookup

UNSORTED = "未归类"
MEMBER_MISSING = "该支部名单里没有这位团员，请到组织管理核对后再上传"
"""支部备注的学段前缀：标题只留班名，学段单独做成标签。"""
STAGE_PREFIXES = ("普通高中", "职业高中", "高级中学", "高中", "初中", "中职", "小学")


def split_branch_label(label: str) -> tuple[str, str]:
    """支部备注拆成 (学段, 班名)：「普通高中高三十二班」→（普通高中, 高三十二班）。"""
    label = (label or "").strip()
    for prefix in STAGE_PREFIXES:
        if label.startswith(prefix) and len(label) > len(prefix):
            return prefix, label[len(prefix):]
    return "", label


def member_index(cache: dict) -> dict[str, set[tuple[str, str]]]:
    """memberCache → {账号: {(姓名, 手机号)}}；没有成员数据的支部不进索引。

    没有数据与「名单里没有这个人」是两件事：前者不报警，否则每个没同步过
    组织数据的支部都会把所有档案标成缺团员。"""
    index: dict[str, set[tuple[str, str]]] = {}
    for account, blob in (cache or {}).items():
        rows = {(str(d.get("name") or "").strip(), str(d.get("mobile") or "").strip())
                for d in (blob or {}).get("members") or []}
        rows.discard(("", ""))
        if rows:
            index[str(account)] = rows
    return index


def pdf_page_count(path: str) -> int | None:
    """读取 PDF 总页数；损坏或不是有效 PDF 返回 None。"""
    try:
        from pypdf import PdfReader

        return len(PdfReader(path).pages)
    except Exception:
        return None


@dataclass
class FileStatus:
    path: str
    name: str
    pages: int | None = None
    naming: str = "ok"  # ok | fixed | error
    page: str = "skip"  # ok | error | skip
    member: str = "unknown"  # ok | missing | unknown（没有支部名单数据）
    account: str = ""
    branch: str = ""
    problem: str = ""
    notice: str = ""
    renamed_from: str = ""

    @property
    def usable(self) -> bool:
        return self.naming != "error" and self.page != "error" and bool(self.account)

    @property
    def tone(self) -> str:
        if not self.usable:
            return "error"
        return "warn" if self.naming == "fixed" or self.member == "missing" else "ok"

    @property
    def status_text(self) -> str:
        if not self.usable:
            return "异常"
        if self.naming == "fixed":
            return "已纠正"
        return "缺团员" if self.member == "missing" else "正常"


def inspect(files, expected_pages: int, accounts: list[dict], roster,
            auto_fix: bool = True, counter=pdf_page_count,
            members: dict[str, set[tuple[str, str]]] | None = None) -> list[FileStatus]:
    """逐文件检查并返回状态；auto_fix 时把格式不标准但可解析的文件改成标准名。

    members 是 member_index() 的结果，给出时核对支部名单：查不到只写警告说明，
    文件仍然可上传。"""
    branch_of = {a.get("userName", ""): (a.get("remark") or a.get("userName", ""))
                 for a in accounts}
    statuses: list[FileStatus] = []

    for src in files:
        path, name = src, os.path.basename(src)
        analysis = analyze_file_name(name)
        naming, problem, renamed_from = "ok", "", ""

        if analysis.parsed:
            if analysis.suggested_name != name:
                dst = os.path.join(os.path.dirname(path), analysis.suggested_name)
                if os.path.exists(dst):
                    naming, problem = "error", f"同名文件已存在：{analysis.suggested_name}"
                elif auto_fix:
                    try:
                        os.rename(path, dst)
                        renamed_from, path, name = name, dst, analysis.suggested_name
                        naming = "fixed"
                    except OSError as e:
                        naming, problem = "error", f"改名失败：{e}"
                else:
                    naming = "fixed"
        else:
            naming, problem = "error", analysis.problem or "文件名无法解析出姓名与手机号"

        pages, page = None, "skip"
        if expected_pages > 0:
            pages = counter(path)
            verdict = page_verdict(pages, expected_pages)
            page = "error" if verdict else "ok"
            if verdict:
                problem = f"{problem}；{verdict[0]}" if problem else verdict[0]

        statuses.append(FileStatus(path=path, name=name, pages=pages, naming=naming,
                                   page=page, problem=problem, renamed_from=renamed_from))

    # 改名之后再路由，账号对应最终文件名
    parsed = {s.name: a.parsed for s in statuses
              if (a := analyze_file_name(s.name)).parsed}
    targets = resolve_file_targets(
        [s.name for s in statuses], parsed,
        [a.get("userName", "") for a in accounts], roster_class_lookup(roster))

    for status, target in zip(statuses, targets):
        status.account = target.account or ""
        status.branch = branch_of.get(target.account, "")
        if target.problem:
            status.account = ""
            if not status.problem:
                status.problem = target.problem
            if status.naming != "fixed":
                status.naming = "error"

    # 支部团员核对放在最后：只对已路由到支部的文件查，缺人不拦上传
    if members:
        for status in statuses:
            table = members.get(status.account) if status.account else None
            pair = parsed.get(status.name)
            if not table or not pair:
                continue
            status.member = "ok" if (pair[0].strip(), pair[1].strip()) in table else "missing"
            if status.member == "missing":
                status.notice = MEMBER_MISSING
    return statuses


def group_by_branch(statuses: list[FileStatus]) -> dict[str, list[FileStatus]]:
    """按支部归类，没有路由到账号的落到「未归类」。"""
    groups: dict[str, list[FileStatus]] = {}
    for status in statuses:
        groups.setdefault(status.branch or UNSORTED, []).append(status)
    return groups


def summarize(statuses: list[FileStatus]) -> dict[str, int]:
    """统计口径：缺团员算警告，不算「正常」，也不算「异常」（仍可上传）。"""
    return {
        "total": len(statuses),
        "ok": sum(1 for s in statuses if s.usable and s.naming == "ok"
                  and s.member != "missing"),
        "fixed": sum(1 for s in statuses if s.usable and s.naming == "fixed"),
        "missing": sum(1 for s in statuses if s.usable and s.member == "missing"),
        "error": sum(1 for s in statuses if not s.usable),
        "branches": len({s.branch for s in statuses if s.account}),
    }
