"""账号网页：标签跟随账号刷新、登录看门狗的纯逻辑部分。

BrowserWindow 依赖 QWebEngineView（需要真实图形环境），这里只把它那几个
方法绑到一个假对象上验证行为，不创建窗口。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from app.ui.browser_page import BrowserWindow


class _Profile:
    def __init__(self):
        self.dropped = False

    def deleteLater(self):
        self.dropped = True


class _Tab:
    def __init__(self, title: str):
        self.title = title

    def set_title(self, title: str):
        self.title = title


class _FakeBrowser:
    """只保留 refresh_accounts / _login_watchdog 用到的那几个属性。"""

    refresh_accounts = BrowserWindow.refresh_accounts
    _cred_sig = BrowserWindow._cred_sig
    _login_watchdog = BrowserWindow._login_watchdog

    def __init__(self, accounts: list[dict], open_users: list[str]):
        self.accounts = accounts
        self._views = {u: object() for u in open_users}
        self._labels = {u: u for u in open_users}
        self._tabs = {u: _Tab(u) for u in open_users}
        self._creds = {}
        self._state = {u: "ready" for u in open_users}
        self._login_token = {u: 1 for u in open_users}
        self._auto_logins = {u: 2 for u in open_users}
        self._profiles = {u: _Profile() for u in open_users}
        self.relogged: list[str] = []
        self.closed: list[str] = []

    def _accounts(self) -> list[dict]:
        return self.accounts

    def _close(self, user: str):
        self.closed.append(user)
        self._views.pop(user, None)
        self._tabs.pop(user, None)

    def _login(self, user: str):
        self.relogged.append(user)

    def _fail(self, user: str, message: str):
        self._state[user] = "ready"
        self.failed = (user, message)

    def _sync_nav(self):
        pass


def _acc(user: str, remark: str, **extra) -> dict:
    acc = {"userName": user, "remark": remark, "password": "p", "loginValidCode": ""}
    acc.update(extra)
    return acc


def test_refresh_accounts_renames_tab_without_relogin():
    """只改备注：标签名跟着改，已经登录的会话不动（不重新登录）。"""
    b = _FakeBrowser([_acc("ssfz1", "高一1班")], ["ssfz1"])
    b._creds["ssfz1"] = ("p", "")
    b.accounts = [_acc("ssfz1", "高三9班")]
    b.refresh_accounts()
    assert b._tabs["ssfz1"].title == "高三9班"
    assert b._labels["ssfz1"] == "高三9班"
    assert b.relogged == []


@pytest.mark.parametrize("change,expected", [
    ({"password": "new-pass"}, ("new-pass", "")),
    ({"loginValidCode": "8241"}, ("p", "8241")),
])
def test_refresh_accounts_relogins_when_credentials_change(change, expected):
    """密码或登录验证码变了，就用新凭据重登，页面里不能留旧会话。"""
    b = _FakeBrowser([_acc("ssfz1", "高一1班")], ["ssfz1"])
    b._creds["ssfz1"] = ("p", "")
    b.accounts = [_acc("ssfz1", "高一1班", **change)]
    b.refresh_accounts()
    assert b.relogged == ["ssfz1"]
    assert b._creds["ssfz1"] == expected


def test_refresh_accounts_closes_tab_of_deleted_account():
    b = _FakeBrowser([_acc("ssfz1", "高一1班"), _acc("ssfz2", "高一2班")], ["ssfz1", "ssfz2"])
    b._creds = {"ssfz1": ("p", ""), "ssfz2": ("p", "")}
    b.accounts = [_acc("ssfz1", "高一1班")]
    stale = b._profiles["ssfz2"]
    b.refresh_accounts()
    assert b.closed == ["ssfz2"]
    assert list(b._views) == ["ssfz1"]
    assert stale.dropped and "ssfz2" not in b._profiles   # 账号没了就不留存储分区
    assert b.relogged == []


def test_refresh_accounts_keeps_untouched_tabs_quiet():
    """配置没变时不该有任何动作（避免每轮同步都把标签重登一遍）。"""
    b = _FakeBrowser([_acc("ssfz1", "高一1班")], ["ssfz1"])
    b._creds["ssfz1"] = ("p", "")
    b.refresh_accounts()
    assert b.relogged == [] and b.closed == []


def test_login_watchdog_reveals_page_only_for_current_attempt():
    """到点仍在登录态才收等待页；重登过（代号变了）或已就绪都不动。"""
    b = _FakeBrowser([_acc("ssfz1", "高一1班")], ["ssfz1"])
    b._state["ssfz1"] = "login"
    b.failed = None
    b._login_watchdog("ssfz1", 1)
    assert b._state["ssfz1"] == "ready"
    assert b.failed[0] == "ssfz1"

    b._state["ssfz1"] = "login"
    b.failed = None
    b._login_watchdog("ssfz1", 0)          # 旧代号
    assert b.failed is None

    b._state["ssfz1"] = "ready"
    b._login_watchdog("ssfz1", 1)
    assert b.failed is None
