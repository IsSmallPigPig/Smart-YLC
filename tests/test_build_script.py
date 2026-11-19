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


def test_build_collects_binary_heavy_packages():
    """numpy / cv2 / pymupdf 都要整包收：只靠 hook 时它们常缺子模块或原生库。"""
    out = _dry_run().lower()
    assert "nofollow" not in out
    assert "exclude-module" not in out
    for pkg in ("numpy", "cv2", "pymupdf"):
        assert f"--collect-all={pkg}" in out


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


def test_all_windows_variants_pin_pymupdf_with_win32_wheel():
    """四个 Windows 变体都必须钉 PyMuPDF 1.24.11。

    更高版本没有 win32 wheel，pip 会回退到 88MB 的 sdist 现场编译 MuPDF 的 C 代码，
    Windows10-x86 构建会卡在 "Preparing metadata (pyproject.toml)" 几十分钟。
    1.24.11 的 cp38-abi3 wheel 同时覆盖 Python 3.8 / 3.11 与 32/64 位。
    """
    import re

    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    block = text.split("build-windows:", 1)[1].split("\n  build-linux:", 1)[0]
    variants = re.findall(r"- name: (Windows[\w-]+)", block)
    assert len(variants) == 4, f"应覆盖四个 Windows 变体，实际 {variants}"
    # 每个变体的 deps 行都要钉同一个版本，不能留裸 pymupdf
    deps_lines = re.findall(r"deps: (.+)", block)
    assert len(deps_lines) == 4
    for line in deps_lines:
        assert "pymupdf==1.24.11" in line, f"未钉 PyMuPDF：{line}"
        assert not re.search(r"(?<![\w=.-])pymupdf(?![\w=.-])", line), \
            f"仍有裸 pymupdf：{line}"

    # requirements.txt 同样要钉，避免本地安装踩同一个坑
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "pymupdf==1.24.11" in req


def _load_build_module():
    """scripts 不是包，按路径加载；模块级代码只算 ROOT，没有副作用。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_build_script", ROOT / "scripts/build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_qt_tree(tmp_path: Path, *, with_pak: bool = True) -> Path:
    """造一份构建机上的 PyQt5 安装树。"""
    pkg = tmp_path / "site" / "PyQt5"
    qt = pkg / "Qt5"
    resources = qt / "resources"
    resources.mkdir(parents=True)
    if with_pak:
        (resources / "qtwebengine_resources.pak").write_text("pak")
        (resources / "icudtl.dat").write_text("icu")
    (qt / "bin").mkdir(parents=True)
    (qt / "bin" / "QtWebEngineProcess.exe").write_text("exe")
    locales = qt / "translations" / "qtwebengine_locales"
    locales.mkdir(parents=True)
    (locales / "zh-CN.pak").write_text("loc")
    (pkg / "__init__.py").write_text("")
    return pkg


def test_build_copies_webengine_files_into_bundle(tmp_path, monkeypatch):
    """PyInstaller 漏收的资源要补进产物，且按 6.x 的 _internal 布局落位。"""
    import PyQt5

    build = _load_build_module()
    pkg = _fake_qt_tree(tmp_path)
    monkeypatch.setattr(PyQt5, "__file__", str(pkg / "__init__.py"))

    dist = tmp_path / "dist" / "SmartYouth"
    (dist / "_internal").mkdir(parents=True)
    assert build._ensure_webengine_files(dist) == []

    qt = dist / "_internal" / "PyQt5" / "Qt5"
    assert (qt / "resources" / "qtwebengine_resources.pak").is_file()
    assert (qt / "resources" / "icudtl.dat").is_file()
    assert (qt / "bin" / "QtWebEngineProcess.exe").is_file()
    assert any((qt / "translations" / "qtwebengine_locales").glob("*.pak"))


def test_build_reports_missing_webengine_files(tmp_path, monkeypatch):
    """源头就没有 pak 时要报缺，不能让构建悄悄产出打不开网页的包。"""
    import PyQt5

    build = _load_build_module()
    pkg = _fake_qt_tree(tmp_path, with_pak=False)
    monkeypatch.setattr(PyQt5, "__file__", str(pkg / "__init__.py"))

    dist = tmp_path / "dist" / "SmartYouth"
    dist.mkdir(parents=True)          # PyInstaller 5.x 平铺布局
    missing = build._ensure_webengine_files(dist)
    assert "resources/qtwebengine_resources.pak" in missing
