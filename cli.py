"""命令行入口：`python cli.py scan <目录> --json`。

具体实现与输出契约见 app/cli.py。
"""

from __future__ import annotations

import sys

from app.cli import main

if __name__ == "__main__":
    sys.exit(main())
