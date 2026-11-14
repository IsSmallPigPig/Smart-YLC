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

导航带回首页采用「设置 URL → 校验 → 未到位再设」的循环：登录接口的响应
是 JSON 正文（status:ok），先跳走过早会取消登录请求、卡在响应页则说明
上一次导航没执行；循环保证最终停在首页而不是 JSON 页。

浏览器与多账号：优先 Arc，未安装才用 Safari；打开失败不换浏览器重试
（此前 Arc 脚本中途报错回退 Safari，会同时拉起两个浏览器）。Arc 与 Safari
的 Cookie 互相独立，各自只能保持一个账号的登录态——同一浏览器里打开
第二个账号会顶掉第一个的 SESSION（服务端按主机 cookie 识别会话），
多账号打开时由调用方按账号轮流分配浏览器。
"""

from __future__ import annotations

import subprocess
import tempfile
import os
from urllib.parse import urlencode

from app.constants import API_BASE_URL, SITE_BASE_URL
from app.domain.login_protocol import LOGIN_PATH, build_login_query

SITE_HOME = f"{SITE_BASE_URL}/bg/index_main.html"

ARC_APP = "/Applications/Arc.app"


def build_login_url(user_name: str, plain_password: str, login_valid_code: str = "") -> str:
    """登录 GET 接口的完整 URL（网页端登录就是这个请求）。"""
    q = build_login_query(user_name, plain_password, login_valid_code)
    return f"{API_BASE_URL}{LOGIN_PATH}?{urlencode(q)}"


def available_browsers() -> list[str]:
    """本机可用于携带登录态打开的浏览器（按优先级）。"""
    return (["Arc"] if os.path.exists(ARC_APP) else []) + ["Safari"]


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
    # 实测：Arc 只支持应用作用域的 window 1 / active tab 导航；
    # 捕获 make new tab 返回的引用无法用于后续 set URL（-1700）。
    return (
        'tell application "Arc"\n'
        "  activate\n"
        "  if (count of windows) = 0 then make new window\n"
        f'  tell window 1 to make new tab with properties {{URL:"{login_url}"}}\n'
        "  delay 3\n"
        f'  set URL of active tab of window 1 to "{SITE_HOME}"\n'
        "  repeat 3 times\n"
        "    delay 1\n"
        '    if URL of active tab of window 1 contains "index_main" then exit repeat\n'
        f'    set URL of active tab of window 1 to "{SITE_HOME}"\n'
        "  end repeat\n"
        "  return URL of active tab of window 1\n"
        "end tell"
    )


def _safari_script(login_url: str) -> str:
    return (
        'tell application "Safari"\n'
        "  activate\n"
        f'  make new document with properties {{URL:"{login_url}"}}\n'
        "  delay 3\n"
        f'  set URL of front document to "{SITE_HOME}"\n'
        "  repeat 3 times\n"
        "    delay 1\n"
        '    if URL of front document contains "index_main" then exit repeat\n'
        f'    set URL of front document to "{SITE_HOME}"\n'
        "  end repeat\n"
        "  return URL of front document\n"
        "end tell"
    )


def open_logged_in(client, browser: str = "Arc") -> tuple[bool, str]:
    """确保会话有效后，用指定浏览器原生登录并打开站点。返回 (成功, 说明)。

    先恢复上次保存的会话探测，有效就不登录；失效才走程序化登录
    （浏览器侧始终走原生登录，保证浏览器里的 SESSION 是本账号的）。"""
    from app.services import login_gate

    login_gate.restore_session(client)
    probe = client.session_account()
    if not probe.ok:
        verdict = client.login()
        if not verdict.ok:
            return False, f"登录失败：{verdict.message}"
    login_url = build_login_url(client.user_name, client.password)
    script = _arc_script(login_url) if browser == "Arc" else _safari_script(login_url)
    ok, final_url = _run_applescript(script)
    if ok and "index_main" in final_url:
        return True, f"已在 {browser} 打开（浏览器原生登录）"
    if ok:
        # 脚本跑完但没停在首页：登录响应页没有被替换掉，导航环节出了问题
        return False, f"{browser} 未跳转回首页（最终停在 {final_url or '未知页面'}），登录态可能不完整"
    hint = ""
    if "not allowed" in final_url.lower() or "allow" in final_url.lower():
        hint = "（浏览器拒绝了 AppleScript 控制，请在系统设置允许）"
    return False, f"{browser} 打开失败：{final_url}{hint}"
