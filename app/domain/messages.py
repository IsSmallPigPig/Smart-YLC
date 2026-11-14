"""站内信（系统消息）：拉取、分类、定位支部账号。

端点（前端 MessageApi.js）：GET /api/v1/admin/message/findAllMessage
type=0 组织账号；信封 {code, total, listData}，total 是本页条数（不可靠），
按「返回空页」停止翻页。content 是 HTML，团员申请类消息带
data-mobile（团员手机号）——仅用于卡片上显示团员姓名；点击消息打开的
网页端账号就是拉取该消息的支部账号（view.account），无需再定位团员。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KIND_LABEL = {"report": "报到申请", "transfer": "转接申请", "other": "其他消息"}
KIND_COLOR = {"report": "#1565c0", "transfer": "#2e7d32", "other": "#6b7280"}
# 站点把「报到」错打成「报道」，两种写法都要认；关系转接含 转出/转入 两种审核提醒
_REPORT_WORDS = ("报到", "报道")
_TRANSFER_WORDS = ("转接", "转出", "转入")
_MOBILE_RE = re.compile(r"1\d{10}")
_TAG_RE = re.compile(r"<[^>]+>")
# 站点标题形如「[系统通知][报道认证审核提醒]」：第一层是系统通知前缀，第二层才是标题
_PREFIX_WORDS = {"系统通知"}
_LEAD_PREFIX_RE = re.compile(r"^\s*[【\[]([^】\]]*)[】\]]\s*")
_WRAP_RE = re.compile(r"^\s*[【\[]([^】\]]*)[】\]]\s*$")


def clean_title(title: str) -> str:
    """剥掉「[系统通知]」类前缀；若剩余部分整体包在一层中括号里则解开。"""
    t = (title or "").strip()
    m = _LEAD_PREFIX_RE.match(t)
    if m and m.group(1).strip() in _PREFIX_WORDS:
        t = t[m.end():]
    m2 = _WRAP_RE.match(t)
    if m2:
        t = m2.group(1)
    return t.strip()


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
    """报到申请 / 转接申请 / 其他。转接类优先（转出/转入审核提醒都不带「转接」二字）。"""
    text = f"{title} {content}"
    if any(w in text for w in _TRANSFER_WORDS):
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
    """拉取一个账号的全部站内信。

    翻页终止条件：空页、不足一页，或「整页都是已见过的 receiveId」——
    服务端偶发无视 page 参数重复返回同一页，按 receive_id 去重防止死循环刷出
    上千条重复数据把界面卡死。"""
    views: list[MessageView] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        data = client.get_messages_page(page, page_size)
        rows = data.get("listData") or []
        if not rows:
            break
        fresh = [r for r in rows if str(r.get("receiveId")) not in seen]
        if not fresh:
            break
        for r in fresh:
            seen.add(str(r.get("receiveId")))
        views.extend(build_message_views(fresh, account))
        if len(rows) < page_size:
            break
    return views


def build_member_name_index(member_cache: dict, local_rows: list) -> dict[str, str]:
    """手机号 → 团员姓名（系统缓存优先，本地名单兜底）。用于消息卡片主标题。"""
    index: dict[str, str] = {}
    for user, blob in (member_cache or {}).items():
        for m in blob.get("members", []):
            mob = str(m.get("mobile") or "")
            name = str(m.get("name") or "")
            if mob and name:
                index.setdefault(mob, name)
    for r in local_rows:
        mob = getattr(r, "mobile", "")
        if mob and mob not in index:
            index[mob] = getattr(r, "name", "")
    return index
