"""配置读写（config.json 的唯一格式定义）。"""

from __future__ import annotations

import json
import os
import re

from app.constants import CONFIG_FILE

DEFAULT_NOTIFY_TIME = "08:00"


def load() -> dict:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return {}
    # 兼容旧版单账号格式（自动迁移，不丢数据）
    if not cfg.get("accounts") and cfg.get("userName") and cfg.get("password"):
        cfg["accounts"] = [{
            "userName": cfg["userName"], "password": cfg["password"], "remark": "",
            "health": {"status": "unknown", "message": "", "checkedAt": ""},
        }]
        cfg.pop("userName", None)
        cfg.pop("password", None)
    return cfg


def save(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def notify_time(cfg: dict) -> str:
    t = str(cfg.get("notifyTime") or DEFAULT_NOTIFY_TIME)
    return t if re.match(r"^\d{1,2}:\d{2}$", t) else DEFAULT_NOTIFY_TIME
