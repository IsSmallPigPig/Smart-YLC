"""预检查压缩阶段：原件只读，副本落工作目录；异常状态由副本位置决定。"""

from __future__ import annotations

import os

from app.domain import work_area
from app.domain.upload_inspect import FileStatus


def _pdf(folder, name="张三+13800000001+入团志愿书.pdf", size=1024):
    os.makedirs(str(folder), exist_ok=True)
    path = os.path.join(str(folder), name)
    with open(path, "wb") as f:
        f.write(b"%PDF-1.4" + b"0" * size)
    return path


def _status(path, root):
    return FileStatus(path=path, name=os.path.basename(path), root=root)


def test_prepare_one_copies_when_already_small(tmp_path):
    """达标原件：直接复制副本到压缩区，原件不动。"""
    from app.services import prepare

    original = _pdf(tmp_path / "高一1班")
    status = _status(original, str(tmp_path))
    copy = prepare.prepare_one(status, str(tmp_path), target=1 << 20)

    assert copy == work_area.copy_target(str(tmp_path), original)
    assert os.path.isfile(copy)
    assert os.path.isfile(original)
    assert status.work_path == copy
    with open(original, "rb") as f:
        assert f.read().startswith(b"%PDF-1.4")


def test_prepare_one_reuses_fresh_copy(tmp_path, monkeypatch):
    """副本已存在且不比原件旧：不再重复压缩。"""
    from app.services import prepare

    original = _pdf(tmp_path / "高一1班")
    status = _status(original, str(tmp_path))
    copy = prepare.prepare_one(status, str(tmp_path), target=1 << 20)
    with open(copy, "wb") as f:
        f.write(b"MARKER")

    def boom(*a, **k):
        raise AssertionError("不该再次压缩")

    monkeypatch.setattr("app.services.compress.compress_pdf", boom)
    again = prepare.prepare_one(status, str(tmp_path), target=1 << 20)
    assert again == copy
    with open(copy, "rb") as f:
        assert f.read() == b"MARKER"


def test_prepare_one_redoes_copy_when_original_changes(tmp_path, monkeypatch):
    """原件改过（mtime 更新）：副本重建。"""
    import time
    from app.services import prepare

    original = _pdf(tmp_path / "高一1班")
    status = _status(original, str(tmp_path))
    copy = prepare.prepare_one(status, str(tmp_path), target=1 << 20)

    time.sleep(0.02)
    with open(original, "wb") as f:
        f.write(b"%PDF-1.4" + b"X" * 2048)
    os.utime(original, (time.time() + 5, time.time() + 5))
    assert prepare.prepare_one(status, str(tmp_path), target=1 << 20) == copy


def test_prepare_one_oversized_goes_to_abnormal(tmp_path, monkeypatch):
    """压不下来的：副本落异常区，不进压缩区。"""
    from app.services import prepare

    def over(src, dst, target):
        with open(dst, "wb") as f:
            f.write(b"BIG")
        return {"src_size": 999, "dst_size": 999, "over": True}

    monkeypatch.setattr("app.services.compress.compress_pdf", over)
    original = _pdf(tmp_path / "高一1班", size=99999)
    status = _status(original, str(tmp_path))

    copy = prepare.prepare_one(status, str(tmp_path), target=10)
    assert work_area.is_abnormal_copy(copy)
    assert not os.path.exists(work_area.copy_target(str(tmp_path), original))


def test_abnormal_originals_maps_back_to_sources(tmp_path):
    """异常区的副本倒推回原件绝对路径，用于刷新时重建异常标记。"""
    from app.services import prepare

    root = str(tmp_path)
    a = _pdf(tmp_path / "高一1班", "张三+13800000001+入团志愿书.pdf")
    b = _pdf(tmp_path / "高一2班", "李四+13800000002+入团志愿书.pdf")
    ca = work_area.copy_target(root, a, abnormal=True)
    os.makedirs(os.path.dirname(ca), exist_ok=True)
    with open(ca, "wb") as f:
        f.write(b"x")

    found = prepare.abnormal_originals({a: root, b: root})
    assert os.path.normpath(a) in found
    assert os.path.normpath(b) not in found
