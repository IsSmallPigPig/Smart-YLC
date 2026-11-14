"""PyInstaller 打包入口：python -m app.main 的等价物。

PyInstaller 不支持 -m 方式打包包模块，用这个根级脚本作为入口。
"""
from __future__ import annotations

import sys

from app.main import run

if __name__ == "__main__":
    sys.exit(run())
