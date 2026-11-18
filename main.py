"""PyInstaller 打包入口：python -m app.main 的等价物。

PyInstaller 不支持 -m 方式打包包模块，用这个根级脚本作为入口。
"""
from __future__ import annotations

import sys

import os
from pathlib import Path

from app.main import run


def _prepare_bundled_webengine() -> None:
    """PyInstaller 包里 QtWebEngine 的子进程与资源不在 Qt 默认查找路径上。

    QtWebEngine 启动时按 QLibraryInfo 推 QtWebEngineProcess.exe 的位置，
    打包后那条路径指向构建机的 PyQt5，于是报 could not find QtWebEngineProcess。
    这里按 onedir 的两种布局（PyInstaller 5 平铺、6 收进 _internal）显式指过去。
    """
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        return
    exe_dir = Path(sys.executable).resolve().parent
    roots = [
        Path(getattr(sys, "_MEIPASS", exe_dir)),
        exe_dir,
        exe_dir / "_internal",
        # PyInstaller 6 onedir：exe 在安装根，依赖收在 _internal/ 下
        exe_dir / "_internal" / "PyQt5" / "Qt5",
    ]
    # 6.x 布局下 _MEIPASS 已指向 _internal，上面的组合覆盖 5.x 平铺与 6.x 收拢
    for root in roots:
        qt = root / "PyQt5" / "Qt5"
        proc = qt / "bin" / "QtWebEngineProcess.exe"
        if not proc.is_file():
            continue
        # 直接写，不用 setdefault：PyQt5 的启动代码可能先塞了一个指向构建机的值，
        # 保留它就会继续报 QtWebEngine resource not found。
        os.environ["QTWEBENGINEPROCESS_PATH"] = str(proc)
        resources = qt / "resources"
        if resources.is_dir():
            os.environ["QTWEBENGINE_RESOURCES_PATH"] = str(resources)
        locales = qt / "translations" / "qtwebengine_locales"
        if locales.is_dir():
            os.environ["QTWEBENGINE_LOCALES_PATH"] = str(locales)
        return


if __name__ == "__main__":
    _prepare_bundled_webengine()
    sys.exit(run())
