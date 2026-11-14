"""携带登录态打开站点网页。

原理：不再向页面伪造 cookie。会话 cookie（SESSION）由 tuanapi.12355.net 以
HttpOnly 下发，此前从 tuan 页面写 domain 级 cookie 会被浏览器里残留的
tuanapi 主机级旧 SESSION 遮蔽（同名主机 cookie 在请求中排在前面，服务端
先读到旧会话 →「登录已超4小时/已在其他设备登录」）。HttpOnly 主机 cookie
无法用 JS 覆盖，伪造方案天然失效。

改用浏览器原生登录：直接用 AppleScript 让浏览器访问登录 GET 接口
tuanapi.12355.net/login/adminLogin?userName=&password=md5(md5(明文))&…
（与网页端登录是同一个请求，密码只出现双重 md5，不泄露明文）。服务器对
这个真实浏览器请求 Set-Cookie，覆盖旧的主机级 SESSION；随后把标签页
导航回 tuan.12355.net/bg/index_main.html，即为完整登录态。

浏览器优先级：Arc → Safari（需先在 Safari 开启「开发 → 允许来自苹果事件
的 JavaScript」——本流程只用 set URL，通常无需该开关）。
"""

from __future__ import annotations

import subprocess
import tempfile
import os
from urllib.parse import urlencode

from app.constants import API_BASE_URL, SITE_BASE_URL
from app.domain.login_protocol import LOGIN_PATH, build_login_query

SITE_HOME = f"{SITE_BASE_URL}/bg/index_main.html"


def build_login_url(user_name: str, plain_password: str, login_valid_code: str = "") -> str:
    """登录 GET 接口的完整 URL（网页端登录就是这个请求）。"""
    q = build_login_query(user_name, plain_password, login_valid_code)
    return f"{API_BASE_URL}{LOGIN_PATH}?{urlencode(q)}"


def _run_applescript(script: str, timeout: int = 30) -> tuple[bool, str]:
    """AppleScript 写临时文件执行（避免 shell 转义问题）。"""
    fd, path = tempfile.mkstemp(suffix=".applescript", text=True)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(script)
        try:
            p = subprocess.run(["osascript", path], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "AppleScript 等待超时（多半是 macOS 正在等你点「允许」控制浏览器的授权框）"
        if p.returncode == 0:
            return True, p.stdout.strip()
        return False, (p.stderr or "").strip()
    finally:
        os.unlink(path)


def _arc_script(login_url: str) -> str:
    return (
        'tell application "Arc"\n'
        "  activate\n"
        f'  tell front window to make new tab with properties {{URL:"{login_url}"}}\n'
        "  delay 2\n"
        '  set URL of active tab of front window to "' + SITE_HOME + '"\n'
        "end tell"
    )


def _safari_script(login_url: str) -> str:
    return (
        'tell application "Safari"\n'
        "  activate\n"
        f'  make new document with properties {{URL:"{login_url}"}}\n'
        "  delay 2\n"
        f'  set URL of front document to "{SITE_HOME}"\n'
        "end tell"
    )


def open_logged_in(client) -> tuple[bool, str]:
    """确保会话有效后，用浏览器原生登录并打开站点。返回 (成功, 说明)。

    先恢复上次保存的会话探测，有效就不登录；失效才走浏览器原生登录
    （服务器 Set-Cookie 覆盖旧会话）。"""
    from app.services import login_gate

    login_gate.restore_session(client)
    probe = client.session_account()
    if not probe.ok:
        verdict = client.login()
        if not verdict.ok:
            return False, f"登录失败：{verdict.message}"
    login_url = build_login_url(client.user_name, client.password)
    ok, msg = _run_applescript(_arc_script(login_url))
    if ok:
        return True, "已在 Arc 打开（浏览器原生登录）"
    arc_err = msg
    ok, msg = _run_applescript(_safari_script(login_url))
    if ok:
        return True, "已在 Safari 打开（浏览器原生登录）"
    hint = ""
    if "not allowed" in (msg + arc_err).lower() or "allow" in msg.lower():
        hint = "（浏览器拒绝了 AppleScript 控制，请在系统设置允许）"
    return False, f"浏览器打开失败：{msg or arc_err}{hint}"
