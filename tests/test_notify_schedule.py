"""通知节奏：每天固定时刻、按小时间隔、关闭。"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.services import notify_schedule


def cfg(**kw):
    base = {"notifyMode": "daily", "notifyTime": "08:00", "notifyInterval": 6}
    base.update(kw)
    return base


def test_daily_fires_after_target_when_not_run_today():
    now = datetime(2026, 11, 14, 9, 0)
    assert notify_schedule.should_fire(now, cfg()) is True


def test_daily_waits_before_target():
    now = datetime(2026, 11, 14, 7, 59)
    assert notify_schedule.should_fire(now, cfg()) is False


def test_daily_does_not_repeat_after_run_today():
    now = datetime(2026, 11, 14, 9, 0)
    last = datetime(2026, 11, 14, 8, 1)
    assert notify_schedule.should_fire(now, cfg(), last) is False
    assert notify_schedule.should_fire(
        datetime(2026, 11, 15, 8, 1), cfg(), last) is True


def test_interval_uses_last_run_hours():
    now = datetime(2026, 11, 14, 12, 0)
    assert notify_schedule.should_fire(now, cfg(notifyMode="interval"), now - timedelta(hours=3)) is False
    assert notify_schedule.should_fire(now, cfg(notifyMode="interval"), now - timedelta(hours=7)) is True


def test_interval_without_history_fires_immediately():
    now = datetime(2026, 11, 14, 12, 0)
    assert notify_schedule.should_fire(now, cfg(notifyMode="interval")) is True


def test_off_never_fires():
    assert notify_schedule.should_fire(datetime(2026, 11, 14, 12, 0), cfg(notifyMode="off")) is False


def test_interval_hours_clamped_and_fallback():
    assert notify_schedule.interval_hours(cfg(notifyInterval=0)) == 6  # 0 视为未配置
    assert notify_schedule.interval_hours(cfg(notifyInterval=999)) == 24
    assert notify_schedule.interval_hours(cfg(notifyInterval="abc")) == 6
    assert notify_schedule.interval_hours({}) == 6


def test_unknown_mode_falls_back_to_daily():
    assert notify_schedule.mode({"notifyMode": "weird"}) == "daily"
    assert notify_schedule.mode({}) == "daily"


def test_bad_notify_time_falls_back_to_eight():
    assert notify_schedule.daily_hhmm({"notifyTime": "oops"}) == (8, 0)


def test_describe_matches_mode():
    assert notify_schedule.describe(cfg()) == "每天 08:00 推送一次未读消息汇总"
    assert "每 4 小时" in notify_schedule.describe(cfg(notifyMode="interval", notifyInterval=4))
    assert notify_schedule.describe(cfg(notifyMode="off")) == "关闭后台通知"
