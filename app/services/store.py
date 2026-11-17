"""配置读写（config.json 的唯一格式定义）。"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
from typing import Callable

from app.constants import CONFIG_FILE

DEFAULT_NOTIFY_TIME = "08:00"

_LOCK = threading.Lock()  # 配置文件唯一的写入口：load→mutate→save 必须整段持锁


def _backup_path() -> str:
    return str(CONFIG_FILE) + ".bak"


def _replace_atomic(src: str, dst: str, attempts: int = 5) -> None:
    """os.replace 的 Windows 友好版。

    覆盖一个正被别的进程读的文件时，POSIX 直接换掉，Windows 会报
    PermissionError（杀毒、搜索索引、同时开着的托盘进程都在读它）。
    短暂重试几次就过去了，超过次数照旧抛出，不吞错误。"""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.04 * (i + 1))


def load() -> dict:
    if not os.path.exists(CONFIG_FILE):
        # 主文件不在了但备份还在：说明是外部删除/中断留下的，直接用备份恢复，
        # 而不是返回 {} —— 那会让下一次 update 只写进本次的键，整份配置就没了
        backup = _backup_path()
        if os.path.exists(backup):
            try:
                with open(backup, encoding="utf-8") as f:
                    cfg = json.load(f)
            except (OSError, ValueError):
                return {}
            if cfg:
                save(cfg)      # 顺手把主文件补回来
                return cfg
        return {}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        # 另一个进程正在写、文件被截断或 JSON 损坏时，绝不能把 {} 交给
        # update 写回（那会把整份配置抹成只剩本次写入的键）。先从上一版
        # 备份恢复；备份也坏了就显式报错，让调用方停下而不是静默丢数据。
        backup = _backup_path()
        if os.path.exists(backup):
            try:
                with open(backup, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
        raise RuntimeError(f"配置读取失败，已停止写入以免覆盖原数据：{e}") from e
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
    d = os.path.dirname(CONFIG_FILE)
    os.makedirs(d, exist_ok=True)
    backup = _backup_path()
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=d)
    fd_b, tmp_b = tempfile.mkstemp(prefix=".config-", suffix=".bak.tmp", dir=d)
    os.close(fd_b)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        shutil.copyfile(tmp, tmp_b)
        # 备份与主文件都是完整 JSON、各自原子落地，且各有独立 inode：
        # 硬链接会和主文件共享 inode，外部进程就地截断主文件时备份会一起坏掉。
        # 备份先落地，主文件一旦可见就已经有可读的恢复副本（首次写入也算）。
        _replace_atomic(tmp_b, backup)
        _replace_atomic(tmp, CONFIG_FILE)
    except BaseException:
        for p in (tmp, tmp_b):
            if os.path.exists(p):
                os.unlink(p)
        raise


def update(mutate: Callable[[dict], None]) -> dict:
    """线程安全的 load → mutate → save。

    工作线程（成员缓存/站内信缓存逐账号落盘）与 GUI 线程（账号/健康状态落盘）
    都走这里：先拿最新配置再改，后写者不会用旧快照把先写者的数据抹掉。
    配置读不出来时直接抛错，不用空配置覆盖磁盘上的原数据。"""
    with _LOCK:
        cfg = load()
        mutate(cfg)
        save(cfg)
        return cfg


def notify_time(cfg: dict) -> str:
    t = str(cfg.get("notifyTime") or DEFAULT_NOTIFY_TIME)
    return t if re.match(r"^\d{1,2}:\d{2}$", t) else DEFAULT_NOTIFY_TIME
