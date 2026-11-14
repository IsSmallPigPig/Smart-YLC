"""登录闸门：会话复用、验证码/风控冷却、登录状态 4 小时节流。"""

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from requests.cookies import RequestsCookieJar

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def gate_env(tmp_path, monkeypatch):
    from app.services import login_gate, store

    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    return login_gate, store


class FakeClient:
    """模拟 SiteClient：probe 结果与 login 响应序列可控，统计登录次数。"""

    def __init__(self, user, probes, logins):
        self.user_name = user
        self.password = "x"
        self.http = SimpleNamespace(cookies=RequestsCookieJar())
        self.probes = list(probes)  # [(ok, message)]
        self.logins = list(logins)  # [(kind, message)]
        self.login_calls = 0

    def session_account(self):
        from app.services.api import ApiResult

        ok, msg = self.probes.pop(0) if self.probes else (False, "会话已失效")
        return ApiResult(ok, msg, None)

    def login(self, login_valid_code: str = ""):
        from app.services.api import ApiResult

        self.login_calls += 1
        kind, msg = self.logins.pop(0) if self.logins else ("failed", "登录失败")
        return ApiResult(kind == "ok", msg, {"kind": kind, "status": ""})


def test_probe_ok_skips_login(gate_env):
    gate, _ = gate_env
    c = FakeClient("u1", [(True, "管理员")], [("ok", "登录成功")])
    kind, msg = gate.ensure_logged_in(c)
    assert kind == "ok" and c.login_calls == 0


def test_login_ok_persists_session(gate_env):
    gate, store = gate_env
    c = FakeClient("u1", [(False, "会话已失效")], [("ok", "登录成功")])
    c.http.cookies.set("SESSION", "abc", domain="tuanapi.12355.net", path="/")
    kind, _ = gate.ensure_logged_in(c)
    assert kind == "ok" and c.login_calls == 1
    saved = store.load()["sessionCookies"]["u1"]
    assert saved[0]["name"] == "SESSION" and saved[0]["value"] == "abc"
    # 下次用同一 FakeClient 探测成功 → 不再登录
    c2 = FakeClient("u1", [(True, "管理员")], [])
    kind2, _ = gate.ensure_logged_in(c2)
    assert kind2 == "ok" and c2.login_calls == 0


def test_risk_message_blocks_one_hour(gate_env):
    gate, store = gate_env
    c = FakeClient("u1", [(False, "会话已失效")] * 2,
                   [("failed", "操作过于频繁，请稍后再试")] * 2)
    kind, msg = gate.ensure_logged_in(c)
    assert kind == "risk" and "风控" in msg
    # 冷却期内第二次直接跳过，不再发登录请求
    kind2, msg2 = gate.ensure_logged_in(c)
    assert kind2 == "cooldown" and c.login_calls == 1
    until = store.load()["loginGate"]["u1"]["riskUntil"]
    assert 0 < until - __import__("time").time() <= 3600


def test_captcha_blocks_refresh(gate_env):
    gate, _ = gate_env
    c = FakeClient("u2", [(False, "会话已失效")] * 2,
                   [("captcha", "密码错误次数过多，需要验证码")] * 2)
    kind, msg = gate.ensure_logged_in(c)
    assert kind == "captcha" and "跳过" in msg
    kind2, _ = gate.ensure_logged_in(c)
    assert kind2 == "cooldown" and c.login_calls == 1


def test_probe_network_failure_no_login(gate_env):
    gate, _ = gate_env
    c = FakeClient("u3", [(False, "会话探测失败：连接超时")], [("ok", "登录成功")])
    kind, _ = gate.ensure_logged_in(c)
    assert kind == "network" and c.login_calls == 0  # 网络问题不触发登录也不记失败


def test_status_fresh_rules(gate_env):
    """刷新登录状态按每个账号的最后刷新时间节流，数据同步不参与。"""
    gate, _ = gate_env

    def _acc(hours_ago, status="ok"):
        ts = (datetime.now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
        return {"userName": "u1", "health": {"status": status, "checkedAt": ts}}

    assert gate.status_fresh(_acc(1))                   # 正常 + 1 小时前刷过 → 跳过
    assert not gate.status_fresh(_acc(5))               # 超 4 小时 → 刷新
    assert not gate.status_fresh(_acc(1, "captcha"))    # 异常账号不节流，留待冷却恢复
    assert not gate.status_fresh(_acc(1, "failed"))
    blank = {"userName": "u1", "health": {"status": "ok", "checkedAt": ""}}
    assert not gate.status_fresh(blank)                 # 没记录最后刷新时间 → 刷新
    assert not gate.status_fresh({"userName": "u1"})    # 没测过 → 刷新


def test_risk_keyword_variants(gate_env):
    gate, _ = gate_env
    for i, msg in enumerate(("触发风控系统", "登录太快", "账号已锁定")):
        c = FakeClient(f"u4-{i}", [(False, "会话已失效")], [("failed", msg)])
        assert gate.ensure_logged_in(c)[0] == "risk", msg
