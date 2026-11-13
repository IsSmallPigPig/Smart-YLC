"""登录协议（移植自 login-protocol.ts，站点只读侦察结论）。

League.adminLogin → League.ApiProxy('/login/adminLogin', params) → GET query
params = { weakPass, userName, password: md5(md5(明文)), loginValidCode }
weakPass = 0 强密码（Utils.checkPassword 为 true），1 弱密码
成功 → 前端跳 index_main.html；失败 → isShowValidCode=true，之后需要图形验证码
图形验证码：GET /login/loginValidCode?t=<ts>，头 POWERED-BY-MENGXIANHUI: Approve
特殊状态：EXPIREDPASS / WEAKPASS（需改密）、data.data == 1（需校验成员姓名）
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass

LOGIN_PATH = "/login/adminLogin"
CAPTCHA_PATH = "/login/loginValidCode"
CAPTCHA_HEADER = "POWERED-BY-MENGXIANHUI"

STRONG_RE = re.compile(r"(?=.*\d)(?=.*[a-zA-Z])(?=.*[^a-zA-Z0-9]).{8,}")


def is_strong_password(plain: str) -> bool:
    """与站点 Utils.checkPassword 一致：8 位以上 + 数字 + 字母 + 特殊字符。"""
    return STRONG_RE.search(plain) is not None


def _md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def double_md5(plain: str) -> str:
    return _md5_hex(_md5_hex(plain))


def build_login_query(user_name: str, plain_password: str, login_valid_code: str = "") -> dict:
    return {
        "weakPass": 0 if is_strong_password(plain_password) else 1,
        "userName": user_name,
        "password": double_md5(plain_password),
        "loginValidCode": login_valid_code,
    }


def build_captcha_query(now: float | None = None) -> dict:
    return {"t": int(now if now is not None else time.time() * 1000)}


@dataclass
class LoginVerdict:
    kind: str  # ok | needMemberName | passwordChange | captcha | failed
    message: str
    status: str = ""
    token: str = ""


CAPTCHA_HINT_RE = re.compile(r"验证码|validCode|captcha|错误次数|失败", re.IGNORECASE)


def parse_login_response(raw) -> LoginVerdict:
    r = raw if isinstance(raw, dict) else {}
    status = str(r.get("status", ""))
    msg = str(r.get("msg") or r.get("message") or "")
    if status in ("EXPIREDPASS", "WEAKPASS"):
        return LoginVerdict("passwordChange", msg or "需修改密码后才能登录", status, str(r.get("data", "")))
    if status in ("OK", "200"):
        if str(r.get("data", "")) == "1":
            return LoginVerdict("needMemberName", msg or "需要校验成员姓名")
        return LoginVerdict("ok", msg or "登录成功")
    if CAPTCHA_HINT_RE.search(msg):
        return LoginVerdict("captcha", msg)
    return LoginVerdict("failed", msg or f"登录失败（status={status or '空'}）")
