"""批量上传按团支部分流（纯函数）：文件名 → 团员 → 班级 → 账号。

约定：名单「班级」列填团支部编号（如 12407），对应账号 ssfz12407
（匹配规则：账号 = 前缀 + 编号，或账号以编号结尾）。
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


def roster_class_lookup(roster: Roster | None) -> dict[tuple[str, str], str]:
    """(姓名, 手机号) → 班级。多学期名单合并后查。"""
    out: dict[tuple[str, str], str] = {}
    if not roster:
        return out
    for row in roster.rows:
        cls = str((row.extra or {}).get("class") or "").strip()
        if cls:
            out[(row.name, row.mobile)] = cls
    return out


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
    class_lookup: dict[tuple[str, str], str],
) -> list[FileTarget]:
    """parsed: 文件名 → (姓名, 手机号)（命名检查已通过的文件才有）。"""
    targets: list[FileTarget] = []
    for fn in filenames:
        pm = parsed.get(fn)
        if not pm:
            targets.append(FileTarget(fn, problem="文件名无法解析出姓名与手机号，先修正命名"))
            continue
        name, mobile = pm
        cls = class_lookup.get((name, mobile), "")
        if not cls:
            targets.append(FileTarget(fn, name, mobile, problem=f"名单中找不到 {name}（{mobile}）的班级信息"))
            continue
        acc = account_for_class(cls, account_names)
        if not acc:
            targets.append(FileTarget(fn, name, mobile, cls, problem=f"班级 {cls} 没有对应的团支部账号，请到系统配置添加"))
            continue
        targets.append(FileTarget(fn, name, mobile, cls, acc))
    return targets
