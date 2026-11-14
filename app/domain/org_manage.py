"""组织管理数据层（纯函数）：本地名单 + 系统成员缓存 → 年级-支部-团员树。

本地名单来自「系统配置」导入的团员名单 xlsx（行含班级编号），系统成员来自
批量登录各支部账号拉取的成员缓存（cfg["memberCache"]）。按 姓名+手机号 合并：
source = local（仅本地）/ system（仅系统）/ both（双方都有）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.check_rules import MemberRecord
from app.domain.org_rule import OrgRule, parse_class_code

SOURCE_LABEL = {"local": "仅本地", "system": "仅系统", "both": "本地+系统"}

# 系统端展示的全部字段（label, MemberRecord 字段名）
SYSTEM_FIELDS = [
    ("实名认证", "real_name_status"),
    ("报到资料状态", "report_data_status"),
    ("电子志愿书", "ebook_status"),
    ("电子团员证照片", "ecard_photo_status"),
    ("政治面貌", "political_status"),
    ("全日制学历", "full_time_education"),
    ("职业", "occupation"),
    ("工作/学习单位", "work_unit"),
    ("入团时间", "join_date"),
    ("报到申请时间", "report_apply_at"),
    ("报到审核时间", "report_audit_at"),
    ("资料修改时间", "profile_modify_at"),
    ("资料修改审核时间", "profile_modify_audit_at"),
    ("奖励状态", "award_status"),
    ("未交团费（元）", "unpaid_total"),
    ("累计未交月份", "unpaid_months"),
    ("所属团支部", "org_name"),
]


def _key(name: str, mobile: str) -> str:
    return f"{name}::{mobile}"


def record_from_dict(d: dict) -> "MemberRecord":
    """memberCache 里的成员字典 → MemberRecord（未知键忽略）。"""
    fields = {f for f in MemberRecord.__dataclass_fields__}
    return MemberRecord(**{k: v for k, v in d.items() if k in fields})


@dataclass
class MemberView:
    name: str
    mobile: str
    cls: str = ""        # 班级编号（如 12407），系统独有成员为空
    term: str = ""       # 学期
    source: str = "local"
    system: MemberRecord | None = None

    @property
    def search_text(self) -> str:
        return f"{self.name} {self.mobile}".lower()


@dataclass
class BranchNode:
    code: str            # 班级编号原值；系统独有支部用账号备注名
    label: str           # 展示名：普通高中高三七班 / 未知支部
    dept: str = ""
    grade: str = ""
    members: list[MemberView] = field(default_factory=list)
    secretary: dict | None = None  # {name, mobile, branch}

    @property
    def secretary_text(self) -> str:
        s = self.secretary
        if not s:
            return "团支书：未登记"
        mob = f" {s.get('mobile', '')}" if s.get("mobile") else ""
        return f"团支书：{s.get('name', '')}{mob}"


def branch_label_for(code: str, rule: OrgRule | None = None, now=None) -> tuple[str, str, str]:
    """班级编号 → (label, dept, grade)；解析失败时 label=编号原文，其余空。"""
    info = parse_class_code(code, rule, now)
    if info is None:
        return (code or "未分组", "", "")
    return info.label, info.dept, info.grade


def build_branches(
    local_rows: list[MemberView],
    system_groups: list[tuple[str, list[MemberRecord]]],
    secretaries: list[dict] | None = None,
    rule: OrgRule | None = None,
    now=None,
) -> list[BranchNode]:
    """合并本地与系统成员 → 支部节点列表（已按 学部-年级-班号 排序）。

    local_rows: 本地名单行（cls=班级编号）；system_groups: [(支部备注名, 系统成员)]。
    secretaries: 团支书名单，branch 字段匹配班级编号或支部名。"""
    secretaries = secretaries or []
    branches: dict[str, BranchNode] = {}
    local_index: dict[str, MemberView] = {}

    def node_for(code: str, fallback: str = "") -> BranchNode:
        if code:
            key = code
        else:
            # 系统独有支部：账号备注名与已建支部 label 相同则并入（本地按编号建键）
            existing = next((n for n in branches.values() if n.label == fallback), None) if fallback else None
            if existing is not None:
                return existing
            key = fallback or "未分组"
        if key not in branches:
            if code:
                label, dept, grade = branch_label_for(code, rule, now)
            else:
                label, dept, grade = fallback or "未分组", "其他", ""
            if not dept:
                label, dept, grade = (label or fallback or "未分组"), "其他", ""
            branches[key] = BranchNode(code=key, label=label, dept=dept, grade=grade)
        return branches[key]

    for m in local_rows:
        node_for(m.cls).members.append(m)
        local_index[_key(m.name, m.mobile)] = m

    for branch_name, records in system_groups:
        for rec in records:
            local = local_index.get(_key(rec.name, rec.mobile))
            if local is not None:
                local.source = "both"
                local.system = rec
            else:
                node = node_for("", branch_name)
                node.members.append(MemberView(rec.name, rec.mobile, source="system", system=rec))

    # 团支书：branch 字段与班级编号或支部名匹配
    for node in branches.values():
        for s in secretaries:
            if s.get("branch") in (node.code, node.label):
                node.secretary = s
                break

    order = (rule or OrgRule()).grade_order()

    def sort_key(n: BranchNode):
        info = parse_class_code(n.code, rule, now) if n.code else None
        return (n.dept == "其他", n.dept, order.get(n.grade, 9),
                info.class_no if info else 0, n.label)

    return sorted(branches.values(), key=sort_key)


def filter_members(branches: list[BranchNode], search: str = "", system_only: bool = False) -> list[BranchNode]:
    """按关键词（姓名/手机号子串）与「仅显示系统名单」过滤；空支部剔除，结构不变。"""
    kw = (search or "").strip().lower()
    out: list[BranchNode] = []
    for n in branches:
        rows = [m for m in n.members
                if (not system_only or m.system is not None)
                and (not kw or kw in m.search_text)]
        if rows:
            out.append(BranchNode(n.code, n.label, n.dept, n.grade, rows, n.secretary))
    return out
