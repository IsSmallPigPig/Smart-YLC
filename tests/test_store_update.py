import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_update_merges_with_latest_config(tmp_path, monkeypatch):
    """store.update 必须基于最新配置再写：后写的键不能抹掉先写入的其他键。"""
    from app.services import store

    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save({"memberCache": {"a": [1]}})

    # 第一笔：GUI 线程写账号；第二笔：工作线程写成员缓存
    store.update(lambda cfg: cfg.__setitem__("accounts", [{"userName": "x"}]))
    store.update(lambda cfg: cfg.__setitem__("memberCache", {"b": [2]}))

    cfg = store.load()
    assert cfg["accounts"] == [{"userName": "x"}]  # 旧实现的 load-save 竞态会把这里抹掉
    assert cfg["memberCache"] == {"b": [2]}


def test_update_creates_missing_file(tmp_path, monkeypatch):
    from app.services import store

    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    cfg = store.update(lambda c: c.__setitem__("k", "v"))
    assert cfg["k"] == "v" and store.load()["k"] == "v"
