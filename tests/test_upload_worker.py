"""上传 worker 的并发编排：支部之间并行、支部内部串行、计数不丢。"""

from __future__ import annotations

import os
import threading
import time

import pytest

from app.ui import upload_page as up


def _pdf(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4\n")
    return str(path)


class _FakeClient:
    def __init__(self, account):
        self.account = account
        self.uploads: list[str] = []
        self.audits: list[str] = []

    def upload_file(self, path):
        self.uploads.append(path)
        time.sleep(0.05)          # 模拟网络往返
        from app.services.api import ApiResult

        return ApiResult(True, "ok")

    def audit_voluntary_book(self, member_id):
        self.audits.append(member_id)
        from app.services.api import ApiResult

        return ApiResult(True, "ok")


def test_upload_runs_accounts_in_parallel(qapp_session, tmp_path, monkeypatch):
    """三个支部各自一份档案：并发提交时总耗时应接近单份，而不是三倍。"""
    jobs = [
        {"path": _pdf(tmp_path, "张三+13800000001+入团志愿书.pdf"), "account": "a1"},
        {"path": _pdf(tmp_path, "李四+13800000002+入团志愿书.pdf"), "account": "a2"},
        {"path": _pdf(tmp_path, "王五+13800000003+入团志愿书.pdf"), "account": "a3"},
    ]
    worker = up.UploadWorker({"a1": "p", "a2": "p", "a3": "p"}, jobs, target=10 << 20)

    seen: set[str] = set()
    lock = threading.Lock()

    def fake_login(self, account):
        with lock:
            seen.add(account)
        return _FakeClient(account), ""

    monkeypatch.setattr(up.UploadWorker, "_login", fake_login)
    monkeypatch.setattr(up.UploadWorker, "_member_map", lambda self, c, a: {})

    rows: list[tuple] = []
    worker.row_ready.connect(lambda *a: rows.append(a))
    started = time.perf_counter()
    worker.run()
    elapsed = time.perf_counter() - started
    qapp_session.processEvents()

    assert seen == {"a1", "a2", "a3"}
    assert len(rows) == 3 and all(r[2] == "成功" for r in rows)
    assert elapsed < 0.45, f"三个支部串行要 0.15s 以上，实测 {elapsed:.2f}s"


def test_upload_keeps_one_account_sequential(qapp_session, tmp_path, monkeypatch):
    """同一支部的多份档案必须按顺序提交：站点的分片上传按支部受理。"""
    jobs = [
        {"path": _pdf(tmp_path, f"张{i}+1380000000{i}+入团志愿书.pdf"), "account": "a1"}
        for i in range(1, 4)
    ]
    worker = up.UploadWorker({"a1": "p"}, jobs, target=10 << 20)
    order: list[str] = []
    lock = threading.Lock()

    class Serial(_FakeClient):
        def upload_file(self, path):
            with lock:
                order.append(os.path.basename(path))
            return super().upload_file(path)

    monkeypatch.setattr(up.UploadWorker, "_login",
                        lambda self, account: (Serial(account), ""))
    monkeypatch.setattr(up.UploadWorker, "_member_map", lambda self, c, a: {})

    worker.run()
    qapp_session.processEvents()
    assert order == [os.path.basename(j["path"]) for j in jobs]


def test_upload_oversized_batch_reports_every_file(qapp_session, tmp_path, monkeypatch):
    """超过单批上限时每份都要给出原因，不能只报第一条。"""
    monkeypatch.setattr(up.UploadWorker, "batch_limit", 2)
    jobs = [
        {"path": _pdf(tmp_path, f"张{i}+1380000000{i}+入团志愿书.pdf"), "account": "a1"}
        for i in range(1, 4)
    ]
    worker = up.UploadWorker({"a1": "p"}, jobs, target=10 << 20)
    rows: list[tuple] = []
    worker.row_ready.connect(lambda *a: rows.append(a))

    monkeypatch.setattr(up.UploadWorker, "_login",
                        lambda self, account: (pytest.fail("不该登录"), ""))
    worker.run()
    qapp_session.processEvents()
    assert len(rows) == 3
    assert all("超过" in r[3] for r in rows)
