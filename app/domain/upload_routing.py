"""批量上传按团支部分流（纯函数）：文件名 → 团员 → 班级 → 账号。

约定：名单「班级」列填团支部编号（如 12407），对应账号 ssfz12407
（匹配规则：账号 = 前缀 + 编号，或账号以编号结尾）。
名单只需姓名和班级两列（手机号可选）：先按 (姓名, 手机号) 精确匹配，
查不到时按姓名兜底，同名多班则拦截。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.naming import Roster

CLASS_RE = re.compile(r"\d+")


def digits_of(cls: str) -> str:
    """取班级里的数字串：'高三3班 12407' → '12407'（取最长数字段）。"""
    parts = CLASS_RE.findall(cls or "")
    return max(parts, key=len) if parts else ""


def account_for_class(cls: str, account_names: list[str]) -> str | None:
    d = digits_of(cls)
    if not d:
        return None
    for a in account_names:
        if a == d or a.endswith(d):
            return a
    return None


def roster_class_lookup(roster: Roster | None) -> "ClassIndex":
    """名单 → 班级索引。手机号非必选：先按 (姓名, 手机号) 精确匹配，再按姓名兜底。"""
    pair: dict[tuple[str, str], str] = {}
    by_name: dict[str, set[str]] = {}
    if roster:
        for row in roster.rows:
            cls = str((row.extra or {}).get("class") or "").strip()
            if not cls:
                continue
            pair[(row.name, row.mobile)] = cls
            by_name.setdefault(row.name, set()).add(cls)
    return ClassIndex(pair, {k: sorted(v) for k, v in by_name.items()})


@dataclass
class ClassIndex:
    pair: dict[tuple[str, str], str]
    by_name: dict[str, list[str]]  # 姓名 → 去重后的班级列表


@dataclass
class FileTarget:
    filename: str
    member_name: str = ""
    mobile: str = ""
    cls: str = ""
    account: str = ""
    problem: str = ""  # 空 = 可上传；否则是不可路由的原因（给弹窗/表格展示）


def resolve_file_targets(
    filenames: list[str],
    parsed: dict[str, tuple[str, str]],
    account_names: list[str],
    index: ClassIndex,
) -> list[FileTarget]:
    """parsed: 文件名 → (姓名, 手机号)（命名检查已通过的文件才有）。

    班级匹配：先查 (姓名, 手机号)；名单没有手机号时按姓名兜底，
    同名分布在多个班级则拦截请人工确认。"""
    empty = ClassIndex({}, {})
    index = index or empty
    targets: list[FileTarget] = []
    for fn in filenames:
        pm = parsed.get(fn)
        if not pm:
            targets.append(FileTarget(fn, problem="文件名无法解析出姓名与手机号，先修正命名"))
            continue
        name, mobile = pm
        cls = index.pair.get((name, mobile), "")
        if not cls and index.by_name:
            classes = index.by_name.get(name, [])
            if len(classes) == 1:
                cls = classes[0]
            elif len(classes) > 1:
                targets.append(FileTarget(
                    fn, name, mobile,
                    problem=f"名单中「{name}」出现在多个班级（{'、'.join(classes)}），无法确定支部；请修正名单后再试"))
                continue
        if not cls:
            targets.append(FileTarget(fn, name, mobile, problem=f"名单中找不到 {name}（{mobile}）的班级信息"))
            continue
        acc = account_for_class(cls, account_names)
        if not acc:
            targets.append(FileTarget(fn, name, mobile, cls, problem=f"班级 {cls} 没有对应的团支部账号，请到系统配置添加"))
            continue
        targets.append(FileTarget(fn, name, mobile, cls, acc))
    return targets


# ---------- 上传资格：未完成报到的团员，站点不接受志愿书上传 ----------

# league_member.js auditStatus 语义：0/1/2/4/5/8 报到流程未走完，3/6/7 可传志愿书
UNREPORTED_STATUSES = {"报到被撤回", "报到待审核", "报到被退回", "修改资料待审核", "修改资料被退回", "待省级管理员审核"}

# 电子志愿书状态（volunteerBookModifyStatus 枚举）：这两种说明志愿书已上传且通过审核，
# 线上已有电子志愿书，重复上传没有意义
UPLOADED_EBOOK_STATUSES = {"通过", "已上传"}


def upload_block_reason(report_status: str) -> str:
    """团员报到资料状态 → 拦截原因；可上传返回空串。"""
    s = (report_status or "").strip()
    if s in UNREPORTED_STATUSES:
        return f"该团员未完成报到（{s}），智慧团建暂不接受志愿书上传，需先在网页端完成报到"
    return ""


def upload_skip_reason(report_status: str, ebook_status: str) -> str:
    """上传前预检：未完成报到拦截，线上已有审核通过的电子志愿书跳过重复上传。"""
    blocked = upload_block_reason(report_status)
    if blocked:
        return blocked
    e = (ebook_status or "").strip()
    if e in UPLOADED_EBOOK_STATUSES:
        return f"线上系统已显示审核通过（电子志愿书{e}），无需重复上传"
    return ""
