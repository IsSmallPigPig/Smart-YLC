"""异常档案：文件夹就是清单。

标记异常 = 文件移进同级「异常档案」；取消标记 = 移回上一层；
扫描照常收这个文件夹里的 PDF，界面上按路径自动归入异常。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication, QWidget

from app.domain.archive_scan import (ABNORMAL_DIR_NAME, abnormal_target,
                                     is_abnormal_path, normal_target)


@pytest.fixture()
def app():
    yield QApplication.instance() or QApplication([])


def _pdf(folder, name="张三+13800000000+入团志愿书.pdf"):
    folder = str(folder)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(b"%PDF-1.4")
    return path


def test_is_abnormal_path_matches_any_level(tmp_path):
    deep = tmp_path / "高一1班" / ABNORMAL_DIR_NAME / "a.pdf"
    assert is_abnormal_path(str(deep))
    assert not is_abnormal_path(str(tmp_path / "高一1班" / "a.pdf"))
    # 只是文件名里含关键字不算
    assert not is_abnormal_path(str(tmp_path / "异常档案说明.pdf"))


def test_abnormal_target_is_sibling_folder(tmp_path):
    path = _pdf(tmp_path / "高一1班")
    assert abnormal_target(path) == os.path.join(
        str(tmp_path / "高一1班"), ABNORMAL_DIR_NAME, os.path.basename(path))


def test_normal_target_avoids_overwriting(tmp_path):
    path = _pdf(tmp_path / "高一1班" / ABNORMAL_DIR_NAME)
    target = normal_target(path)
    assert target == os.path.join(str(tmp_path / "高一1班"), os.path.basename(path))
    # 原处已经有一份同名文件：加 (2)，不覆盖
    _pdf(tmp_path / "高一1班")
    assert normal_target(path).endswith("(2).pdf")


def test_scan_collects_files_inside_abnormal_folder(tmp_path):
    from app.domain.archive_scan import scan_folder

    path = _pdf(tmp_path / "高一1班" / ABNORMAL_DIR_NAME)
    scan = scan_folder(str(tmp_path))
    assert path in scan.files


def _stub_page():
    """只带 _mark_abnormal / _sync_abnormal 用到的那几个属性。"""
    from app.ui import upload_page

    stub = QWidget()
    stub._abnormal_paths = set()
    stub._cell_counts = {}
    stub._ignored_blanks = set()
    stub._preview_view = None
    stub.refresh = lambda: None
    stub._mark_abnormal = lambda path, bad: upload_page.UploadPage._mark_abnormal(
        stub, path, bad)
    stub._relocate = lambda path, bad: upload_page.UploadPage._relocate(
        stub, path, bad)
    stub._sync_abnormal = lambda statuses: upload_page.UploadPage._sync_abnormal(
        stub, statuses)
    return stub


def test_mark_abnormal_moves_copy_in_and_back(app, tmp_path, monkeypatch):
    """标记异常只搬工作副本：原件不动，副本在 压缩↔异常 之间切换。"""
    from app.domain import work_area
    from app.domain.upload_inspect import FileStatus
    from app.ui import upload_page

    monkeypatch.setattr(upload_page.InfoBar, "success", lambda *a, **k: None)
    monkeypatch.setattr(upload_page.InfoBar, "error", lambda *a, **k: None)

    original = _pdf(tmp_path / "高一1班")
    root = str(tmp_path)
    work_area.ensure_work_dirs(root)
    normal_copy = work_area.copy_target(root, original)
    os.makedirs(os.path.dirname(normal_copy), exist_ok=True)
    with open(normal_copy, "wb") as f:
        f.write(b"%PDF-1.4")

    stub = _stub_page()
    stub.statuses = [FileStatus(path=original, name=os.path.basename(original),
                                root=root, work_path=normal_copy)]
    stub._mark_abnormal(original, True)

    abnormal_copy = work_area.copy_target(root, original, abnormal=True)
    assert os.path.isfile(original)            # 原件始终在原地
    assert os.path.isfile(abnormal_copy)       # 副本进了异常区
    assert not os.path.exists(normal_copy)     # 压缩区不再有它

    stub._mark_abnormal(original, False)
    assert os.path.isfile(original)
    assert os.path.isfile(normal_copy)         # 副本搬回压缩区
    assert not os.path.exists(abnormal_copy)


def test_mark_abnormal_rejects_redundant_toggle(app, tmp_path, monkeypatch):
    """已经是异常状态的文件再「标记」不该重复搬家。"""
    from app.ui import upload_page

    monkeypatch.setattr(upload_page.InfoBar, "success", lambda *a, **k: None)
    original = _pdf(tmp_path / "高一1班")
    stub = _stub_page()
    stub._abnormal_paths = {original}
    stub._mark_abnormal(original, True)
    assert os.path.isfile(original)


def test_relocate_with_missing_copy_falls_back_to_original(app, tmp_path, monkeypatch):
    """副本还没生成时标记异常：从原件复制一份到异常区，原件不动。"""
    from app.domain import work_area
    from app.ui import upload_page

    monkeypatch.setattr(upload_page.InfoBar, "success", lambda *a, **k: None)
    monkeypatch.setattr(upload_page.InfoBar, "error", lambda *a, **k: None)

    from app.domain.upload_inspect import FileStatus

    original = _pdf(tmp_path / "高一1班")
    stub = _stub_page()
    stub.statuses = [FileStatus(path=original, name=os.path.basename(original),
                                root=str(tmp_path), work_path="")]
    target = stub._relocate(original, True)
    assert target == work_area.copy_target(str(tmp_path), original, abnormal=True)
    assert os.path.isfile(target)
    assert os.path.isfile(original)


def test_sync_abnormal_adopts_folder_files(app, tmp_path):
    from app.domain.upload_inspect import FileStatus

    inside = _pdf(tmp_path / "高一1班" / ABNORMAL_DIR_NAME)
    normal = _pdf(tmp_path / "高一1班", "李四+13900000000+入团志愿书.pdf")
    stub = _stub_page()
    stub._sync_abnormal([
        FileStatus(path=inside, name=os.path.basename(inside)),
        FileStatus(path=normal, name=os.path.basename(normal)),
    ])
    assert stub._abnormal_paths == {inside}


def test_row_item_marks_abnormal_row(app, tmp_path):
    """异常行：warning 黄底 + 「已标记」标签，说明里写明不参与上传。"""
    from app.domain.upload_inspect import FileStatus
    from app.ui import upload_page

    path = _pdf(tmp_path / "高一1班")
    stub = _stub_page()
    stub._abnormal_paths = {path}
    item = upload_page.UploadPage._row_item(
        stub, FileStatus(path=path, name=os.path.basename(path), pages=13))

    assert item["abnormal"] is True
    # 标签颜色用 PillLabel 认识的取值；写 "warning" 会落进默认灰
    assert item["tone"] == "error"
    assert item["status"] == "已标记"
    assert "不参与上传" in item["note"]


def test_row_item_adds_missing_cell_pill(app, tmp_path):
    """表格有空格时单独挂一个「漏填」软标签，不和「异常」挤在同一个红标签里。"""
    from app.domain.upload_inspect import FileStatus
    from app.ui import upload_page

    path = _pdf(tmp_path / "高一1班")
    stub = _stub_page()
    stub._abnormal_paths = set()
    stub._cell_counts = {path: 2}
    item = upload_page.UploadPage._row_item(
        stub, FileStatus(path=path, name=os.path.basename(path), pages=13))

    assert ("漏填", "warning", "warning") in item["extra_pills"]
    assert "2 处空格" in item["note"]

    stub._cell_counts = {path: 0}
    clean = upload_page.UploadPage._row_item(
        stub, FileStatus(path=path, name=os.path.basename(path), pages=13))
    assert clean["extra_pills"] == []


def test_row_item_ignores_blanks_after_user_ignores(app, tmp_path):
    """忽略漏填后：不再铺黄底、不挂「漏填」标签，说明写「已忽略 N 处」。"""
    from app.domain.upload_inspect import FileStatus
    from app.ui import upload_page

    path = _pdf(tmp_path / "高一1班")
    stub = _stub_page()
    stub._cell_counts = {path: 3}
    status = FileStatus(path=path, name=os.path.basename(path), pages=13)

    before = upload_page.UploadPage._row_item(stub, status)
    assert before["abnormal"] is True
    assert ("漏填", "warning", "warning") in before["extra_pills"]

    stub._ignored_blanks = {path}
    after = upload_page.UploadPage._row_item(stub, status)
    assert after["abnormal"] is False
    assert after["extra_pills"] == []
    assert "已忽略 3 处空格" in after["note"]


def test_ignore_blanks_persists_without_touching_usable(app, monkeypatch, tmp_path):
    """忽略只写 ignoredBlanks，不改档案的可上传判定。"""
    from app.domain.upload_inspect import FileStatus
    from app.services import store
    from app.ui import upload_page

    path = _pdf(tmp_path / "高一1班")
    stub = _stub_page()
    stub._ignored_blanks = set()
    stub._rebuild_branches = lambda: None
    # 用临时配置文件，别写到 ~/.gd12355-helper/config.json
    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save({"accounts": []})
    upload_page.UploadPage.ignore_blanks(stub, path)

    assert path in stub._ignored_blanks
    assert path in (store.load().get("ignoredBlanks") or [])
    assert FileStatus(path=path, name=os.path.basename(path),
                      account="ssfz1").usable is True
