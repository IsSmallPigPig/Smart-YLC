"""同步系统数据与刷新登录状态是两条独立链，各自的时间规则互不拦截。

同步系统数据每 30 秒一轮、每轮都取数（成员缓存、站内信）；
刷新登录状态按每个账号的最后刷新时间做 4 小时节流。
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services import login_gate, store


def _cache(hours_ago: float, key: str) -> dict:
    """构造一份刚拉过的数据缓存，旧版会据此整账号跳过本轮同步。"""
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {key: {"u1": {"fetchedAt": ts, "members": [], "messages": []}}}


def _acc(status: str = "ok", hours_ago: float = 1) -> dict:
    ts = (datetime.now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    return {"userName": "u1", "password": "p",
            "health": {"status": status, "checkedAt": ts}}


def test_member_sync_takes_data_even_when_cache_is_fresh(monkeypatch, tmp_path):
    from app.ui import org_manage_page as mod

    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save(_cache(1, "memberCache"))
    calls = []

    class FakeClient:
        def __init__(self, user, pwd):
            calls.append("client")

        def get_all_members(self):
            return [], None

    monkeypatch.setattr(mod, "SiteClient", FakeClient)
    monkeypatch.setattr(login_gate, "ensure_logged_in", lambda c, **kw: ("ok", ""))
    monkeypatch.setattr(store, "update", lambda fn: fn({"memberCache": {}}))

    result = mod.MemberSyncWorker._fetch_one(SimpleNamespace(sequential=True), _acc())
    assert result[3] == "ok", result  # 不再返回 "fresh" 跳过
    assert calls == ["client"]


def test_message_sync_takes_data_even_when_cache_is_fresh(monkeypatch, tmp_path):
    from app.ui import messages_page as mod

    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save(_cache(1, "messagesCache"))
    calls = []

    class FakeClient:
        def __init__(self, user, pwd):
            calls.append("client")

    monkeypatch.setattr(mod, "SiteClient", FakeClient)
    monkeypatch.setattr(mod, "fetch_all_messages", lambda c, u: [])
    monkeypatch.setattr(login_gate, "ensure_logged_in", lambda c, **kw: ("ok", ""))
    monkeypatch.setattr(store, "update", lambda fn: fn({"messagesCache": {}}))

    # force=False 正是旧版会走 4 小时新鲜跳过的路径
    stub = SimpleNamespace(force=False, _new=[])
    result = mod.MessageFetchWorker._fetch_one(stub, _acc())
    assert result[2] == "ok", result
    assert calls == ["client"]


def _health_stub(accounts):
    return SimpleNamespace(
        accounts=accounts, _worker=None, health_btn=MagicMock(), refresh_btn=MagicMock(),
        _on_account_checked=MagicMock(), _flush_health_results=MagicMock(),
        _show_health_progress=MagicMock(), _on_health_finished=MagicMock(),
        _on_health_buttons_back=MagicMock(),
    )


def test_auto_health_check_only_refreshes_due_accounts(monkeypatch):
    from app.ui import settings_page as mod

    made = []

    class FakeWorker:
        def __init__(self, accounts, parent=None):
            self.accounts = accounts
            self.account_checked = MagicMock()
            self.finished = MagicMock()
            made.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(mod, "HealthWorker", FakeWorker)
    fresh = _acc("ok", 1) | {"userName": "a"}
    due = _acc("ok", 5) | {"userName": "b"}
    broken = _acc("captcha", 1) | {"userName": "c"}
    accounts = [fresh, due, broken]

    stub = _health_stub(accounts)
    mod.SettingsPage.start_health_check(stub)
    assert [a["userName"] for a in made[0].accounts] == ["b", "c"]  # 异常账号要留着恢复

    made.clear()
    mod.SettingsPage.start_health_check(_health_stub([fresh]))
    assert not made  # 全在 4 小时内，本轮不发请求

    mod.SettingsPage.start_health_check(_health_stub(accounts), force=True)
    assert [a["userName"] for a in made[0].accounts] == ["a", "b", "c"]  # 手动全量


def test_background_chain_only_uses_saved_session(monkeypatch):
    """30 秒数据链只取数不登录，手动入口仍可登录。"""
    from app.ui import messages_page as messages
    from app.ui import org_manage_page as members

    seen = []

    def fake_ensure(client, allow_login=True):
        seen.append(allow_login)
        return "session", "会话已失效，本轮只取数不登录；登录状态由测活刷新"

    monkeypatch.setattr(login_gate, "ensure_logged_in", fake_ensure)
    monkeypatch.setattr(members, "SiteClient", lambda u, p: object())
    monkeypatch.setattr(messages, "SiteClient", lambda u, p: object())

    r1 = members.MemberSyncWorker._fetch_one(SimpleNamespace(sequential=True), _acc())
    r2 = messages.MessageFetchWorker._fetch_one(
        SimpleNamespace(force=True, FAST_SKIP_HEALTH=set(), _new=[]), _acc())
    assert r1[3] == "session" and r2[2] == "session"
    assert seen == [False, False]

    members.MemberSyncWorker._fetch_one(SimpleNamespace(sequential=False), _acc())
    messages.MessageFetchWorker._fetch_one(
        SimpleNamespace(force=False, FAST_SKIP_HEALTH=set(), _new=[]), _acc())
    assert seen[-2:] == [True, True]


def test_auto_health_check_skips_browser_session(monkeypatch, tmp_path):
    """网页端使用中的账号不参与自动测活，手动全量测活收回占用。"""
    import time

    from app.ui import settings_page as mod

    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save({"browserSessions": {"b": {"browser": "Arc", "at": time.time()}}})
    made = []

    class FakeWorker:
        def __init__(self, accounts, parent=None):
            self.accounts = accounts
            self.account_checked = MagicMock()
            self.finished = MagicMock()
            made.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(mod, "HealthWorker", FakeWorker)
    busy = _acc("ok", 5) | {"userName": "b"}
    other = _acc("ok", 5) | {"userName": "d"}

    mod.SettingsPage.start_health_check(_health_stub([busy, other]))
    assert [a["userName"] for a in made[0].accounts] == ["d"]

    made.clear()
    mod.SettingsPage.start_health_check(_health_stub([busy, other]), force=True)
    assert [a["userName"] for a in made[0].accounts] == ["b", "d"]
    assert store.load().get("browserSessions") == {}


def test_refresh_selected_only_clears_its_own_browser_sessions(monkeypatch, tmp_path):
    """表格刷新按钮只刷新勾选账号，也只收回这些账号的网页占用。"""
    import time

    from app.ui import settings_page as mod

    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save({"browserSessions": {
        "b": {"browser": "Arc", "at": time.time()},
        "d": {"browser": "Safari", "at": time.time()},
    }})
    made = []

    class FakeWorker:
        def __init__(self, accounts, parent=None):
            self.accounts = accounts
            self.account_checked = MagicMock()
            self.finished = MagicMock()
            made.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(mod, "HealthWorker", FakeWorker)
    busy = _acc("ok", 5) | {"userName": "b"}
    other = _acc("ok", 5) | {"userName": "d"}

    mod.SettingsPage.start_health_check(_health_stub([busy, other]), force=True, users=["b"])
    assert [a["userName"] for a in made[0].accounts] == ["b"]  # 手动刷新不受 4 小时节流
    assert set(store.load().get("browserSessions")) == {"d"}  # 另一个网页会话保留
