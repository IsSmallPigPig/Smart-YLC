"""手机同步落盘测试：幂等、不覆盖、自动入列、不合规命名提示。"""

from __future__ import annotations

import pathlib
from datetime import date

from app.services import phone_sync as ps


DAY = date(2026, 11, 19)


def _pdf(tmp_path: pathlib.Path, name: str, payload: bytes = b"pdf-bytes") -> str:
    p = tmp_path / name
    p.write_bytes(payload)
    return str(p)


def test_receive_pdf_lands_in_date_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.os.path, "expanduser", lambda p: str(tmp_path))
    src = _pdf(tmp_path, "张三+13800000000+入团志愿书.pdf")
    dest, status = ps.receive_pdf(src, today=DAY)
    assert status == "ok"
    assert pathlib.Path(dest).parent == tmp_path / ps.SYNC_FOLDER / "2026-11-19"
    assert pathlib.Path(dest).read_bytes() == b"pdf-bytes"


def test_duplicate_is_detected_not_copied(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.os.path, "expanduser", lambda p: str(tmp_path))
    src = _pdf(tmp_path, "张三+13800000000+入团志愿书.pdf")
    first, _ = ps.receive_pdf(src, today=DAY)
    again, status = ps.receive_pdf(src, today=DAY)
    assert status == "duplicate"
    assert again == first
    assert len(list((tmp_path / ps.SYNC_FOLDER / DAY.isoformat()).glob("*.pdf"))) == 1


def test_same_name_different_content_gets_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.os.path, "expanduser", lambda p: str(tmp_path))
    a = _pdf(tmp_path, "张三+13800000000+入团志愿书.pdf", b"version-a")
    b = tmp_path / "incoming.pdf"
    b.write_bytes(b"version-b")
    first, _ = ps.receive_pdf(a, today=DAY)
    second, status = ps.receive_pdf(str(b), name="张三+13800000000+入团志愿书.pdf", today=DAY)
    assert status == "ok"
    assert second != first and "(2)" in pathlib.Path(second).name
    assert pathlib.Path(first).read_bytes() == b"version-a"
    assert pathlib.Path(second).read_bytes() == b"version-b"


def test_bad_name_is_saved_but_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.os.path, "expanduser", lambda p: str(tmp_path))
    src = _pdf(tmp_path, "随便的名字.pdf")
    dest, status = ps.receive_pdf(src, today=DAY)
    assert status == "need_rename"
    assert pathlib.Path(dest).is_file()


def test_non_pdf_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.os.path, "expanduser", lambda p: str(tmp_path))
    src = tmp_path / "note.txt"
    src.write_text("hi")
    dest, status = ps.receive_pdf(str(src), today=DAY)
    assert dest is None and status == "不是 PDF 文件"


def test_batch_registers_source_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.os.path, "expanduser", lambda p: str(tmp_path))
    added = []
    src = _pdf(tmp_path, "李四+13900000000+入团志愿书.pdf")
    result = ps.receive_batch([(src, "李四+13900000000+入团志愿书.pdf")],
                              today=DAY)
    # receive_pdf 走了默认 register_source_dir；这里只验证结果与目录
    assert len(result.saved) == 1 and not result.rejected
    assert result.directory == str(tmp_path / ps.SYNC_FOLDER / DAY.isoformat())

    # 注入版 register_source_dir 验证「登记进来源目录」这个动作本身
    ps.register_source_dir(result.directory, add_dir_fn=lambda dirs: added.extend(dirs))
    assert added == [result.directory]


def test_batch_keeps_going_after_reject(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.os.path, "expanduser", lambda p: str(tmp_path))
    good = _pdf(tmp_path, "王五+13700000000+入团志愿书.pdf")
    bad = tmp_path / "x.txt"
    bad.write_text("x")
    result = ps.receive_batch([(bad, "x.txt"), (good, "王五+13700000000+入团志愿书.pdf")],
                              today=DAY)
    assert len(result.saved) == 1
    assert result.rejected[0][0] == "x.txt"
