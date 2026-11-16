"""打包脚本：命令里必须带齐资源与平台开关，别等 Release 上才发现问题。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _dry_run():
    return subprocess.run([sys.executable, "scripts/build.py", "--dry-run"],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout


def test_build_uses_pyinstaller_onedir():
    out = _dry_run()
    assert "-m PyInstaller" in out
    assert "--onedir" in out
    assert "--windowed" in out
    assert "nuitka" not in out.lower()


def test_build_includes_assets_and_qfluentwidgets():
    out = _dry_run()
    assert "--collect-all=qfluentwidgets" in out
    assert "--add-data=app/assets" in out
    assert "main.py" in out.splitlines()[-1]


def test_build_does_not_exclude_pymupdf():
    # pymupdf 由 PyInstaller 的 hook 收集原生库；一旦再加 --nofollow/--exclude
    # 就会退回 Nuitka 那套「排除后运行期缺模块」的老问题
    out = _dry_run().lower()
    assert "nofollow" not in out
    assert "exclude-module" not in out
    assert "pymupdf" not in out  # 不需要手写，交给 hook


def test_build_targets_current_platform():
    out = _dry_run()
    if sys.platform == "win32":
        assert "--icon=" in out and "smart-ylc.ico" in out
        assert "--version-file=" in out
    elif sys.platform == "darwin":
        assert "--osx-bundle-identifier=com.smallylc.smartyouth" in out
        assert "--hidden-import=UserNotifications" in out
        assert "SmartYouth.icns" in out


def test_linux_postprocess_lays_hicolor_icon(tmp_path, monkeypatch):
    """Linux 打包把图标铺进 share/icons/hicolor，.deb 从这里装到系统图标路径。"""
    import scripts.build as build

    monkeypatch.setattr(build, "ROOT", tmp_path)
    src = tmp_path / "app/assets/smart-ylc@512.png"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"\x89PNG fake")
    dist = tmp_path / "dist" / build.APP_NAME
    dist.mkdir(parents=True)

    out = build._postprocess_linux()
    assert out == dist
    icon = dist / "share/icons/hicolor/512x512/apps/SmartYouth.png"
    assert icon.is_file()


def test_linux_postprocess_noop_without_dist(tmp_path, monkeypatch):
    import scripts.build as build

    monkeypatch.setattr(build, "ROOT", tmp_path)
    assert build._postprocess_linux() is None
