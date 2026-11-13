"""开机自启动管理：macOS LaunchAgent（登录静默启动托盘后台模式）。"""

from __future__ import annotations

import os
import subprocess
import sys

LABEL = "net.12355.tuan.adminhelper.qt"
PLIST_DIR = os.path.expanduser("~/Library/LaunchAgents")
PLIST_PATH = os.path.join(PLIST_DIR, f"{LABEL}.plist")
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>app.main</string>
        <string>--tray</string>
    </array>
    <key>WorkingDirectory</key><string>{workdir}</string>
    <key>RunAtLoad</key><true/>
    <key>ProcessType</key><string>Background</string>
    <key>StandardOutPath</key><string>{log}</string>
    <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def is_installed() -> bool:
    return os.path.isfile(PLIST_PATH)


def install() -> str:
    """安装并立即加载 LaunchAgent。返回说明文本。"""
    os.makedirs(PLIST_DIR, exist_ok=True)
    log_path = os.path.join(PLIST_DIR, "gd12355-helper-bg.log")
    plist = PLIST_TEMPLATE.format(
        label=LABEL, python=sys.executable, workdir=PROJECT_DIR, log=log_path
    )
    with open(PLIST_PATH, "w", encoding="utf-8") as f:
        f.write(plist)
    _launchctl("unload", PLIST_PATH)
    _launchctl("load", PLIST_PATH)
    return PLIST_PATH


def uninstall() -> None:
    _launchctl("unload", PLIST_PATH)
    try:
        os.remove(PLIST_PATH)
    except FileNotFoundError:
        pass


def _launchctl(*args: str) -> None:
    try:
        subprocess.run(["launchctl", *args], check=False, timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError):
        pass
