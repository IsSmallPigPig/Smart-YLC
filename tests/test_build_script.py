"""打包脚本：命令里必须带齐资源与平台开关，别等 Release 上才发现问题。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _dry_run():
    return subprocess.run([sys.executable, "scripts/build.py", "--dry-run"],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout


def test_build_uses_nuitka_standalone():
    out = _dry_run()
    assert "-m nuitka" in out
    assert "--standalone" in out
    assert "--enable-plugin=pyside6" in out
    assert "pyinstaller" not in out.lower()


def test_build_includes_assets_and_qfluentwidgets():
    out = _dry_run()
    assert "--include-data-dir=app/assets=app/assets" in out
    assert "--include-package-data=qfluentwidgets" in out
    assert "main.py" in out.splitlines()[-1]


def test_build_targets_current_platform():
    out = _dry_run()
    if sys.platform == "win32":
        assert "--windows-console-mode=disable" in out
        assert "--windows-icon-from-ico=" in out
    elif sys.platform == "darwin":
        assert "--macos-create-app-bundle" in out
        assert "--macos-signed-app-name=com.smallylc.smartyouth" in out
