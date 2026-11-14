"""网页端登录态：浏览器只保留一条有效 SESSION，自动链不再抢登录。"""

import time
from types import SimpleNamespace

from app.domain.login_protocol import build_login_url
from app.services import login_gate, store



def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))


def test_browser_session_window(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    login_gate.mark_browser_session("u1", "Arc")
    assert "u1" in login_gate.active_browser_sessions()

    def _age(cfg):
        cfg["browserSessions"]["u1"]["at"] = (
            time.time() - login_gate.BROWSER_SESSION_HOURS * 3600 - 10)

    store.update(_age)
    assert "u1" not in login_gate.active_browser_sessions()

    login_gate.mark_browser_session("u2", "Safari")
    login_gate.clear_browser_sessions()
    assert store.load()["browserSessions"] == {}


def test_data_sync_never_logs_in(monkeypatch, tmp_path):
    """会话失效时，30 秒数据链跳过账号而不登录；手动入口仍可登录。"""
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(login_gate, "restore_session", lambda c: None)
    calls = []

    class Client:
        user_name = "u1"
        password = "p"

        def session_account(self):
            return SimpleNamespace(ok=False, message="会话已失效")

        def login(self):
            calls.append("login")
            return SimpleNamespace(ok=True, message="登录成功", data={"kind": "ok"})

    kind, msg = login_gate.ensure_logged_in(Client(), allow_login=False)
    assert kind == "session" and "本轮只取数" in msg
    assert not calls
    assert "session" in login_gate.SKIP_KINDS


def test_login_url_is_the_web_login_request():
    """网页端登录就是这一个 GET：密码只以双重 md5 出现，浏览器打开它即可换会话。"""
    url = build_login_url("ssfz12407", "abc12345")
    assert url.startswith("https://tuanapi.12355.net/login/adminLogin?")
    assert "userName=ssfz12407" in url
    assert "password=" in url and "abc12345" not in url
