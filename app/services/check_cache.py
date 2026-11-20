"""检查结果缓存：按文件内容摘要记住「这份档案已经查过什么」。

为什么按内容而不是路径：
  上传前要跑两件慢事——读页数（每份 1~6ms）和逐格查空格（每份 300~800ms）。
  原件会被规范化改名、压缩副本会随参数重做、程序重开会清空内存，
  用 (路径, 修改时间) 当键，上面任一情况都会让几百份档案白跑一遍。
  同一份内容无论叫什麼名字、放在哪个目录、压过几次，检查结论都一样。

摘要为什么不是整份 sha256：
  实测一页扫描件压缩后 1~2MB，整份 sha256 读满 10MB 要 10~25ms；
  这里只需要「能区分不同文件」，取 大小 + 首/中/尾各 256KB 采样即可，
  单份约 1~2ms，快一个数量级。极端情况下两份文件在三个采样区完全相同
  而中间不同，会被当成同一份——采样区共 768KB，扫描件实际做不到。

缓存落在配置目录旁的 check_cache.json，与 config.json 分开：
  它是可重建的派生数据，坏了删掉即可，不该连带影响账号、名单等真实配置。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time

from app.constants import CONFIG_FILE

CACHE_FILE = os.path.join(os.path.dirname(CONFIG_FILE), "check_cache.json")
# 单个采样区大小。768KB 总采样对应 1~2ms/份
SAMPLE = 256 * 1024
# 条目上限。一份档案一条，几千个来源目录足够用；超了按最近使用淘汰
MAX_ENTRIES = 4000
VERSION = 1

_lock = threading.Lock()
# 会话内的一级缓存：(真实路径, 大小, mtime) → 摘要。
# 同一轮扫描里页数与空格检查都要用摘要，避免重复读盘。
_memo: dict[tuple[str, int, int], str] = {}


def _sample(path: str, size: int) -> bytes:
    """读首、中、尾三个采样区。小文件整份读。"""
    if size <= SAMPLE * 3:
        with open(path, "rb") as f:
            return f.read()
    with open(path, "rb") as f:
        head = f.read(SAMPLE)
        middle_at = max(0, size // 2 - SAMPLE // 2)
        f.seek(middle_at)
        middle = f.read(SAMPLE)
        f.seek(max(0, size - SAMPLE))
        tail = f.read(SAMPLE)
    return head + middle + tail


def fingerprint(path: str) -> str:
    """内容摘要。读不出文件时返回空串，调用方按「没缓存」处理。"""
    try:
        stat = os.stat(path)
    except OSError:
        return ""
    key = (os.path.realpath(path), stat.st_size, stat.st_mtime_ns)
    with _lock:
        hit = _memo.get(key)
    if hit is not None:
        return hit
    try:
        digest = hashlib.sha256()
        digest.update(str(stat.st_size).encode())
        digest.update(_sample(path, stat.st_size))
        value = digest.hexdigest()
    except OSError:
        return ""
    with _lock:
        _memo[key] = value
    return value


# ---------- 持久化 ----------

def _load() -> dict:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def _save(files: dict) -> None:
    if len(files) > MAX_ENTRIES:
        # 按最近写入时间淘汰：老档案删了就删了，重查一次而已。
        # 用纳秒：一轮扫描里几百份在同一秒内写完，秒级时间戳排序不稳定，
        # 会把刚写进去的条目也淘汰掉。
        keep = sorted(files.items(), key=lambda kv: kv[1].get("at", 0), reverse=True)
        files = dict(keep[:MAX_ENTRIES])
    directory = os.path.dirname(CACHE_FILE)
    tmp = ""
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".check-cache-", suffix=".tmp", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": VERSION, "files": files}, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
        tmp = ""
    except OSError:
        # 缓存写不进去不影响检查本身，下次重查一遍即可
        pass
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def _update(digest: str, **fields) -> None:
    if not digest:
        return
    with _lock:
        files = _load()
        entry = files.get(digest) or {}
        entry.update(fields)
        entry["at"] = time.time_ns()
        files[digest] = entry
        _save(files)


def _read(digest: str, name: str) -> int | None:
    if not digest:
        return None
    with _lock:
        entry = _load().get(digest) or {}
    value = entry.get(name)
    return value if isinstance(value, int) and value >= 0 else None


# ---------- 两个用途 ----------

def remember_pages(path_or_digest: str, pages: int | None) -> None:
    """记住页数。读不出页数（None）不缓存——那是失败，不是结论。"""
    if pages is not None:
        _update(path_or_digest, pages=int(pages))


def pages_of(digest: str) -> int | None:
    return _read(digest, "pages")


def remember_blanks(digest: str, count: int) -> None:
    """记住逐格检查的空格数。跳过（-1）不缓存，否则会把环境问题钉成结论。"""
    if count >= 0:
        _update(digest, blanks=int(count))


def blanks_of(digest: str) -> int | None:
    return _read(digest, "blanks")


def clear() -> None:
    """清空缓存（测试与设置页「重新检查全部」用）。"""
    with _lock:
        _memo.clear()
        try:
            os.remove(CACHE_FILE)
        except OSError:
            pass
