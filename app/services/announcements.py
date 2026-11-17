"""启动公告：从 GitHub 上的 JSON 文件拉取，按 id 记录已读。

公告源是仓库里的纯文本 JSON（默认 docs/announcements.json），不经 GitHub API：
API 匿名请求每小时只有 60 次，用户一多就会被限流；raw 文件可以被现成的
GitHub 加速站直接代理，发布也只需在网页上改一个文件。

拉取顺序固定为「代理站优先、直连兜底」。本机直连 raw.githubusercontent.com
常常不通，放最后只是给代理全挂时留一条路。超时短、失败只记日志，不打扰用户。
本模块不依赖 Qt，便于独立测解析与回退。
"""

from __future__ import annotations

import json
import urllib.error
from dataclasses import dataclass

from app.services import store, updater

# 公告源：与程序同仓库，随 main 分支更新。想拆成独立仓库时只改这一行。
SOURCE_URL = ("https://raw.githubusercontent.com/IsSmallPigPig/Smart-YLC/"
              "main/docs/announcements.json")
STATE_KEY = "announcements"
TIMEOUT = 3
MAX_PROXIES = 6  # 启动阶段最多试几个代理，避免网络全不通时拖太久


class AnnouncementError(Exception):
    """公告源不可用或格式不合法，message 里带可定位的原因。"""


@dataclass(frozen=True)
class Announcement:
    id: str
    title: str
    items: tuple[str, ...]
    date: str = ""


def parse_announcements(content: bytes | str) -> list[Announcement]:
    """解析公告文件。格式不对就明确报错，绝不静默丢条目。

    约定（version 1）：::

        {"version": 1, "announcements": [
          {"id": "2026-11-17-v1", "title": "版本更新",
           "date": "2026-11-17", "items": ["第一条", "第二条"]}
        ]}

    announcements 按新→旧排列；id 是已读记录的唯一键，发布后不要改。
    """
    text = content.decode("utf-8", "replace") if isinstance(content, bytes) else content
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as e:
        raise AnnouncementError(f"公告文件不是合法 JSON：{e}") from e
    if not isinstance(data, dict):
        raise AnnouncementError("公告文件根节点必须是对象")
    version = data.get("version", 1)
    if version != 1:
        raise AnnouncementError(f"不支持的公告格式版本：{version}")
    rows = data.get("announcements")
    if not isinstance(rows, list):
        raise AnnouncementError("公告文件缺少 announcements 数组")

    out: list[Announcement] = []
    for i, row in enumerate(rows, 1):
        where = f"第 {i} 条公告"
        if not isinstance(row, dict):
            raise AnnouncementError(f"{where}不是对象")
        aid = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        raw_items = row.get("items")
        if not aid:
            raise AnnouncementError(f"{where}缺少 id")
        if not title:
            raise AnnouncementError(f"{where}缺少 title")
        if not isinstance(raw_items, list) or not raw_items:
            raise AnnouncementError(f"{where}缺少非空的 items 数组")
        items = []
        for x in raw_items:
            if not isinstance(x, str) or not x.strip():
                raise AnnouncementError(f"{where}的 items 必须是非空字符串")
            items.append(x.strip())
        if not items:
            raise AnnouncementError(f"{where}的 items 全为空")
        out.append(Announcement(aid, title, tuple(items), str(row.get("date") or "").strip()))
    return out


def _chain(gateways=None) -> list[dict]:
    """代理站优先、直连兜底。传 gateways 时完全按调用方的顺序。"""
    if gateways is not None:
        return list(gateways)
    ranked = updater.ranked_gateways()   # 没测过速时就是 GATEWAYS 的清单顺序
    proxies = [g for g in ranked if g.get("prefix")][:MAX_PROXIES]
    return proxies + [g for g in ranked if not g.get("prefix")]


def fetch_announcements(url: str = SOURCE_URL, gateways=None,
                        timeout: int = TIMEOUT) -> list[Announcement]:
    """拉取并解析公告。全部通道失败时抛 AnnouncementError，不返回空清单。

    空清单是「当前没有公告」，失败是「不知道有没有公告」——两者不能混，
    否则没法在日志里区分「没发公告」和「代理全挂了」。
    """
    from app.services import app_log

    errors: list[str] = []
    for gw in _chain(gateways):
        label = str(gw.get("label") or gw.get("key") or "未知通道")
        target = updater.proxy_url(url, gw)
        try:
            raw = updater._read(target, timeout)
        except (updater.UpdateError, urllib.error.URLError, OSError, ValueError) as e:
            errors.append(f"{label}：{e}")
            continue
        try:
            items = parse_announcements(raw)
        except AnnouncementError as e:
            errors.append(f"{label}：{e}")
            continue
        app_log.info("公告拉取成功：%s，共 %d 条", label, len(items))
        return items
    detail = "；".join(errors[-3:]) or "没有可用的拉取通道"
    raise AnnouncementError(detail)


# ---------- 已读状态 ----------

def seen_ids() -> set[str]:
    state = store.load().get(STATE_KEY) or {}
    raw = state.get("seenIds") or []
    return {str(x) for x in raw}


def pending(items: list[Announcement]) -> list[Announcement]:
    """按公告文件的顺序返回尚未读过的条目。"""
    seen = seen_ids()
    return [a for a in items if a.id not in seen]


def mark_seen(ids) -> None:
    """确认一条公告后记下 id；保留其它状态字段，不整块覆盖。"""
    values = [str(i) for i in ids if str(i)]
    if not values:
        return

    def _write(cfg):
        state = dict(cfg.get(STATE_KEY) or {})
        merged = list(state.get("seenIds") or [])
        for value in values:
            if value not in merged:
                merged.append(value)
        state["seenIds"] = merged
        cfg[STATE_KEY] = state

    store.update(_write)
