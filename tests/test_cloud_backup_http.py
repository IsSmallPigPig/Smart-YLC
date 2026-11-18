"""云端备份走真实 HTTP：用本地服务模拟 Netlify 函数的接口形状。

函数本身跑在 Netlify 上，但接口契约（GET/PUT `/api/backup?id=`、404 表示没有、
错误体是 {ok,error}）可以在这里钉住——客户端改坏了会立刻失败。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services import cloud_backup as cb


class _Store(dict):
    pass


def _make_server(store: _Store):
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

        def do_GET(self):
            code = self.path.split("id=")[-1].upper()
            if code not in store:
                self._send(404, {"ok": False, "error": "没有找到这份备份"})
                return
            self._send(200, store[code])

        def do_PUT(self):
            code = self.path.split("id=")[-1].upper()
            length = int(self.headers.get("content-length") or 0)
            envelope = json.loads(self.rfile.read(length) or b"{}")
            for key in ("salt", "nonce", "ciphertext"):
                if key not in envelope:
                    self._send(400, {"ok": False, "error": f"缺少字段 {key}"})
                    return
            store[code] = envelope
            self._send(200, {"ok": True, "code": code})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture()
def endpoint():
    server, url = _make_server(_Store())
    yield url
    server.shutdown()


CFG = {"accounts": [{"userName": "demo01", "remark": "高一1班"}], "rosters": {}}


def test_upload_then_download_restores(endpoint):
    code = cb.generate_code()
    cb.upload(endpoint, code, cb.encrypt_config(CFG, code))
    restored = cb.decrypt_config(cb.download(endpoint, code), code)
    assert restored == CFG


def test_missing_code_reports_clearly(endpoint):
    with pytest.raises(RuntimeError, match="没有找到"):
        cb.download(endpoint, "ABC234")


def test_wrong_code_after_download_fails(endpoint):
    cb.upload(endpoint, "ABC234", cb.encrypt_config(CFG, "ABC234"))
    envelope = cb.download(endpoint, "ABC234")
    with pytest.raises(ValueError, match="提取码"):
        cb.decrypt_config(envelope, "ABC235")


def test_plaintext_never_reaches_server(endpoint):
    """服务端拿到的字节里不能出现明文内容——这是整套加密的意义所在。"""
    code = "ABC234"
    envelope = cb.encrypt_config({"accounts": [{"userName": "secret-value"}]}, code)
    blob = json.dumps(envelope)
    assert "secret-value" not in blob
    cb.upload(endpoint, code, envelope)
    assert "secret-value" not in json.dumps(cb.download(endpoint, code))


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
