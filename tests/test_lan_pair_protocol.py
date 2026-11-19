"""App 配对协议 v1 的端到端测试：配对 → 清单 → 上传 → 进度 → 结束。

手机端还没开发，这里的测试就是协议的可执行契约：app 端按 docs/app-pair-protocol.md
实现，能通过这些接口语义即视为对齐。落盘一律指向 tmp_path。
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request

import pytest

from app.services import phone_sync


@pytest.fixture()
def share(tmp_path, monkeypatch):
    monkeypatch.setattr(phone_sync, "sync_dir", lambda today=None: str(tmp_path))
    monkeypatch.setattr(phone_sync, "register_source_dir", lambda path, add_dir_fn=None: None)
    from app.services.lan_share import LanShare

    s = LanShare(port=0)
    url = s.start()
    yield s, url.split("/?")[0]
    s.stop()


def post(base: str, path: str, payload: dict | None = None,
         query: dict | None = None, body: bytes | None = None) -> tuple[int, dict]:
    url = base + path
    if query:
        from urllib.parse import urlencode
        url += "?" + urlencode(query)
    if body is not None:
        data, headers = body, {"Content-Type": "application/pdf"}
    else:
        data = json.dumps(payload or {}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_pair_rejects_wrong_pin(share):
    _s, base = share
    code, data = post(base, "/api/pair", {"pin": "WRONG", "device": "测试手机"})
    assert code == 403 and data["ok"] is False


def test_full_pair_flow(share):
    s, base = share
    # 1. 配对：pin 换成 token
    code, data = post(base, "/api/pair", {"pin": s.pin, "device": "测试手机"})
    assert code == 200 and data["ok"] is True
    token = data["token"]
    assert token and s.session(token) is not None

    # 2. 清单：此时同步目录为空，没有已存在的摘要
    code, data = post(base, "/api/manifest", query={"token": token})
    assert code == 200 and data["digests"] == []

    # 3. 上传一份带 sha256 的 PDF
    pdf = b"%PDF-1.4\n" + b"x" * 2048
    digest = hashlib.sha256(pdf).hexdigest()
    code, data = post(base, "/api/upload",
                      query={"token": token, "name": "张三+13800138000+入团志愿书.pdf",
                             "sha256": digest}, body=pdf)
    assert code == 200 and data["ok"] is True
    assert data["status"] in ("ok", "need_rename", "duplicate")

    # 4. 进度：手机端能查到已收文件，用于对账
    code, data = post(base, "/api/status", query={"token": token})
    assert code == 200 and "张三+13800138000+入团志愿书.pdf" in data["received"]
    assert digest in s.session(token).uploaded

    # 5. 结束：会话被清掉，再查变 401
    code, data = post(base, "/api/done", query={"token": token})
    assert code == 200 and data["received"] == 1
    assert s.session(token) is None
    code, data = post(base, "/api/status", query={"token": token})
    assert code == 401


def test_manifest_skips_already_synced_files(share, tmp_path, monkeypatch):
    """清单返回已落盘档案的摘要，手机端据此跳过重复文件。"""
    s, base = share
    # 把同步根目录指到 tmp_path，写一份"上周已传过"的 PDF
    monkeypatch.setattr(phone_sync, "SYNC_FOLDER", "sync-root")
    monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path) if p == "~" else p)
    day_dir = tmp_path / "sync-root" / "2000-01-01"
    day_dir.mkdir(parents=True)
    old = day_dir / "old.pdf"
    old.write_bytes(b"%PDF-1.4\n" + b"z" * 512)
    expected_digest = hashlib.sha256(old.read_bytes()).hexdigest()

    code, data = post(base, "/api/pair", {"pin": s.pin})
    token = data["token"]
    code, data = post(base, "/api/manifest", query={"token": token})
    assert code == 200
    assert expected_digest in data["digests"]


def test_upload_without_token_is_rejected(share):
    """没有 token 不能上传——配对码只用来换 token，不能直接传文件。"""
    _s, base = share
    code, data = post(base, "/api/upload",
                      query={"name": "x.pdf"}, body=b"%PDF-1.4\n")
    assert code == 401 and data["ok"] is False
