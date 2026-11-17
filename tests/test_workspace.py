"""工作区：待上传列表存储 + 新文件自动处理（命名改名 + 压缩入列表）。"""

import os

from app.services import workspace


def test_pending_store_roundtrip(tmp_path, monkeypatch):
    from app.services import store

    # 把配置目录指到临时路径，避免碰真实配置（待上传列表跟着配置目录走）
    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    p = str(tmp_path / "a.pdf")
    with open(p, "wb") as f:
        f.write(b"x")

    assert workspace.add_pending(p, "msg") is True
    assert workspace.add_pending(p) is False  # 去重
    entries = workspace.load_pending()
    assert len(entries) == 1 and entries[0]["path"] == p and entries[0]["message"] == "msg"
    workspace.remove_pending(p)
    assert workspace.load_pending() == []


def test_new_pdf_files(tmp_path):
    (tmp_path / "b.PDF").write_bytes(b"x")
    (tmp_path / "c.txt").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    out = workspace.new_pdf_files(str(tmp_path), set(), recursive=False)
    assert len(out) == 1 and out[0].endswith("b.PDF")


def test_new_pdf_files_walks_subfolders(tmp_path):
    """来源目录可以是整个年级：子文件夹里的档案也要收。"""
    sub = tmp_path / "高一1班"
    sub.mkdir()
    (sub / "张三+13800000000+入团志愿书.pdf").write_bytes(b"x")
    (tmp_path / "说明.pdf").write_bytes(b"x")
    found = workspace.new_pdf_files(str(tmp_path), set())
    names = sorted(os.path.basename(p) for p in found)
    assert names == ["张三+13800000000+入团志愿书.pdf", "说明.pdf"]
    assert any(os.sep + "高一1班" + os.sep in p for p in found)


def test_workspace_dirs_roundtrip(tmp_path, monkeypatch):
    """多目录：正式字段是列表，旧的单值字段跟着第一个目录走。"""
    from app.services import store

    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save({})

    assert workspace.load_workspace_dirs() == []
    workspace.save_workspace_dirs(["/a", "/b", "/a"])
    cfg = store.load()
    assert cfg["workspaceDirs"] == ["/a", "/b"]   # 去重保序
    assert cfg["workspaceDir"] == "/a"
    assert workspace.load_workspace_dirs() == ["/a", "/b"]


def test_workspace_dirs_falls_back_to_single_dir(tmp_path, monkeypatch):
    """只有旧配置里的单目录时，把它当作唯一来源目录，老用户不用重新选。"""
    from app.services import store

    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save({"workspaceDir": "/old/dir"})
    assert workspace.load_workspace_dirs() == ["/old/dir"]


def test_prepare_pdf_rename_and_add(tmp_path, monkeypatch):
    # 可解析但带空格 → 自动改名为标准名
    src = tmp_path / "张三 +13800000000+入团志愿书.pdf"
    src.write_bytes(b"x" * 100)
    info = workspace.prepare_pdf(str(src))
    assert info["renamed_from"].startswith("张三")
    assert info["name"] == "张三+13800000000+入团志愿书.pdf"
    assert os.path.isfile(os.path.join(tmp_path, info["name"]))
    assert info["problem"] == ""


def test_prepare_pdf_unparsable(tmp_path):
    src = tmp_path / "随手扫描件.pdf"
    src.write_bytes(b"x" * 100)
    info = workspace.prepare_pdf(str(src))
    assert info["name"] == "随手扫描件.pdf"
    assert info["problem"] != ""
