"""携带登录态打开站点网页。

站点是单账号单会话：同账号再次登录会让之前的 SESSION 立刻失效，网页端会弹
「您的登录状态已失效，原因：登录已超4小时无进行操作或已在其他设备登录」。

两个叠加原因：
1. 浏览器里残留 .12355.net 域级 SESSION（早期 JS 写入方案留下，非 HttpOnly，
   可以用 JS 删除）。它与 tuanapi.12355.net 主机级 HttpOnly SESSION 同名，
   业务请求会同时带上两条，服务端读到先到达的旧值就判定失效。打开前先在
   tuan.12355.net 页面删掉域级那条，浏览器原生登录下发的主机级 SESSION
   就成了唯一值。
2. 后台自动链若对同一账号重新登录，会顶掉浏览器刚拿到的会话。30 秒数据链
   只取数不登录，自动测活跳过网页占用账号；网页打开成功后由 login_gate
   记录占用，手动操作会收回。

流程：tuan 页面清残留 cookie → 浏览器访问登录 GET 接口 → 服务端 Set-Cookie
覆盖主机级 SESSION → 导航回首页 → 读页面文本确认没有失效弹窗。登录接口与
网页端登录是同一个请求，密码只出现双重 md5，不泄露明文。

平台差异：
- macOS：AppleScript 控制 Arc/Safari。Arc 与 Safari 的 Cookie 互相独立，
  各保持一个账号登录，多账号按浏览器轮流分配。Safari 需开启开发菜单里的
  「允许来自苹果事件的 JavaScript」才能清残留 cookie。
- Windows/Linux：系统默认浏览器两步打开，没有执行 JS 的通道；若浏览器里
  也有域级残留 cookie，需要先在站点退出登录一次。

导航带回首页采用「设置 URL → 校验 → 未到位再设」的循环：登录接口的响应
是 JSON 正文（status:ok），先跳走过早会取消登录请求、卡在响应页则说明
上一次导航没执行；循环保证最终停在首页而不是 JSON 页。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import os
import time
import webbrowser
from urllib.parse import urlencode

from app.constants import API_BASE_URL, SITE_BASE_URL
from app.domain.login_protocol import LOGIN_PATH, build_login_query

SITE_HOME = f"{SITE_BASE_URL}/bg/index_main.html"

ARC_APP = "/Applications/Arc.app"

# 域级残留是旧方案用 JS 写的，非 HttpOnly，可以再用 JS 删掉。
# 主机级 HttpOnly 那条删不掉，但会被原生登录的 Set-Cookie 覆盖。
CLEAR_COOKIE_JS = (
    "document.cookie='SESSION=; domain=.12355.net; path=/; max-age=0';"
    "document.cookie='SESSION=; path=/; max-age=0';"
    "'cleared'"
)

# 站点把失效提示渲染在页面 DOM 里，读文本比只看 URL 更能确认登录态。
CHECK_LOGIN_JS = (
    "(function(){var b=document.body;if(!b)return 'nobody';"
    "var t=b.innerText||'';"
    "return t.indexOf('登录状态已失效')>=0?'expired':'ok'})()"
)


def _as_escape(s: str) -> str:
    """AppleScript 双引号字符串转义。"""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_login_url(user_name: str, plain_password: str, login_valid_code: str = "") -> str:
    """登录 GET 接口的完整 URL（网页端登录就是这个请求）。"""
    q = build_login_query(user_name, plain_password, login_valid_code)
    return f"{API_BASE_URL}{LOGIN_PATH}?{urlencode(q)}"


def available_browsers() -> list[str]:
    """本机可用于携带登录态打开的浏览器（按优先级）。

    macOS 的 Arc/Safari Cookie 空间互相独立（多账号轮流分配）；
    Windows/Linux 只有一个系统默认浏览器，即一个登录位。"""
    if sys.platform != "darwin":
        return ["默认浏览器"]
    return (["Arc"] if os.path.exists(ARC_APP) else []) + ["Safari"]


def _open_default_browser(login_url: str) -> tuple[bool, str]:
    """Windows/Linux：默认浏览器两步打开。"""
    if not webbrowser.open(login_url):
        return False, "系统默认浏览器打开失败"

    time.sleep(3)  # 等登录接口响应完成 Set-Cookie
    webbrowser.open(SITE_HOME)
    return True, "已在默认浏览器打开（浏览器原生登录）；登录接口的 JSON 响应页可手动关闭"


def _run_applescript(script: str, timeout: int = 20) -> tuple[bool, str]:
    """AppleScript 写临时文件执行（避免 shell 转义问题）。"""
    fd, path = tempfile.mkstemp(suffix=".applescript", text=True)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(script)
        try:
            p = subprocess.run(["osascript", path], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "AppleScript 等待超时（多半是 macOS 正在等你授权控制浏览器）"
        if p.returncode == 0:
            return True, p.stdout.strip()
        return False, (p.stderr or "").strip()
    finally:
        os.unlink(path)


def _clean(out: str) -> str:
    """Arc/Safari 的字符串结果可能带一层双引号。"""
    return (out or "").strip().strip('"').strip()


def _arc_scripts(login_url: str) -> tuple[str, str, str]:
    """分三段执行：清残留 cookie、原生登录回首页、确认页面状态。

    实测把三段塞进一个 osascript 进程会偶发卡住；分段后每段都能在数秒内
    返回，失败也能明确落在具体环节。Arc 只支持应用作用域的 window 1 /
    active tab 导航与执行 JS，捕获 make new tab 的返回值无法用于后续 set URL。"""
    clear_js = _as_escape(CLEAR_COOKIE_JS)
    check_js = _as_escape(CHECK_LOGIN_JS)
    prepare = (
        'tell application "Arc"\n'
        "  activate\n"
        "  if (count of windows) = 0 then make new window\n"
        f'  tell window 1 to make new tab with properties {{URL:"{SITE_HOME}"}}\n'
        "  delay 1\n"
        f'  set cleared to execute window 1\'s active tab javascript "{clear_js}"\n'
        "  return cleared as text\n"
        "end tell"
    )
    sign_in = (
        'tell application "Arc"\n'
        f'  set URL of active tab of window 1 to "{login_url}"\n'
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
    verify = (
        'tell application "Arc"\n'
        "  delay 2\n"
        f'  set state to execute window 1\'s active tab javascript "{check_js}"\n'
        "  return state as text\n"
        "end tell"
    )
    return prepare, sign_in, verify


def _safari_scripts(login_url: str) -> tuple[str, str, str]:
    clear_js = _as_escape(CLEAR_COOKIE_JS)
    check_js = _as_escape(CHECK_LOGIN_JS)
    prepare = (
        'tell application "Safari"\n'
        "  activate\n"
        f'  make new document with properties {{URL:"{SITE_HOME}"}}\n'
        "  delay 1\n"
        f'  set cleared to do JavaScript "{clear_js}" in front document\n'
        "  return cleared as text\n"
        "end tell"
    )
    sign_in = (
        'tell application "Safari"\n'
        f'  set URL of front document to "{login_url}"\n'
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
    verify = (
        'tell application "Safari"\n'
        "  delay 2\n"
        f'  set state to do JavaScript "{check_js}" in front document\n'
        "  return state as text\n"
        "end tell"
    )
    return prepare, sign_in, verify


def open_logged_in(client, browser: str = "Arc") -> tuple[bool, str]:
    """用浏览器原生登录并打开站点。返回 (成功, 说明)。

    浏览器是这次操作唯一的登录者：Python 端不再先登录一次，避免刚生成的
    会话立刻被浏览器顶掉。成功后登记网页会话占用，让自动链不再抢登录。"""
    user, pwd = client.user_name, client.password
    if not user or not pwd:
        return False, "账号或密码为空"

    login_url = build_login_url(user, pwd)
    if sys.platform != "darwin":
        ok, msg = _open_default_browser(login_url)
        browser = "默认浏览器"
    else:
        scripts = _arc_scripts(login_url) if browser == "Arc" else _safari_scripts(login_url)
        labels = ("清理残留 cookie", "浏览器原生登录", "确认页面状态")
        results = []
        for label, script in zip(labels, scripts):
            run_ok, out = _run_applescript(script)
            if not run_ok:
                hint = ""
                low = out.lower()
                if "not allowed" in low or "allow" in low:
                    hint = "（请在系统设置允许控制浏览器）"
                if browser == "Safari" and ("javascript" in low or "允许来自苹果事件" in out):
                    hint = "（请在 Safari 开发菜单开启允许来自苹果事件的 JavaScript）"
                return False, f"{browser} {label}失败：{out}{hint}"
            results.append(_clean(out))

        cleared, final_url, state = results
        if "index_main" not in final_url:
            return False, f"{browser} 未跳转回首页（最终停在 {final_url or '未知页面'}），登录态可能不完整"
        if cleared != "cleared":
            return False, f"{browser} 未能清除域级残留 cookie（{cleared or '无返回'}），网页端可能提示登录已失效"
        if state == "expired":
            return False, (f"{browser} 已打开首页，但站点仍提示登录状态已失效；"
                           "该账号可能需要验证码或密码有误，请先在网页端手动登录一次")
        if state != "ok":
            return False, f"{browser} 未能确认页面登录状态（{state or '无返回'}）"
        ok, msg = True, f"已在 {browser} 打开，后台自动刷新已让路"

    if ok:
        from app.services import login_gate

        login_gate.mark_browser_session(user, browser)
    return ok, msg
