"""来源目录扫描：递归收 PDF、按命名排除无关文件。"""

from __future__ import annotations

import os

from app.domain.archive_scan import relative_to, scan_folder, scan_folders
from app.domain.naming import analyze_file_name


def standard(name: str, mobile: str) -> str:
    return analyze_file_name(f"{name}+{mobile}+入团志愿书.pdf").suggested_name


def touch(directory, name: str) -> str:
    path = os.path.join(str(directory), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x")
    return path


def test_scan_walks_subfolders(tmp_path):
    """来源目录常是「整个年级」：子文件夹里的档案必须收进来。"""
    touch(tmp_path, standard("张三", "13800000001"))
    touch(tmp_path / "补交", standard("李四", "13800000002"))

    scan = scan_folder(str(tmp_path))
    assert sorted(os.path.basename(p) for p in scan.files) == sorted(
        [standard("张三", "13800000001"), standard("李四", "13800000002")])
    assert scan.ignored == []


def test_scan_excludes_files_whose_name_is_not_archive(tmp_path):
    """通知、公示这类 PDF 解析不出姓名与手机号：排除，但记在 ignored 里可查。"""
    touch(tmp_path, standard("张三", "13800000001"))
    notice = touch(tmp_path, "关于国庆放假的通知.pdf")
    touch(tmp_path / "补交", "会议签到表.pdf")

    scan = scan_folder(str(tmp_path))
    assert len(scan.files) == 1
    assert notice in scan.ignored and len(scan.ignored) == 2


def test_scan_skips_hidden_and_macosx(tmp_path):
    touch(tmp_path, standard("张三", "13800000001"))
    touch(tmp_path / "__MACOSX", standard("李四", "13800000002"))
    touch(tmp_path / ".git", standard("王五", "13800000003"))

    scan = scan_folder(str(tmp_path))
    assert len(scan.files) == 1


def test_scan_reports_missing_folder(tmp_path):
    scan = scan_folder(str(tmp_path / "不存在的目录"))
    assert scan.missing and scan.files == []
    assert scan.name == "不存在的目录"


def test_scan_name_is_folder_basename(tmp_path):
    folder = tmp_path / "高一1班"
    folder.mkdir()
    assert scan_folder(str(folder)).name == "高一1班"


def test_scan_folders_keeps_order(tmp_path):
    a = tmp_path / "A"
    b = tmp_path / "B"
    a.mkdir()
    b.mkdir()
    scans = scan_folders([str(b), str(a)])
    assert [s.name for s in scans] == ["B", "A"]


def test_relative_to_gives_subpath(tmp_path):
    nested = tmp_path / "补交" / standard("张三", "13800000001")
    path = touch(nested.parent, nested.name)
    assert relative_to(str(tmp_path), path) == os.path.join("补交", nested.name)
