"""网页端登录态：浏览器只保留一条有效 SESSION，自动链不再抢登录。"""

import time
from types import SimpleNamespace

from app.services import login_gate, store, webopen

HOME = "https://tuan.12355.net/bg/index_main.html"


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


def test_scripts_clear_domain_cookie_and_check_page():
    url = "https://tuanapi.12355.net/login/adminLogin?userName=u1"
    arc = webopen._arc_scripts(url)
    safari = webopen._safari_scripts(url)
    assert len(arc) == len(safari) == 3
    for scripts in (arc, safari):
        prepare, sign_in, verify = scripts
        assert "SESSION=" in prepare and ".12355.net" in prepare and "max-age=0" in prepare
        assert "userName=u1" in sign_in and "index_main" in sign_in
        assert "登录状态已失效" in verify
    assert "execute window 1's active tab javascript" in arc[0]
    assert "do JavaScript" in safari[0]


def test_clean():
    assert webopen._clean('"cleared"') == "cleared"
    assert webopen._clean(f"  {HOME}  ") == HOME
    assert webopen._clean("") == ""


def _mac(monkeypatch):
    monkeypatch.setattr(webopen, "sys", SimpleNamespace(platform="darwin"))


def _stub_scripts(monkeypatch, state):
    """按三段脚本的内容返回对应的模拟结果。"""

    def fake(script, timeout=20):
        if "max-age=0" in script:
            return True, f'"{state["cleared"]}"'
        if "javascript" in script or "JavaScript" in script:
            return True, f'"{state["page"]}"'
        return True, state["url"]

    monkeypatch.setattr(webopen, "_run_applescript", fake)


def test_open_logged_in_marks_session_only_on_valid_page(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    _mac(monkeypatch)
    client = SimpleNamespace(user_name="u1", password="p")
    state = {"cleared": "cleared", "url": HOME, "page": "ok"}
    _stub_scripts(monkeypatch, state)

    ok, msg = webopen.open_logged_in(client, "Arc")
    assert ok and "后台自动刷新已让路" in msg
    assert "u1" in login_gate.active_browser_sessions()

    login_gate.clear_browser_sessions()
    state["page"] = "expired"
    ok, msg = webopen.open_logged_in(client, "Arc")
    assert not ok and "登录状态已失效" in msg
    assert "u1" not in login_gate.active_browser_sessions()

    state["page"] = "ok"
    state["cleared"] = "blocked"
    ok, msg = webopen.open_logged_in(client, "Arc")
    assert not ok and "域级" in msg
    assert "u1" not in login_gate.active_browser_sessions()


def test_open_logged_in_reports_script_failure(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    _mac(monkeypatch)
    monkeypatch.setattr(webopen, "_run_applescript",
                        lambda s, timeout=20: (False, "Not allowed to send Apple events"))
    client = SimpleNamespace(user_name="u1", password="p")
    ok, msg = webopen.open_logged_in(client, "Arc")
    assert not ok and "请在系统设置允许控制浏览器" in msg
    assert store.load().get("browserSessions", {}) == {}
