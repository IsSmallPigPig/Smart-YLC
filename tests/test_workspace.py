"""工作区：待上传列表存储 + 新文件自动处理（命名改名 + 压缩入列表）。"""

import os

from app.services import workspace


def test_pending_store_roundtrip(tmp_path):
    from app.constants import CONFIG_FILE
    import app.constants as C

    # 把配置目录指到临时路径，避免碰真实配置
    real = C.CONFIG_FILE
    try:
        C.CONFIG_FILE = str(tmp_path / "config.json")
        p = str(tmp_path / "a.pdf")
        with open(p, "wb") as f:
            f.write(b"x")
        assert workspace.add_pending(p, "msg") is True
        assert workspace.add_pending(p) is False  # 去重
        entries = workspace.load_pending()
        assert len(entries) == 1 and entries[0]["path"] == p and entries[0]["message"] == "msg"
        workspace.remove_pending(p)
        assert workspace.load_pending() == []
    finally:
        C.CONFIG_FILE = real


def test_new_pdf_files(tmp_path):
    (tmp_path / "b.PDF").write_bytes(b"x")
    (tmp_path / "c.txt").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    out = workspace.new_pdf_files(str(tmp_path), set())
    assert len(out) == 1 and out[0].endswith("b.PDF")


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
