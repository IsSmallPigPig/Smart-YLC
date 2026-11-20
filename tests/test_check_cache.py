"""检查结果缓存：按内容摘要记住页数与空格数，换名/重压/重启都不重跑。

摘要用「大小 + 首中尾采样」，这里同时验证它对不同文件确实给出不同值——
采样碰撞会把两份不同档案当成同一份，属于必须守住的边界。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    from app.services import check_cache

    monkeypatch.setattr(check_cache, "CACHE_FILE", str(tmp_path / "check_cache.json"))
    check_cache.clear()
    yield check_cache
    check_cache.clear()


def _write(path, data: bytes):
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


def test_fingerprint_ignores_name_and_location(cache, tmp_path):
    """同一份内容换个文件名、换个目录，摘要必须相同。"""
    body = b"%PDF-1.4" + b"x" * 5000
    a = _write(tmp_path / "张三.pdf", body)
    b = _write(tmp_path / "副本.pdf", body)
    assert cache.fingerprint(a) == cache.fingerprint(b)

    moved = tmp_path / "子目录"
    moved.mkdir()
    c = _write(moved / "另一个名字.pdf", body)
    assert cache.fingerprint(a) == cache.fingerprint(c)


def test_fingerprint_separates_different_content(cache, tmp_path):
    """内容不同（哪怕只差一个字节）必须给出不同摘要。"""
    a = _write(tmp_path / "a.pdf", b"%PDF-1.4" + b"x" * 5000)
    b = _write(tmp_path / "b.pdf", b"%PDF-1.4" + b"x" * 4999 + b"y")
    assert cache.fingerprint(a) != cache.fingerprint(b)


def test_fingerprint_detects_change_in_middle_of_large_file(cache, tmp_path):
    """大文件改中间一个字节也要能区分：采样区必须覆盖中段。"""
    size = 3 * 1024 * 1024
    body = bytearray(b"a" * size)
    a = _write(tmp_path / "big.pdf", bytes(body))
    body[size // 2] = ord("b")
    b = _write(tmp_path / "big2.pdf", bytes(body))
    assert cache.fingerprint(a) != cache.fingerprint(b)


def test_pages_survive_restart(cache, tmp_path):
    """页数写盘后，新进程（清空内存）仍能读到。"""
    pdf = _write(tmp_path / "一份.pdf", b"%PDF-1.4 pages")
    digest = cache.fingerprint(pdf)
    assert cache.pages_of(digest) is None

    cache.remember_pages(digest, 13)
    cache._memo.clear()               # 模拟重启：内存一级缓存没了
    assert cache.pages_of(digest) == 13


def test_pages_failure_not_cached(cache, tmp_path):
    """读不出页数是失败不是结论，不能钉成「0 页」。"""
    pdf = _write(tmp_path / "坏.pdf", b"not a pdf")
    digest = cache.fingerprint(pdf)
    cache.remember_pages(digest, None)
    assert cache.pages_of(digest) is None


def test_blanks_survive_restart(cache, tmp_path):
    pdf = _write(tmp_path / "空格.pdf", b"%PDF-1.4 blanks")
    digest = cache.fingerprint(pdf)
    cache.remember_blanks(digest, 3)
    cache._memo.clear()
    assert cache.blanks_of(digest) == 3


def test_blanks_skip_not_cached(cache, tmp_path):
    """跳过（-1）是环境问题，不能缓存成「没有空格」。"""
    pdf = _write(tmp_path / "跳过.pdf", b"%PDF-1.4 skip")
    digest = cache.fingerprint(pdf)
    cache.remember_blanks(digest, -1)
    assert cache.blanks_of(digest) is None


def test_pages_and_blanks_coexist(cache, tmp_path):
    """同一份档案的两种结论互不覆盖。"""
    pdf = _write(tmp_path / "同一份.pdf", b"%PDF-1.4 both")
    digest = cache.fingerprint(pdf)
    cache.remember_pages(digest, 13)
    cache.remember_blanks(digest, 0)
    assert cache.pages_of(digest) == 13
    assert cache.blanks_of(digest) == 0


def test_corrupt_cache_file_is_ignored(cache, tmp_path):
    """缓存文件坏了不能让上传页崩，只当没有缓存。"""
    pdf = _write(tmp_path / "x.pdf", b"%PDF-1.4")
    digest = cache.fingerprint(pdf)
    with open(cache.CACHE_FILE, "w", encoding="utf-8") as f:
        f.write("{ 这不是 json")
    assert cache.pages_of(digest) is None
    cache.remember_pages(digest, 5)
    assert cache.pages_of(digest) == 5


def test_entry_cap_keeps_recent(cache, tmp_path, monkeypatch):
    """条目超上限时按最近写入保留，不会无限膨胀。"""
    monkeypatch.setattr(cache, "MAX_ENTRIES", 3)
    digests = []
    for i in range(6):
        pdf = _write(tmp_path / f"{i}.pdf", b"%PDF-1.4" + str(i).encode())
        digest = cache.fingerprint(pdf)
        digests.append(digest)
        cache.remember_pages(digest, i + 1)
    stored = cache._load()
    assert len(stored) <= 3
    assert cache.pages_of(digests[-1]) == 6      # 最近写入的必须还在
    assert cache.pages_of(digests[0]) is None    # 最早的被淘汰


def test_missing_file_returns_empty_digest(cache):
    assert cache.fingerprint("/nonexistent/nope.pdf") == ""
