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


def test_corrupt_config_recovers_from_backup(tmp_path, monkeypatch):
    """另一进程写了一半把 JSON 截断时，从上一版备份继续，账号不能丢。"""
    from app.services import store

    path = tmp_path / "config.json"
    monkeypatch.setattr(store, "CONFIG_FILE", str(path))
    store.save({"accounts": [{"userName": "x"}]})
    path.write_text('{"accounts": [', encoding="utf-8")

    store.update(lambda c: c.__setitem__("memberCache", {"a": [1]}))
    cfg = store.load()
    assert cfg["accounts"] == [{"userName": "x"}]
    assert cfg["memberCache"] == {"a": [1]}


def test_inplace_truncation_keeps_backup_readable(tmp_path, monkeypatch):
    """旧版本进程就地截断主文件时，备份不能因为共享 inode 一起变坏。"""
    from app.services import store

    path = tmp_path / "config.json"
    monkeypatch.setattr(store, "CONFIG_FILE", str(path))
    store.save({"accounts": [{"userName": "x"}]})
    store.update(lambda c: c.__setitem__("memberCache", {"a": [1]}))

    with open(path, "r+", encoding="utf-8") as f:  # 就地截断，不换 inode
        f.truncate(12)

    cfg = store.load()
    assert cfg["accounts"] == [{"userName": "x"}]
    assert cfg["memberCache"] == {"a": [1]}


def test_corrupt_config_without_backup_refuses_to_overwrite(tmp_path, monkeypatch):
    """没有备份可读时显式报错，绝不把空字典写回覆盖原文件。"""
    import pytest

    from app.services import store

    path = tmp_path / "config.json"
    monkeypatch.setattr(store, "CONFIG_FILE", str(path))
    broken = '{"accounts": ['
    path.write_text(broken, encoding="utf-8")

    with pytest.raises(RuntimeError):
        store.update(lambda c: c.__setitem__("memberCache", {"a": [1]}))
    assert path.read_text(encoding="utf-8") == broken


def test_missing_main_file_restores_from_backup(tmp_path, monkeypatch):
    """主文件被外部删掉、备份还在时按备份恢复，不能从空配置重新开始。"""
    from app.services import store

    path = tmp_path / "config.json"
    monkeypatch.setattr(store, "CONFIG_FILE", str(path))
    store.save({"accounts": [{"userName": "a"}, {"userName": "b"}], "rosters": {"s": "x"}})
    path.unlink()

    cfg = store.load()
    assert len(cfg["accounts"]) == 2
    assert path.exists()          # 主文件被补回来
    assert store.load()["rosters"] == {"s": "x"}
