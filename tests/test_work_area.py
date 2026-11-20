"""工作目录路径规则：每个来源目录各一份「异常 / 压缩」，原件不进扫描。"""

from __future__ import annotations

import os

from app.domain import work_area


def test_copy_target_keeps_relative_subfolder(tmp_path):
    """同名的两份档案分处两个班：副本保留子路径，不会被压平覆盖。"""
    a = tmp_path / "高一1班" / "张三+13800000001+入团志愿书.pdf"
    b = tmp_path / "高一2班" / "张三+13800000001+入团志愿书.pdf"
    ta = work_area.copy_target(str(tmp_path), str(a))
    tb = work_area.copy_target(str(tmp_path), str(b))
    assert ta == os.path.join(str(tmp_path), "工作目录", "压缩",
                              "高一1班", os.path.basename(a))
    assert tb == os.path.join(str(tmp_path), "工作目录", "压缩",
                              "高一2班", os.path.basename(b))
    assert ta != tb


def test_copy_target_abnormal_area(tmp_path):
    src = tmp_path / "高一1班" / "张三+13800000001+入团志愿书.pdf"
    normal = work_area.copy_target(str(tmp_path), str(src))
    abnormal = work_area.copy_target(str(tmp_path), str(src), abnormal=True)
    assert "工作目录" in normal and "压缩" in normal
    assert "工作目录" in abnormal and "异常" in abnormal
    assert os.path.basename(normal) == os.path.basename(abnormal)


def test_scan_skips_work_dir(tmp_path):
    """工作目录里的副本不能再被扫成原件，否则一份算两遍。"""
    from app.domain.archive_scan import scan_folder

    original = tmp_path / "高一1班" / "张三+13800000001+入团志愿书.pdf"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"x")
    copy = work_area.copy_target(str(tmp_path), str(original))
    os.makedirs(os.path.dirname(copy), exist_ok=True)
    with open(copy, "wb") as f:
        f.write(b"x")

    scan = scan_folder(str(tmp_path))
    assert scan.files == [str(original)]
    assert not any("工作目录" in p for p in scan.files)


def test_is_abnormal_copy_and_inside_work_dir(tmp_path):
    abnormal = work_area.copy_target(str(tmp_path), "x.pdf", abnormal=True)
    normal = work_area.copy_target(str(tmp_path), "x.pdf")
    assert work_area.is_abnormal_copy(abnormal)
    assert not work_area.is_abnormal_copy(normal)
    assert work_area.is_inside_work_dir(abnormal)
    assert work_area.is_inside_work_dir(normal)
    assert not work_area.is_inside_work_dir(str(tmp_path / "高一1班" / "x.pdf"))


def test_ensure_work_dirs_creates_both(tmp_path):
    work_area.ensure_work_dirs(str(tmp_path))
    assert os.path.isdir(work_area.abnormal_dir(str(tmp_path)))
    assert os.path.isdir(work_area.compressed_dir(str(tmp_path)))
