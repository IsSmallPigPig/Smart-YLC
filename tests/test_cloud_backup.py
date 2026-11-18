import json

import pytest

from app.services import cloud_backup as cb


CFG = {"accounts": [{"userName": "demo01", "remark": "高一1班"}], "rosters": {"2026秋": "/tmp/r.xlsx"}}


def test_code_alphabet_and_length():
    for _ in range(50):
        code = cb.generate_code()
        assert len(code) == 6
        assert set(code) <= set(cb.ALPHABET)
        assert not (set(code) & set("01IO"))


def test_normalize_code_rejects_ambiguous_and_short():
    with pytest.raises(ValueError):
        cb.normalize_code("ABC12")
    with pytest.raises(ValueError):
        cb.normalize_code("ABC1O9")
    assert cb.normalize_code(" abc234 ") == "ABC234"


def test_normalize_password_requires_four_digits():
    assert cb.normalize_password(" 1234 ") == "1234"
    for bad in ("", "123", "12345", "abcd", "12a4"):
        with pytest.raises(ValueError, match="4 位数字"):
            cb.normalize_password(bad)


def test_roundtrip_restores_config():
    code = cb.generate_code()
    envelope = cb.encrypt_config(CFG, code, "0417")
    assert set(envelope) == {"v", "salt", "nonce", "ciphertext"}
    assert envelope["v"] == 2
    assert json.loads(cb.decrypt(envelope, code, "0417")) == CFG


def test_envelope_is_randomised():
    code = "ABC234"
    first = cb.encrypt_config(CFG, code, "1234")
    second = cb.encrypt_config(CFG, code, "1234")
    assert first["ciphertext"] != second["ciphertext"]
    assert first["salt"] != second["salt"]


def test_wrong_code_fails_cleanly():
    envelope = cb.encrypt_config(CFG, "ABC234", "1234")
    with pytest.raises(ValueError, match="提取码"):
        cb.decrypt(envelope, "ABC235", "1234")


def test_wrong_password_fails_cleanly():
    envelope = cb.encrypt_config(CFG, "ABC234", "1234")
    with pytest.raises(ValueError, match="密码"):
        cb.decrypt(envelope, "ABC234", "4321")


def test_v2_envelope_without_password_asks_for_one():
    envelope = cb.encrypt_config(CFG, "ABC234", "1234")
    with pytest.raises(ValueError, match="需要 4 位密码"):
        cb.decrypt(envelope, "ABC234")


def test_v1_envelope_still_decrypts_with_code_only():
    """1.0.9 的老备份（v=1、密钥只由提取码派生）不能作废。"""
    import base64
    import os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    secret = "ABC234"
    salt = os.urandom(cb.SALT_BYTES)
    nonce = os.urandom(cb.NONCE_BYTES)
    sealed = AESGCM(cb._derive(secret, salt)).encrypt(nonce, b"{}", None)
    envelope = {
        "v": 1,
        "salt": base64.b64encode(salt).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(sealed).decode(),
    }
    assert cb.decrypt(envelope, secret, "") == b"{}"


def test_tampered_ciphertext_fails():
    envelope = cb.encrypt_config(CFG, "ABC234", "1234")
    raw = bytearray(__import__("base64").b64decode(envelope["ciphertext"]))
    raw[0] ^= 0x01
    envelope["ciphertext"] = __import__("base64").b64encode(bytes(raw)).decode()
    with pytest.raises(ValueError):
        cb.decrypt(envelope, "ABC234", "1234")


def test_server_url_normalisation():
    assert cb.normalize_server("backup.example.com/") == "https://backup.example.com"
    assert cb.normalize_server(" http://127.0.0.1:8888 ") == "http://127.0.0.1:8888"
    with pytest.raises(ValueError):
        cb.normalize_server("")


def test_owner_key_shape():
    import hashlib

    key = cb.generate_owner_key()
    assert len(key) == 64
    assert all(ch in "0123456789abcdef" for ch in key)
    assert len({cb.generate_owner_key() for _ in range(20)}) == 20
    assert cb.owner_hash(key) == hashlib.sha256(key.encode()).hexdigest()


def test_site_choices_defaults_first_and_dedup():
    assert cb.site_choices([]) == [cb.DEFAULT_SERVER]
    choices = cb.site_choices(["https://mirror.example.com", cb.DEFAULT_SERVER,
                               "mirror.example.com", "bad url"])
    assert choices == [cb.DEFAULT_SERVER, "https://mirror.example.com"]


def test_add_site_appends_and_caps():
    assert cb.add_site([], "mirror.example.com") == ["https://mirror.example.com"]
    assert cb.add_site(["https://a.example"], cb.DEFAULT_SERVER) == ["https://a.example"]
    many = [f"https://s{i}.example" for i in range(cb.MAX_SERVERS)]
    out = cb.add_site(many, "https://new.example")
    assert out[-1] == "https://new.example"
    assert len(out) == cb.MAX_SERVERS and out[0] == "https://s1.example"


def test_valid_profile_repairs_and_rejects():
    mine = {"name": "", "server": "mirror.example.com", "code": "abc234",
            "ownerKey": "f" * 64}
    p = cb.valid_profile(mine)
    assert p["code"] == "ABC234"
    assert p["server"] == "https://mirror.example.com"
    assert p["name"] == "配置 ABC234"
    assert p["ownerKey"]

    theirs = cb.valid_profile({"server": cb.DEFAULT_SERVER, "code": "ABC234"})
    assert theirs["ownerKey"] == ""       # 加入他人档案：没有凭据
    assert cb.valid_profile({"server": cb.DEFAULT_SERVER, "code": "ABC1O9"}) is None
    assert cb.valid_profile("nonsense") is None
    # 伪造的短 owner key 视同没有：不能因为本地塞了字符串就拿到上传权
    assert cb.valid_profile({"server": cb.DEFAULT_SERVER, "code": "ABC234",
                             "ownerKey": "hack"})["ownerKey"] == ""


def test_load_profiles_skips_broken_entries():
    cfg = {"cloudProfiles": [
        {"server": cb.DEFAULT_SERVER, "code": "ABC234", "ownerKey": "a" * 64},
        {"server": cb.DEFAULT_SERVER, "code": "bad!"},
        None, "junk",
    ]}
    out = cb.load_profiles(cfg)
    assert len(out) == 1 and out[0]["code"] == "ABC234"


def test_upload_sends_owner_key_header(monkeypatch):
    calls = []

    class Resp:
        status_code = 200
        ok = True

        def json(self):
            return {"ok": True}

    def fake_put(url, params=None, json=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers or {}})
        return Resp()

    monkeypatch.setattr(cb.requests, "put", fake_put)
    cb.upload(cb.DEFAULT_SERVER, "ABC234",
              {"salt": "s", "nonce": "n", "ciphertext": "c"}, "a" * 64)
    assert calls[-1]["headers"] == {"X-Owner-Key": "a" * 64}
    cb.upload(cb.DEFAULT_SERVER, "ABC234", {"salt": "s", "nonce": "n", "ciphertext": "c"})
    assert calls[-1]["headers"] == {}


def test_http_status_maps_to_clear_messages(monkeypatch):
    class Resp:
        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self._body = body or {}
            self.ok = status_code < 400

        def json(self):
            if not self._body:
                raise ValueError("no json")
            return self._body

    monkeypatch.setattr(cb.requests, "put",
                        lambda *a, **k: Resp(403, {"error": "这个提取码的配置属于别人"}))
    with pytest.raises(RuntimeError, match="属于别人"):
        cb.upload(cb.DEFAULT_SERVER, "ABC234", {"salt": "s", "nonce": "n", "ciphertext": "c"})

    # 403 且服务端没给原因时，用本地兜底文案
    monkeypatch.setattr(cb.requests, "put", lambda *a, **k: Resp(403))
    with pytest.raises(RuntimeError, match="只能拉取"):
        cb.upload(cb.DEFAULT_SERVER, "ABC234", {"salt": "s", "nonce": "n", "ciphertext": "c"})

    monkeypatch.setattr(cb.requests, "get", lambda *a, **k: Resp(404))
    with pytest.raises(RuntimeError, match="没有找到"):
        cb.download(cb.DEFAULT_SERVER, "ABC234")


def test_probe_reports_exists(monkeypatch):
    class Resp:
        status_code = 200
        ok = True

        def json(self):
            return {"ok": True, "exists": True}

    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen.update(url=url, params=params)
        return Resp()

    monkeypatch.setattr(cb.requests, "get", fake_get)
    assert cb.probe("mirror.example.com", " abc234 ") is True
    assert seen["url"] == "https://mirror.example.com/api/backup"
    assert seen["params"] == {"id": "ABC234", "probe": "1"}


def test_legacy_single_server_still_offered():
    """1.0.9 的 cloudBackupServer 单地址设置，迁进站点列表后仍在下拉里。"""
    custom = ["https://old.example.com"]
    assert cb.site_choices(custom) == [cb.DEFAULT_SERVER, "https://old.example.com"]


def test_dialog_builds_profiles_and_gates_upload(qapp_session):
    """对话框能按档案状态建出来：自己的档案可上传，他人档案只读。"""
    from PyQt5.QtWidgets import QWidget

    from app.ui.cloud_backup_dialog import CloudBackupDialog

    host = QWidget()
    host.resize(900, 600)
    mine = {"name": "我的", "server": cb.DEFAULT_SERVER, "code": "ABC234",
            "ownerKey": "a" * 64}
    theirs = {"name": "年级组", "server": cb.DEFAULT_SERVER, "code": "DEF456",
              "ownerKey": ""}
    saved = {}
    dlg = CloudBackupDialog(
        host, get_config=lambda: CFG, on_restore=lambda cfg: None,
        profiles=[mine, theirs], active_id=f"{cb.DEFAULT_SERVER}|DEF456",
        on_change=lambda patch: saved.update(patch),
    )
    # 打开时选中他人档案：上传禁用、拉取可用
    assert dlg.upload_btn.isEnabledTo(dlg.widget) is False
    assert dlg.restore_btn.isEnabledTo(dlg.widget) is True
    assert "只能拉取" in dlg.status.text()
    # 切到自己的档案：上传解禁
    idx = dlg.profile_combo.findData(f"{cb.DEFAULT_SERVER}|ABC234")
    dlg.profile_combo.setCurrentIndex(idx)
    assert dlg.upload_btn.isEnabledTo(dlg.widget) is True
    # 状态落盘走 on_change（合并写由设置页负责）
    assert saved["cloudActiveProfile"] == f"{cb.DEFAULT_SERVER}|ABC234"
    assert [p["code"] for p in saved["cloudProfiles"]] == ["ABC234", "DEF456"]
    # 空档案列表：按钮全禁用，提示建档
    empty = CloudBackupDialog(host, get_config=lambda: CFG,
                              on_restore=lambda cfg: None, profiles=[])
    assert empty.upload_btn.isEnabledTo(empty.widget) is False
    assert empty.restore_btn.isEnabledTo(empty.widget) is False
    dlg.deleteLater(); empty.deleteLater(); host.deleteLater()
