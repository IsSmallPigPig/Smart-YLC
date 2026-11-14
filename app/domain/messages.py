"""站内信（系统消息）：拉取、分类、定位支部账号。

端点（前端 MessageApi.js）：GET /api/v1/admin/message/findAllMessage
type=0 组织账号；信封 {code, total, listData}，total 是本页条数（不可靠），
按「返回空页」停止翻页。content 是 HTML，团员申请类消息带
data-mobile（团员手机号）——用它把消息定位到团员所在支部账号。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KIND_LABEL = {"report": "报到申请", "transfer": "转接申请", "other": "其他消息"}
KIND_COLOR = {"report": "#1565c0", "transfer": "#2e7d32", "other": "#6b7280"}
# 站点把「报到」错打成「报道」，两种写法都要认
_REPORT_WORDS = ("报到", "报道")
_MOBILE_RE = re.compile(r"1\d{10}")
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class MessageView:
    receive_id: int
    account: str  # 拉取这条消息所用的账号（userName）
    title: str
    content: str  # 纯文本内容（去 HTML）
    mobile: str  # 消息携带的团员手机号（没有则空）
    kind: str  # report / transfer / other
    status: int  # 0 未读 1 已读 2 已删
    status_str: str
    time_str: str  # 站点格式化的接收时间

    @property
    def kind_label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)


def classify_message(title: str, content: str) -> str:
    """报到申请 / 转接申请 / 其他。转接优先（转接标题可能不带「转接」二字）。"""
    text = f"{title} {content}"
    if "转接" in text:
        return "transfer"
    if any(w in text for w in _REPORT_WORDS):
        return "report"
    return "other"


def extract_mobile(content: str) -> str:
    """从消息 HTML 里提取团员手机号（data-mobile 优先，其次首个手机号样式串）。"""
    m = re.search(r"data-mobile='(\d+)'", content) or re.search(r'data-mobile="(\d+)"', content)
    if m:
        return m.group(1)
    m = _MOBILE_RE.search(_TAG_RE.sub("", content))
    return m.group(0) if m else ""


def strip_html(content: str) -> str:
    return _TAG_RE.sub("", content).replace("&nbsp;", " ").strip()


def build_message_views(rows: list[dict], account: str) -> list[MessageView]:
    views: list[MessageView] = []
    for r in rows:
        title = str(r.get("title") or "")
        content = str(r.get("content") or "")
        views.append(MessageView(
            receive_id=int(r.get("receiveId") or 0),
            account=account,
            title=title,
            content=strip_html(content),
            mobile=extract_mobile(content),
            kind=classify_message(title, content),
            status=int(r.get("status") or 0),
            status_str=str(r.get("statusStr") or ""),
            time_str=str(r.get("sendTime") or ""),
        ))
    return views


def fetch_all_messages(client, account: str, page_size: int = 100, max_pages: int = 10) -> list[MessageView]:
    """拉取一个账号的全部站内信（返回空页即停）。"""
    views: list[MessageView] = []
    for page in range(1, max_pages + 1):
        data = client.get_messages_page(page, page_size)
        rows = data.get("listData") or []
        if not rows:
            break
        views.extend(build_message_views(rows, account))
        if len(rows) < page_size:
            break
    return views


def locate_account(view: MessageView, accounts: list[dict], member_index: dict[str, tuple[str, str]]) -> str:
    """消息 → 负责该团员所在支部的账号 userName。

    member_index：手机号 → (账号 userName|"", 班级编号)。有 userName 直接用；
    只有班级编号时由调用方经编号规则/账号备注匹配。找不到返回空串。"""
    user, cls = member_index.get(view.mobile, ("", "")) if view.mobile else ("", "")
    if user:
        return user
    if cls:
        from app.domain.org_rule import describe_code

        branch = describe_code(cls)
        for a in accounts:
            if (a.get("remark") or "") == branch:
                return a.get("userName", "")
    return ""


def build_member_index(accounts: list[dict], member_cache: dict, local_rows: list) -> dict[str, tuple[str, str]]:
    """手机号 → (账号 userName|"", 班级编号)。系统缓存优先（真实在册），本地名单兜底。"""
    index: dict[str, tuple[str, str]] = {}
    for user, blob in (member_cache or {}).items():
        for m in blob.get("members", []):
            mob = str(m.get("mobile") or "")
            if mob:
                index.setdefault(mob, (user, ""))
    for r in local_rows:
        mob = getattr(r, "mobile", "")
        if mob and mob not in index:
            index[mob] = ("", getattr(r, "extra", {}).get("class", ""))
    return index
