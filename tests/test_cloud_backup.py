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


def test_roundtrip_restores_config():
    code = cb.generate_code()
    envelope = cb.encrypt_config(CFG, code)
    assert set(envelope) == {"v", "salt", "nonce", "ciphertext"}
    assert json.loads(cb.decrypt(envelope, code)) == CFG


def test_envelope_is_randomised():
    code = "ABC234"
    first = cb.encrypt_config(CFG, code)
    second = cb.encrypt_config(CFG, code)
    assert first["ciphertext"] != second["ciphertext"]
    assert first["salt"] != second["salt"]


def test_wrong_code_fails_cleanly():
    envelope = cb.encrypt_config(CFG, "ABC234")
    with pytest.raises(ValueError, match="提取码"):
        cb.decrypt(envelope, "ABC235")


def test_tampered_ciphertext_fails():
    envelope = cb.encrypt_config(CFG, "ABC234")
    raw = bytearray(__import__("base64").b64decode(envelope["ciphertext"]))
    raw[0] ^= 0x01
    envelope["ciphertext"] = __import__("base64").b64encode(bytes(raw)).decode()
    with pytest.raises(ValueError):
        cb.decrypt(envelope, "ABC234")


def test_server_url_normalisation():
    assert cb.normalize_server("backup.example.com/") == "https://backup.example.com"
    assert cb.normalize_server(" http://127.0.0.1:8888 ") == "http://127.0.0.1:8888"
    with pytest.raises(ValueError):
        cb.normalize_server("")
