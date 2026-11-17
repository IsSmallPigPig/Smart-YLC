import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.domain.login_protocol import (
    build_captcha_query,
    build_login_query,
    double_md5,
    is_strong_password,
    parse_login_response,
)


def test_strong_password():
    assert is_strong_password("-abc12345!") is True
    assert is_strong_password("abc12345") is False  # 无特殊字符
    assert is_strong_password("-ab1") is False  # 太短


def test_double_md5():
    # md5(md5("x")) 独立复算
    import hashlib

    expect = hashlib.md5(hashlib.md5(b"x").hexdigest().encode()).hexdigest()
    assert double_md5("x") == expect


def test_login_query():
    q = build_login_query("ssfz12407", "-abc12345")
    assert q["userName"] == "ssfz12407"
    assert q["weakPass"] == 0
    assert q["password"] == double_md5("-abc12345")
    assert q["loginValidCode"] == ""


def test_weak_flag():
    assert build_login_query("u", "abc123")["weakPass"] == 1


def test_parse_ok():
    v = parse_login_response({"status": "OK", "msg": "ok"})
    assert v.kind == "ok"
    v = parse_login_response({"status": "OK", "data": "1"})
    assert v.kind == "needMemberName"


def test_parse_password_change():
    v = parse_login_response({"status": "EXPIREDPASS", "data": "tk", "msg": ""})
    assert v.kind == "passwordChange" and v.token == "tk"
    v = parse_login_response({"status": "WEAKPASS"})
    assert v.kind == "passwordChange" and v.status == "WEAKPASS"


def test_parse_captcha():
    v = parse_login_response({"status": "ERROR", "msg": "密码错误次数过多，请输入验证码"})
    assert v.kind == "captcha"


def test_parse_failed():
    v = parse_login_response({"status": "ERROR", "msg": "账号或密码错误"})
    assert v.kind == "failed"


def test_captcha_query():
    q = build_captcha_query(1700000000000)
    assert q == {"t": 1700000000000}
