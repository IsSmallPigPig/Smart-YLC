"""生成演示数据的命令行入口。

数据本身由 `app.services.demo_data` 提供（界面里「系统备份 → 载入演示数据」
用的是同一份），这里只是把它写到磁盘上，方便截图脚本或手工验收使用：

    python scripts/demo_data.py --home /tmp/demo-home
    HOME=/tmp/demo-home python main.py

加 --apply 则直接写当前用户的配置（会先把原文件备份成 config.json.bak-demo）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services import demo_data  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="生成演示配置")
    ap.add_argument("--apply", action="store_true",
                    help="写入当前用户配置（先备份原文件）")
    ap.add_argument("--count", type=int, default=260, help="团员总数（默认 260）")
    ap.add_argument("--home", default="", help="写到该目录下的 .gd12355-helper/config.json")
    args = ap.parse_args()

    cfg = demo_data.build_config(args.count)
    print(demo_data.summary(cfg))

    if args.apply:
        from app.services import store
        target = Path(store.CONFIG_FILE)
        if target.is_file():
            backup = target.with_suffix(".json.bak-demo")
            shutil.copyfile(target, backup)
            print("原配置已备份到", backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已写入", target)
        return 0

    home = Path(args.home) if args.home else Path(os.environ.get("TMPDIR", "/tmp")) / "gd-demo-home"
    out = home / ".gd12355-helper" / "config.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("配置写到", out)
    print(f"用它启动：HOME={home} python main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
