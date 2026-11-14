"""携带登录态打开站点网页。

原理：登录成功后从 requests 会话收集 cookie，用 AppleScript 驱动浏览器先打开
12355.net 域下页面，再用 JS 把 cookie 写成 Domain=.12355.net 的域级 cookie
（页面对父域可写，写入后 tuan / tuanapi 两个主机都会带上），最后跳转回首页。

浏览器优先级：Arc（Chromium，原生支持 execute javascript）→ Safari
（需先在 Safari 开启「开发 → 允许来自苹果事件的 JavaScript」）。
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import os

SITE_HOME = "https://tuan.12355.net/bg/index_main.html"
DOMAIN_SUFFIX = ".12355.net"


def _js_escape(s: str) -> str:
    """AppleScript 双引号字符串转义（外层 AppleScript 用双引号包裹，内层 JS 用单引号）。"""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def build_cookie_js(cookies) -> str:
    """cookie 列表 → 写入域级 cookie 并跳转首页的 JS。"""
    stmts = []
    for c in cookies:
        name, value = c.name, c.value or ""
        if not name:
            continue
        stmts.append(
            f"document.cookie='{_js_escape(name)}={_js_escape(value)}; path=/; domain={DOMAIN_SUFFIX}'"
        )
    if not stmts:
        raise ValueError("会话里没有可用的 12355 cookie，请先测活成功后再试")
    return ";".join(stmts) + f";location.href='{SITE_HOME}';"


def _run_applescript(script: str, timeout: int = 25) -> tuple[bool, str]:
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


def _arc_script(js: str) -> str:
    return (
        'tell application "Arc"\n'
        "  activate\n"
        f'  tell front window to make new tab with properties {{URL:"{SITE_HOME}"}}\n'
        "  delay 2\n"
        f'  execute front window\'s active tab javascript "{_js_escape(js)}"\n'
        "end tell"
    )


def _safari_script(js: str) -> str:
    return (
        'tell application "Safari"\n'
        "  activate\n"
        f'  make new document with properties {{URL:"{SITE_HOME}"}}\n'
        "  delay 2\n"
        f'  do JavaScript "{_js_escape(js)}" in front document\n'
        "end tell"
    )


def open_logged_in(client) -> tuple[bool, str]:
    """确保会话有效后，携带 cookie 在浏览器打开站点。返回 (成功, 说明)。"""
    probe = client.session_account()
    if not probe.ok:
        verdict = client.login()
        if not verdict.ok:
            return False, f"登录失败：{verdict.message}"
    cookies = [c for c in client.http.cookies if "12355" in (c.domain or "")]
    try:
        js = build_cookie_js(cookies)
    except ValueError as e:
        return False, str(e)
    ok, msg = _run_applescript(_arc_script(js))
    if ok:
        return True, "已在 Arc 打开（携带登录态）"
    arc_err = msg
    ok, msg = _run_applescript(_safari_script(js))
    if ok:
        return True, "已在 Safari 打开（携带登录态）"
    hint = ""
    if "not allowed" in (msg + arc_err).lower() or "allow" in msg.lower():
        hint = "（Safari 需先在「开发」菜单勾选「允许来自苹果事件的 JavaScript」）"
    return False, f"浏览器打开失败：{msg or arc_err}{hint}"
