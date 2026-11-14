import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.domain.chunk_protocol import (
    build_chunk_fields,
    build_file_add_body,
    build_file_id,
    build_validation_body,
    check_site_limits,
    chunk_count,
    chunk_range,
    parse_chunk_response,
    parse_audit_response,
    parse_file_add_response,
    parse_validation_response,
)

MB = 1024 * 1024


def test_chunk_count():
    assert chunk_count(0) == 1
    assert chunk_count(9 * MB) == 1
    assert chunk_count(10 * MB) == 1
    assert chunk_count(10 * MB + 1) == 2
    assert chunk_count(25 * MB) == 3


def test_chunk_range():
    assert chunk_range(0, 25 * MB) == (0, 10 * MB)
    assert chunk_range(2, 25 * MB) == (20 * MB, 25 * MB)
    assert chunk_range(0, 0) == (0, 0)


def test_chunk_fields():
    f = build_chunk_fields("file_1", "a.pdf", 25 * MB, 1, 10 * MB)
    assert f["chunks"] == "3" and f["chunk"] == "1" and f["name"] == "a.pdf" and f["md5"] == ""


def test_file_add_body():
    assert build_file_add_body("A.B.PDF") == {"fileName": "A.B.PDF", "suffix": "pdf"}


def test_parse_validation():
    raw = {"status": "OK", "msg": {"data": [{"name": "a.pdf", "status": "0", "msg": "ok"}]}}
    items = parse_validation_response(raw)
    assert items[0].ok is True and items[0].name == "a.pdf"
    raw2 = {"status": "OK", "msg": {"data": [{"name": "b.pdf", "status": "1", "msg": "超限"}]}}
    assert parse_validation_response(raw2)[0].ok is False
    assert parse_validation_response(None) == []
    assert parse_validation_response({"status": "OK"}) == []


def test_parse_file_add():
    r = parse_file_add_response({"status": "OK", "msg": {"mid": "m1", "retpath": "/f", "token": "t"}})
    assert r.ok and r.mid == "m1"
    r2 = parse_file_add_response({"status": "ERROR", "msg": "x"})
    assert not r2.ok
    assert not parse_file_add_response(None).ok


def test_parse_audit():
    ok, msg = parse_audit_response({"status": "OK", "msg": "成功"})
    assert ok and msg == "成功"
    ok, msg = parse_audit_response({"status": "OK"})
    assert ok and msg == "审核通过"
    ok, msg = parse_audit_response({"status": "ERROR", "msg": "未报到"})
    assert not ok and msg == "未报到"
    ok, msg = parse_audit_response({"status": "EXPIRE"})
    assert not ok and "EXPIRE" in msg
    ok, msg = parse_audit_response(None)
    assert not ok


def test_parse_chunk():
    assert parse_chunk_response(None)[0] is True
    assert parse_chunk_response({})[0] is True
    assert parse_chunk_response({"status": "ERROR", "msg": "no"})[0] is False
    assert parse_chunk_response({"status": "OK"})[0] is True


def test_site_limits():
    ok = [{"name": "a.pdf", "size": 9 * MB}]
    assert check_site_limits(ok) == []
    bad = [
        {"name": "big.pdf", "size": 11 * MB},
        {"name": "x.jpg", "size": 1 * MB},
    ] * 30  # 60 份
    reasons = check_site_limits(bad)
    assert any("最多" in r for r in reasons)
    assert any("10MB" in r for r in reasons)
    assert any(".pdf" in r for r in reasons)


def test_build_file_id():
    assert build_file_id(3, 1700000000000) == "file_1700000000000_3"


def test_validation_body():
    assert build_validation_body(["a.pdf"]) == {"fileNames": ["a.pdf"]}
