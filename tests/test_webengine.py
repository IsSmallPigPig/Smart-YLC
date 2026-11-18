"""冻结产物里 WebEngine 路径解析：认得 PyInstaller 两种布局，缺 pak 就不许用。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app.services import webengine as W

ENV_KEYS = ("QTWEBENGINE_RESOURCES_PATH",
            "QTWEBENGINEPROCESS_PATH",
            "QTWEBENGINE_LOCALES_PATH")


def _reset() -> None:
    W._prepared = False
    W._ok = False
    W._why = ""
    W._resources = None


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """每个用例都从「没探测过、没设过变量」开始，避免缓存串味。"""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    _reset()
    yield
    _reset()


def _bundle(tmp_path: Path, monkeypatch, *, internal: bool = True, pak: bool = True) -> Path:
    """造一份 onedir 产物布局，返回 PyQt5/Qt5 目录。"""
    root = tmp_path / "_internal" if internal else tmp_path
    qt = root / "PyQt5" / "Qt5"
    resources = qt / "resources"
    resources.mkdir(parents=True)
    if pak:
        (resources / "qtwebengine_resources.pak").write_text("pak")
        (resources / "icudtl.dat").write_text("icu")
    (qt / "bin").mkdir(parents=True)
    # 两个名字都放：Windows 上是 .exe，macOS/Linux 上不带后缀，测试在两平台都能跑
    (qt / "bin" / "QtWebEngineProcess.exe").write_text("exe")
    (qt / "bin" / "QtWebEngineProcess").write_text("exe")
    locales = qt / "translations" / "qtwebengine_locales"
    locales.mkdir(parents=True)
    (locales / "en-US.pak").write_text("l")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(root), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "SmartYouth.exe"), raising=False)
    return qt


def test_dev_environment_is_available():
    assert W.available() is True
    assert W.resources_dir() is None


def test_pyinstaller6_layout_pins_all_paths(tmp_path, monkeypatch):
    qt = _bundle(tmp_path, monkeypatch, internal=True)

    assert W.available() is True
    assert Path(os.environ["QTWEBENGINE_RESOURCES_PATH"]) == qt / "resources"
    assert Path(os.environ["QTWEBENGINEPROCESS_PATH"]).parent == qt / "bin"
    assert Path(os.environ["QTWEBENGINE_LOCALES_PATH"]) == qt / "translations" / "qtwebengine_locales"


def test_pyinstaller5_flat_layout_also_found(tmp_path, monkeypatch):
    qt = _bundle(tmp_path, monkeypatch, internal=False)

    assert W.available() is True
    assert Path(os.environ["QTWEBENGINE_RESOURCES_PATH"]) == qt / "resources"


def test_missing_pak_means_unavailable(tmp_path, monkeypatch):
    """目录在、pak 不在时必须判不可用：这正是 int3 崩进程的场景。"""
    _bundle(tmp_path, monkeypatch, pak=False)

    assert W.available() is False
    assert "qtwebengine_resources.pak" in W.reason()
    assert "QTWEBENGINE_RESOURCES_PATH" not in os.environ


def test_prepare_is_idempotent(tmp_path, monkeypatch):
    _bundle(tmp_path, monkeypatch)
    W.prepare()
    os.environ["QTWEBENGINE_RESOURCES_PATH"] = "sentinel"

    assert W.prepare() is True
    # 第二次不再探测，也不会把调用方改过的值覆盖回去
    assert os.environ["QTWEBENGINE_RESOURCES_PATH"] == "sentinel"


def test_short_path_leaves_ascii_alone():
    assert W._short_path("/tmp/abc") == "/tmp/abc"
