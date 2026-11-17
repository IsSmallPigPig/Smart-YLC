"""通知节奏：每天固定时刻、按小时间隔、或关闭。

托盘调度与设置页共用这份纯函数，配置键：
  notifyMode      daily | interval | off，缺省按旧配置视为 daily
  notifyTime      daily 模式的时刻（HH:MM）
  notifyInterval  interval 模式的小时数
  lastDailyAt     上一次实际检查的时间（ISO），用于间隔计时
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.services import store

MODES = ("daily", "interval", "off")
MODE_LABELS = {"daily": "每天一次", "interval": "按间隔", "off": "关闭"}
DEFAULT_MODE = "daily"
DEFAULT_INTERVAL_HOURS = 6
INTERVAL_CHOICES = (2, 4, 6, 8, 12)
LAST_AT_KEY = "lastDailyAt"


def mode(cfg: dict) -> str:
    value = str(cfg.get("notifyMode") or DEFAULT_MODE)
    return value if value in MODES else DEFAULT_MODE


def interval_hours(cfg: dict) -> int:
    try:
        hours = int(cfg.get("notifyInterval") or DEFAULT_INTERVAL_HOURS)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_HOURS
    return min(max(hours, 1), 24)


def daily_hhmm(cfg: dict) -> tuple[int, int]:
    hh, mm = store.notify_time(cfg).split(":")
    return int(hh), int(mm)


def parse_last_at(cfg: dict) -> datetime | None:
    raw = str(cfg.get(LAST_AT_KEY) or "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def should_fire(now: datetime, cfg: dict, last_at: datetime | None = None) -> bool:
    """现在是否该发通知。daily 每天一次，interval 按距上次的小时数。"""
    kind = mode(cfg)
    if kind == "off":
        return False
    base = last_at or parse_last_at(cfg)
    if kind == "daily":
        hh, mm = daily_hhmm(cfg)
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < target:
            return False
        return base is None or base.date() < now.date()
    if base is None:
        return True
    return now >= base + timedelta(hours=interval_hours(cfg))


def describe(cfg: dict) -> str:
    """一句话说明当前节奏，给设置页小字用。"""
    kind = mode(cfg)
    if kind == "off":
        return "关闭后台通知"
    if kind == "daily":
        hh, mm = daily_hhmm(cfg)
        return f"每天 {hh:02d}:{mm:02d} 推送一次未读消息汇总"
    return f"每 {interval_hours(cfg)} 小时推送一次未读消息汇总"
