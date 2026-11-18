"""PyInstaller 打包入口：python -m app.main 的等价物。

PyInstaller 不支持 -m 方式打包包模块，用这个根级脚本作为入口。
"""
from __future__ import annotations

import sys

from app.main import run


def _prepare_bundled_webengine() -> None:
    """在 import QtWebEngine 之前钉好子进程与资源路径。

    具体探测在 app.services.webengine：它同时给出「能不能用」的结论，界面层
    据此决定是否初始化引擎（资源不全时 Qt 5.15 会 int3 崩进程，不是抛异常）。
    """
    from app.services.webengine import prepare

    prepare()


if __name__ == "__main__":
    _prepare_bundled_webengine()
    sys.exit(run())
