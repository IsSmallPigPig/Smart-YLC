"""登录闸门：降低登录频率，避免触发验证码/风控。

三类规则（用户指定）：
1. 会话复用——登录成功后把 cookie 存进配置（sessionCookies），下次先恢复
   会话再只读探测，探测有效就不登录。此前每个 Worker 都用空会话，探测
   必然失败 → 每轮（测活 15 分钟一次 + 刷新链）都真登录，这是「部分账号
   跳登陆频繁」的根因。
2. 验证码/风控冷却——登录要求验证码，或消息命中风控关键词（频繁/稍后/
   锁定等），1 小时内不再尝试登录，所有自动刷新跳过该账号。
3. 数据新鲜跳过——测活状态「正常」的账号，数据 4 小时内刷新过就无需再
   刷新（不发探测也不登录）。

手动操作（上传、网页打开）只受风控/验证码冷却约束，不受新鲜度跳过。
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from app.services import store

COOLDOWN_SECONDS = 3600  # 验证码/风控后 1 小时内不登录
FRESH_HOURS = 4  # 状态正常的账号，数据 4 小时内刷新过则跳过

# 风控关键词：命中即判定触发风控系统（比验证码更严格的 1 小时硬冷却）
RISK_RE = re.compile(r"风控|频繁|稍后再试|请稍后|稍后重试|太快|锁定|封禁|禁止登录")

# 这些状态属于「本轮跳过」而非失败：界面提示用 info 而不是 warning
SKIP_KINDS = {"fresh", "cooldown"}


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

def ensure_logged_in(client) -> tuple[str, str]:
    """恢复会话 → 只读探测 → 必要时登录（带冷却闸门）。

    返回 (kind, message)。kind: ok / network / cooldown / risk / captcha /
    passwordChange / needMemberName / failed。除 ok 外调用方都应跳过该账号。"""
    user = client.user_name
    restore_session(client)
    probe = client.session_account()
    if probe.ok:
        return "ok", probe.message or "会话有效"
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


# ---------- 数据新鲜跳过 ----------

def fresh_skip(acc: dict, cache_key: str) -> str:
    """状态「正常」的账号，数据 FRESH_HOURS 小时内刷新过则无需再刷新。

    acc 需带 health；cache_key 是 memberCache / messagesCache。
    返回跳过理由，不跳过返回空串。"""
    if (acc.get("health") or {}).get("status") != "ok":
        return ""
    blob = (store.load().get(cache_key) or {}).get(acc.get("userName", "")) or {}
    ts = blob.get("fetchedAt", "")
    if not ts:
        return ""
    try:
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 3600
    except ValueError:
        return ""
    if 0 <= age_h < FRESH_HOURS:
        return f"账号状态正常，数据 {age_h:.1f} 小时前刚刷新（未超 {FRESH_HOURS} 小时），本轮无需刷新"
    return ""
