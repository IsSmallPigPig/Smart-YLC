"""验证码登录弹窗：取图、提交、失败换图、成功后写回账号。"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from requests.cookies import RequestsCookieJar

from app.services.api import ApiResult
from app.ui.captcha_dialog import CaptchaLoginDialog
from app.ui.settings_account_mixin import SettingsAccountMixin

def _png() -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (120, 40), "white").save(buf, "PNG")
    return buf.getvalue()


PNG = _png()


class FakeClient:
    def __init__(self, result: ApiResult):
        self.user_name = "u1"
        self.password = "old"
        self.http = SimpleNamespace(cookies=RequestsCookieJar())
        self.result = result
        self.fetches = 0
        self.codes: list[str] = []

    def fetch_captcha(self):
        self.fetches += 1
        return PNG

    def login(self, login_valid_code: str = ""):
        self.codes.append(login_valid_code)
        return self.result


def _dialog(client, successes):
    from PyQt5.QtWidgets import QWidget

    parent = QWidget()
    parent.resize(800, 600)
    dlg = CaptchaLoginDialog(client, "u1（测试班）", "需要输入图片验证码", parent,
                             on_success=lambda pwd, c: successes.append((pwd, c)))
    dlg._test_parent = parent      # 弹窗按父窗口尺寸铺满遮罩，父对象必须活着
    dlg.show()
    QTest.qWait(250)
    return dlg


def test_captcha_dialog_loads_image_and_logs_in():
    client = FakeClient(ApiResult(True, "登录成功", {"kind": "ok"}))
    done = []
    dlg = _dialog(client, done)
    try:
        assert client.fetches == 1
        assert dlg.image.pixmap() is not None and not dlg.image.pixmap().isNull()

        dlg.password_edit.setText("new-pass")
        dlg.captcha_edit.setText("AB12")
        dlg.validate()
        QTest.qWait(300)

        assert client.password == "new-pass"
        assert client.codes == ["AB12"]
        assert done and done[0][0] == "new-pass" and done[0][1] is client
    finally:
        try:
            dlg.reject()
        except RuntimeError:
            pass
        QTest.qWait(50)


def test_wrong_captcha_refreshes_image_and_keeps_dialog_open():
    client = FakeClient(ApiResult(False, "图片验证码错误！", {"kind": "captcha"}))
    done = []
    dlg = _dialog(client, done)
    try:
        dlg.password_edit.setText("new-pass")
        dlg.captcha_edit.setText("WRONG")
        dlg.validate()
        QTest.qWait(300)

        assert not done
        assert client.fetches == 2          # 自动换了一张
        assert dlg.captcha_edit.text() == ""
        assert "验证码" in dlg.error.text()
    finally:
        try:
            dlg.reject()
        except RuntimeError:
            pass
        QTest.qWait(50)


@pytest.mark.parametrize("status,expected", [
    ("captcha", "captcha"),
    ("failed", "captcha"),
    ("cooldown", "captcha"),
    ("ok", "web"),
    ("unknown", "web"),
])
def test_double_click_routes_by_health_status(status, expected):
    calls = []
    page = SimpleNamespace(
        accounts=[{"userName": "u1", "health": {"status": status}}],
        _user_at_row=lambda row: "u1",
        _open_captcha_for=lambda user: calls.append(("captcha", user)),
        open_web_for=lambda user: calls.append(("web", user)),
    )
    SettingsAccountMixin._on_cell_double_clicked(page, 0, 1)
    assert calls == [(expected, "u1")]


def test_manual_login_writes_password_health_and_clears_gate(monkeypatch):
    from app.services import login_gate, store

    store.update(lambda cfg: cfg.update({
        "accounts": [{"userName": "u1", "password": "old", "remark": "测试班"}],
        "loginGate": {"u1": {"captchaUntil": 9e9}},
    }))
    monkeypatch.setattr("app.ui.settings_account_mixin.InfoBar.success",
                        lambda *a, **k: None)
    page = SimpleNamespace(
        accounts=[{"userName": "u1", "password": "old", "remark": "测试班"}],
        _client=object(),
        _save_config=lambda: None,
        _refresh_table=lambda: None,
    )
    client = FakeClient(ApiResult(True, "登录成功", {"kind": "ok"}))
    client.http.cookies.set("SESSION", "abc", domain="tuanapi.12355.net", path="/")

    SettingsAccountMixin._captcha_login_done(page, "u1", "new-pass", client)

    acc = page.accounts[0]
    assert acc["password"] == "new-pass"
    assert acc["health"]["status"] == "ok"
    cfg = store.load()
    assert cfg["loginGate"]["u1"]["captchaUntil"] == 0
    assert cfg["sessionCookies"]["u1"][0]["name"] == "SESSION"


def test_password_is_visible_in_captcha_dialog():
    """密码不遮：这行就是要核对的那个密码，遮起来等于让用户盲改。"""
    from PyQt5.QtWidgets import QLineEdit

    client = FakeClient(ApiResult(True, "登录成功", {"kind": "ok"}))
    dlg = _dialog(client, [])
    try:
        assert dlg.password_edit.text() == "old"
        assert dlg.password_edit.echoMode() == QLineEdit.EchoMode.Normal
    finally:
        dlg.reject()
        QTest.qWait(50)


def test_clicking_captcha_image_refreshes_it():
    """换图靠点验证码图片本身，不再单独摆一个「换一张」按钮。"""
    client = FakeClient(ApiResult(True, "登录成功", {"kind": "ok"}))
    dlg = _dialog(client, [])
    try:
        assert not hasattr(dlg, "refresh_btn")
        before = client.fetches
        QTest.mouseClick(dlg.image, Qt.MouseButton.LeftButton)
        QTest.qWait(300)
        assert client.fetches == before + 1
    finally:
        dlg.reject()
        QTest.qWait(50)
