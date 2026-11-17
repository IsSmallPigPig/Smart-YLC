"""登录闸门：降低登录频率，避免触发验证码/风控。

三类规则（用户指定）：
1. 会话复用——登录成功后把 cookie 存进配置（sessionCookies），下次先恢复
   会话再只读探测，探测有效就不登录。此前每个 Worker 都用空会话，探测
   必然失败 → 每轮（测活 15 分钟一次 + 刷新链）都真登录，这是「部分账号
   跳登陆频繁」的根因。
2. 验证码/风控冷却——登录要求验证码，或消息命中风控关键词（频繁/稍后/
   锁定等），1 小时内不再尝试登录，所有自动刷新跳过该账号。
3. 登录状态刷新节流——状态「正常」的账号，最后刷新时间距今未超 4 小时
   就不再重复测活、探测或登录。同步系统数据是另一个操作，按 30 秒周期
   执行，用已保存的会话直接取数，不受这 4 小时约束。

同步系统数据只恢复会话取数，不自行登录：站点是单账号单会话，自动链每轮
重新登录会把用户在网页端刚拿到的会话顶掉。自动测活也跳过网页占用账号。
手动操作（上传、网页打开、手动测活、手动刷新所有数据）会收回网页占用，
仍可登录。
"""

from __future__ import annotations

import re
import time
from datetime import datetime

from app.services import store

COOLDOWN_SECONDS = 3600  # 验证码/风控后 1 小时内不登录
FRESH_HOURS = 4  # 离线缓存核对窗口（上传页判断成员缓存是否够新）
STATUS_FRESH_HOURS = 4  # 刷新登录状态的节流窗口
BROWSER_SESSION_HOURS = 4  # 网页会话占用窗口，与站点会话超时一致

# 风控关键词：命中即判定触发风控系统（比验证码更严格的 1 小时硬冷却）
RISK_RE = re.compile(r"风控|频繁|稍后再试|请稍后|稍后重试|太快|锁定|封禁|禁止登录")

# 这些状态属于「本轮跳过」而非失败：界面提示用 info 而不是 warning
# 冷却中的账号跳过属于预期；session 是数据链只用已保存会话取数的结果
SKIP_KINDS = {"cooldown", "session"}


# ---------- 会话复用 ----------

def restore_session(client) -> None:
    """把上次登录保存的 cookie 恢复进会话，探测有效即可免登录。"""
    rows = (store.load().get("sessionCookies") or {}).get(client.user_name) or []
    for c in rows:
        try:
            client.http.cookies.set(c.get("name", ""), c.get("value", ""),
                                    domain=c.get("domain", ""), path=c.get("path", "/"))
        except Exception:
            pass  # 单条 cookie 损坏不影响其余


def save_session(client) -> None:
    rows = [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
        for c in client.http.cookies
    ]
    if not rows:
        return

    def _write(cfg, user=client.user_name, rows=rows):
        sc = cfg.get("sessionCookies", {})
        sc[user] = rows
        cfg["sessionCookies"] = sc

    store.update(_write)


# ---------- 冷却闸门 ----------

def _until(kind_key: str, user: str) -> float:
    st = (store.load().get("loginGate") or {}).get(user) or {}
    try:
        return float(st.get(kind_key) or 0)
    except (TypeError, ValueError):
        return 0.0


def blocked_reason(user: str) -> str:
    """冷却中返回给用户看的理由；否则空串。"""
    now = time.time()
    risk_until = _until("riskUntil", user)
    cap_until = _until("captchaUntil", user)
    if risk_until and now < risk_until:
        mins = max(1, int((risk_until - now) // 60))
        return f"该账号触发了风控系统，约 {mins} 分钟后才能登录，本轮跳过其数据刷新"
    if cap_until and now < cap_until:
        mins = max(1, int((cap_until - now) // 60))
        return f"上次登录要求验证码（密码失败过多），约 {mins} 分钟后再试，本轮跳过其数据刷新"
    return ""


def _set_gate(user: str, mutate) -> None:
    def _write(cfg, user=user, mutate=mutate):
        gate = cfg.get("loginGate", {})
        st = gate.get(user) or {}
        mutate(st)
        gate[user] = st
        cfg["loginGate"] = gate

    store.update(_write)


def _record_login_ok(client) -> None:
    _set_gate(client.user_name, lambda st: st.update(
        {"lastLoginAt": time.time(), "riskUntil": 0, "captchaUntil": 0}))
    save_session(client)


def _record_cooldown(user: str, key: str) -> None:
    until = time.time() + COOLDOWN_SECONDS
    _set_gate(user, lambda st, key=key, until=until: st.update(
        {key: until, "lastLoginAt": time.time()}))


# ---------- 统一登录管线 ----------

def ensure_logged_in(client, allow_login: bool = True) -> tuple[str, str]:
    """恢复会话 → 只读探测 → 必要时登录（带冷却闸门）。

    allow_login=False 用于 30 秒数据链：只用已保存会话取数，会话失效就跳过
    该账号，由「刷新登录状态」负责登录，避免顶掉网页端正在使用的会话。
    返回 (kind, message)。kind: ok / session / network / cooldown / risk /
    captcha / passwordChange / needMemberName / failed。除 ok 外调用方都应跳过。"""
    user = client.user_name
    restore_session(client)
    probe = client.session_account()
    if probe.ok:
        return "ok", probe.message or "会话有效"
    if not allow_login:
        return "session", "会话已失效，本轮只取数，登录状态由测活刷新"
    # 探测失败分两种：网络异常（不做冷却判定也不记失败）和会话失效
    if "探测失败" in probe.message or "网络" in probe.message:
        return "network", probe.message

    reason = blocked_reason(user)
    if reason:
        return "cooldown", reason

    verdict = client.login()
    kind = str((verdict.data or {}).get("kind", "failed")) if isinstance(verdict.data, dict) else "failed"
    message = verdict.message or kind
    if kind == "ok":
        _record_login_ok(client)
        return "ok", message
    if RISK_RE.search(message):
        _record_cooldown(user, "riskUntil")
        return "risk", f"{message}（触发风控，1 小时内不再尝试登录）"
    if kind == "captcha":
        _record_cooldown(user, "captchaUntil")
        return "captcha", f"{message}（本轮跳过该账号的数据刷新，稍后自动重试）"
    return kind, message


# ---------- 登录状态刷新节流 ----------

def status_fresh(acc: dict) -> str:
    """刷新登录状态的节流：最后刷新时间距今未超 4 小时则跳过。

    只约束「刷新登录状态」（测活、会话探测、必要时重新登录），不约束
    「同步系统数据」——后者按 30 秒周期用已保存的会话直接取数。
    只有状态正常的账号享受节流：异常账号要留着，等验证码/风控冷却
    结束后由测活自动恢复。checkedAt 由测活写入，是本地时间。"""
    health = acc.get("health") or {}
    if health.get("status") != "ok":
        return ""
    ts = health.get("checkedAt", "")
    if not ts:
        return ""
    try:
        age_h = (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 3600
    except ValueError:
        return ""
    if 0 <= age_h < STATUS_FRESH_HOURS:
        return f"登录状态 {age_h:.1f} 小时前刚刷新（未超 {STATUS_FRESH_HOURS} 小时），本轮跳过"
    return ""


# ---------- 网页会话占用 ----------

def mark_browser_session(user: str, browser: str) -> None:
    """网页端原生登录成功后登记：自动链在窗口期内不再抢该账号的登录。"""
    def _write(cfg, user=user, browser=browser):
        sessions = cfg.get("browserSessions") or {}
        sessions[user] = {"browser": browser, "at": time.time()}
        cfg["browserSessions"] = sessions

    store.update(_write)


def active_browser_sessions() -> dict:
    """仍在窗口期内的网页会话：{user: {browser, at}}。"""
    raw = store.load().get("browserSessions") or {}
    now = time.time()
    out = {}
    for user, st in raw.items():
        try:
            at = float((st or {}).get("at") or 0)
        except (TypeError, ValueError):
            continue
        if at and now - at < BROWSER_SESSION_HOURS * 3600:
            out[user] = st
    return out


def clear_browser_sessions(users=None) -> None:
    """手动操作收回网页占用：之后的自动测活与登录恢复正常。

    users 给定时只收回这些账号，其余账号正在使用的网页会话保持登记。"""
    def _write(cfg, users=users):
        if users is None:
            cfg["browserSessions"] = {}
        else:
            sessions = dict(cfg.get("browserSessions") or {})
            for user in users:
                sessions.pop(user, None)
            cfg["browserSessions"] = sessions

    store.update(_write)
