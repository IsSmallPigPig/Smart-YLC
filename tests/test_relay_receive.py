"""中转接收的测试：用假 HTTP 响应覆盖拉取、摘要校验、去重、落盘、删副本。

不碰真实中转站，也不碰用户「同步自手机」目录。
"""

from __future__ import annotations

import hashlib

import pytest

from app.services import phone_sync
from app.services import relay_receive as relay


@pytest.fixture()
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(phone_sync, "sync_dir", lambda today=None: str(tmp_path))
    monkeypatch.setattr(phone_sync, "register_source_dir", lambda path: None)
    monkeypatch.setattr(relay, "normalize_server", lambda s: "https://relay.test")


class FakeResp:
    def __init__(self, payload=None, content=b"", status=200):
        self._payload = payload
        self.content = content
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_receive_all_saves_and_reports(tmp_path, local, monkeypatch):
    pdf = b"%PDF-1.4\n" + b"p" * 1024
    digest = hashlib.sha256(pdf).hexdigest()
    meta = {"id": "f1", "name": "张三+13800138000+入团志愿书.pdf",
            "size": len(pdf), "sha256": digest}

    monkeypatch.setattr(relay, "list_pending", lambda *a: [meta])
    monkeypatch.setattr(relay, "pull", lambda *a: pdf)
    cleared = []
    monkeypatch.setattr(relay, "clear", lambda *a: cleared.append(a))

    result = relay.receive_all("https://relay.test", "ABC234", owner_key="k" * 64)
    assert len(result["saved"]) == 1 and not result["errors"]
    assert cleared and cleared[0][2] == "f1"          # 云端副本已删


def test_receive_all_skips_existing_digest(tmp_path, local, monkeypatch):
    pdf = b"%PDF-1.4\n" + b"q" * 512
    digest = hashlib.sha256(pdf).hexdigest()
    meta = {"id": "f2", "name": "已传过的.pdf", "sha256": digest}
    monkeypatch.setattr(relay, "list_pending", lambda *a: [meta])
    monkeypatch.setattr(relay, "pull", lambda *a: pytest.fail("不该拉取已存在的文件"))

    result = relay.receive_all("https://relay.test", "ABC234", already={digest})
    assert result["skipped"] == ["已传过的.pdf"] and not result["saved"]


def test_receive_all_rejects_corrupted_digest(tmp_path, local, monkeypatch):
    meta = {"id": "f3", "name": "损坏的.pdf", "sha256": "0" * 64}
    monkeypatch.setattr(relay, "list_pending", lambda *a: [meta])
    monkeypatch.setattr(relay, "pull", lambda *a: b"%PDF-1.4\n")

    result = relay.receive_all("https://relay.test", "ABC234")
    assert len(result["rejected"]) == 1 and not result["saved"]
    assert not list(tmp_path.glob("*.pdf"))          # 损坏文件没落盘


def test_list_pending_parses_files(monkeypatch, local):
    monkeypatch.setattr(relay.requests, "get",
                        lambda *a, **kw: FakeResp({"ok": True, "files": [
                            {"id": "f1", "name": "a.pdf"}, {"name": "缺id"}]}))
    files = relay.list_pending("https://relay.test", "ABC234")
    assert len(files) == 1 and files[0]["id"] == "f1"


def test_raise_for_exposes_server_error():
    resp = FakeResp({"error": "提取码不存在"}, status=404)
    with pytest.raises(relay.RelayError, match="提取码不存在"):
        relay._raise_for(resp, "查询")
