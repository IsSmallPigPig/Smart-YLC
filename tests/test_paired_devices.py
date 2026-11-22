"""已绑定设备的读写测试：配对记录要能持久化、合并、显式清除。"""

from __future__ import annotations

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from app.services import paired_devices

    path = tmp_path / "paired.json"
    monkeypatch.setattr(paired_devices, "PAIRED_FILE", str(path))
    return paired_devices


def test_remember_and_list(store):
    assert store.list_devices() == []
    store.remember("小明的手机", "192.168.1.20", now=1000)
    devices = store.list_devices()
    assert len(devices) == 1
    assert devices[0].name == "小明的手机"
    assert devices[0].paired_at == 1000
    assert devices[0].last_at == 1000


def test_same_device_merges_and_keeps_first_time(store):
    store.remember("手机", "10.0.0.2", now=100)
    store.remember("手机", "10.0.0.2", now=200)
    devices = store.list_devices()
    assert len(devices) == 1
    assert devices[0].paired_at == 100
    assert devices[0].last_at == 200


def test_forget_only_removes_matching_device(store):
    store.remember("手机甲", "10.0.0.2", now=100)
    store.remember("手机乙", "10.0.0.3", now=200)
    store.forget("手机甲", "10.0.0.2")
    assert [d.name for d in store.list_devices()] == ["手机乙"]


def test_corrupt_file_raises_instead_of_returning_empty(store, tmp_path):
    """读不出来必须报错，不能返回空列表让下一次写入抹掉历史。"""
    with open(store.PAIRED_FILE, "w", encoding="utf-8") as f:
        f.write("{broken")
    with pytest.raises(RuntimeError, match="读取失败"):
        store.list_devices()
