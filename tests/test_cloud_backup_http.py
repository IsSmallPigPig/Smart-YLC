"""云端备份走真实 HTTP：用本地服务模拟 Netlify 函数 v2 的接口形状。

函数本身跑在 Netlify 上，但接口契约（GET/PUT `/api/backup?id=`、404 表示没有、
probe 只答存在与否、owner key 不符 403、错误体是 {ok,error}）可以在这里钉住——
客户端或服务端改坏了会立刻失败。服务端实现见 server 分支
netlify/functions/backup.mts，本文件的 handler 是它的 Python 镜像。
"""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services import cloud_backup as cb


def _owner_matches(stored: str | None, owner_key: str) -> bool:
    if not stored:
        return False
    return stored == hashlib.sha256(owner_key.encode()).hexdigest()


def _make_server(store: dict):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _code(self):
            from urllib.parse import parse_qs, urlparse

            query = parse_qs(urlparse(self.path).query)
            return (query.get("id") or [""])[0].upper()

        def do_GET(self):
            from urllib.parse import parse_qs, urlparse

            code = self._code()
            query = parse_qs(urlparse(self.path).query)
            record = store.get(code)
            if query.get("probe") == ["1"]:
                self._send(200, {"ok": True, "exists": record is not None})
                return
            if not record:
                self._send(404, {"ok": False, "error": "没有找到这份备份"})
                return
            envelope = {k: v for k, v in record.items() if k != "owner"}
            self._send(200, envelope)

        def do_PUT(self):
            code = self._code()
            length = int(self.headers.get("content-length") or 0)
            envelope = json.loads(self.rfile.read(length) or b"{}")
            for key in ("salt", "nonce", "ciphertext"):
                if key not in envelope:
                    self._send(400, {"ok": False, "error": f"缺少字段 {key}"})
                    return
            owner_key = self.headers.get("x-owner-key") or ""
            if len(owner_key) != 64 or any(c not in "0123456789abcdef" for c in owner_key):
                self._send(403, {"ok": False, "error": "缺少所有权凭据，无法上传"})
                return
            prior = store.get(code)
            if prior and not _owner_matches(prior.get("owner"), owner_key):
                self._send(403, {"ok": False, "error": "这个提取码的配置属于别人"})
                return
            store[code] = {**envelope,
                           "owner": hashlib.sha256(owner_key.encode()).hexdigest()}
            self._send(200, {"ok": True, "code": code})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture()
def endpoint():
    store = {}
    server, url = _make_server(store)
    yield url, store
    server.shutdown()


CFG = {"accounts": [{"userName": "demo01", "remark": "高一1班"}], "rosters": {}}


def test_upload_then_download_restores(endpoint):
    url, _ = endpoint
    code, pwd = cb.generate_code(), "0417"
    key = cb.generate_owner_key()
    cb.upload(url, code, cb.encrypt_config(CFG, code, pwd), key)
    restored = cb.decrypt_config(cb.download(url, code), code, pwd)
    assert restored == CFG


def test_missing_code_reports_clearly(endpoint):
    url, _ = endpoint
    with pytest.raises(RuntimeError, match="没有找到"):
        cb.download(url, "ABC234")


def test_wrong_password_after_download_fails(endpoint):
    url, _ = endpoint
    key = cb.generate_owner_key()
    cb.upload(url, "ABC234", cb.encrypt_config(CFG, "ABC234", "1234"), key)
    envelope = cb.download(url, "ABC234")
    with pytest.raises(ValueError, match="密码"):
        cb.decrypt_config(envelope, "ABC234", "4321")


def test_owner_can_reupload(endpoint):
    url, _ = endpoint
    key = cb.generate_owner_key()
    cb.upload(url, "ABC234", cb.encrypt_config(CFG, "ABC234", "1234"), key)
    cb.upload(url, "ABC234", cb.encrypt_config({"accounts": []}, "ABC234", "1234"), key)


def test_stranger_cannot_overwrite(endpoint):
    """他人拿着同一个提取码也无法覆盖——这是「只有所属人能上传」的服务端半边。"""
    url, _ = endpoint
    mine = cb.generate_owner_key()
    theirs = cb.generate_owner_key()
    cb.upload(url, "ABC234", cb.encrypt_config(CFG, "ABC234", "1234"), mine)
    with pytest.raises(RuntimeError, match="属于别人"):
        cb.upload(url, "ABC234", cb.encrypt_config({"accounts": []}, "ABC234", "9999"), theirs)


def test_upload_without_owner_key_rejected(endpoint):
    url, _ = endpoint
    with pytest.raises(RuntimeError, match="所有权"):
        cb.upload(url, "ABC234", cb.encrypt_config(CFG, "ABC234", "1234"))


def test_probe_sees_existing_and_free_codes(endpoint):
    url, _ = endpoint
    assert cb.probe(url, "ABC234") is False
    cb.upload(url, "ABC234", cb.encrypt_config(CFG, "ABC234", "1234"),
              cb.generate_owner_key())
    assert cb.probe(url, "ABC234") is True


def test_get_never_leaks_owner_hash(endpoint):
    url, store = endpoint
    key = cb.generate_owner_key()
    cb.upload(url, "ABC234", cb.encrypt_config(CFG, "ABC234", "1234"), key)
    assert "owner" in store["ABC234"]                 # 服务端确实存了
    body = cb.download(url, "ABC234")                 # 但拉回来没有
    assert "owner" not in body
    assert hashlib.sha256(key.encode()).hexdigest() not in json.dumps(body)


def test_plaintext_never_reaches_server(endpoint):
    """服务端拿到的字节里不能出现明文内容——这是整套加密的意义所在。"""
    url, _ = endpoint
    code, pwd = "ABC234", "2468"
    envelope = cb.encrypt_config({"accounts": [{"userName": "secret-value"}]}, code, pwd)
    assert "secret-value" not in json.dumps(envelope)
    cb.upload(url, code, envelope, cb.generate_owner_key())
    assert "secret-value" not in json.dumps(cb.download(url, code))


def test_v1_record_stays_readable_but_locked(endpoint):
    """v1 老记录没有 owner：还能解密，但谁也覆盖不了。"""
    import base64
    import os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    url, store = endpoint
    salt = os.urandom(cb.SALT_BYTES)
    nonce = os.urandom(cb.NONCE_BYTES)
    sealed = AESGCM(cb._derive("ABC234", salt)).encrypt(nonce, b"{}", None)
    store["ABC234"] = {                       # 旧信封形状：没有 owner 字段
        "v": 1,
        "salt": base64.b64encode(salt).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(sealed).decode(),
    }
    assert cb.decrypt(cb.download(url, "ABC234"), "ABC234") == b"{}"
    with pytest.raises(RuntimeError, match="属于别人"):
        cb.upload(url, "ABC234", cb.encrypt_config(CFG, "ABC234", "1234"),
                  cb.generate_owner_key())


def test_non_json_server_response_is_reported(endpoint):
    class _NotJson(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            raw = b"<html>nope</html>"
            self.send_response(200)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = HTTPServer(("127.0.0.1", 0), _NotJson)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(RuntimeError, match="备份内容"):
            cb.download(f"http://127.0.0.1:{server.server_address[1]}", "ABC234")
    finally:
        server.shutdown()
