"""开机自启动管理：macOS LaunchAgent / Windows 注册表 Run 键 / Linux XDG autostart（登录静默启动托盘后台模式）。"""

from __future__ import annotations

import os
import subprocess
import sys

LABEL = "net.12355.tuan.adminhelper.qt"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
PLIST_DIR = os.path.expanduser("~/Library/LaunchAgents")
PLIST_PATH = os.path.join(PLIST_DIR, f"{LABEL}.plist")
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# XDG autostart 规范：~/.config/autostart/<桌面项>.desktop，登录时由桌面环境拉起
AUTOSTART_DESKTOP_DIR = os.path.expanduser("~/.config/autostart")
AUTOSTART_DESKTOP_PATH = os.path.join(AUTOSTART_DESKTOP_DIR, f"{LABEL}.desktop")
DESKTOP_TEMPLATE = """[Desktop Entry]
Type=Application
Name=智慧团委
Exec={exe} --tray
X-GNOME-Autostart-enabled=true
Comment=智慧团委后台托盘
"""


def _linux_command() -> str:
    """Linux 自启动命令行：打包后是 AppImage/deb 里的 exe，源码运行回退 python -m。"""
    exe = sys.executable
    if os.path.basename(exe) in ("python", "python3"):
        return f'"{exe}" -m app.main --tray'
    return f'"{exe}" --tray'

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


def _windows_command() -> str:
    """Windows 自启动命令行：打包 exe 直接加 --tray；源码运行用 pythonw -m。"""
    exe = sys.executable.lower()
    if exe.endswith("pythonw.exe") or not exe.endswith("python.exe"):
        return f'"{sys.executable}" --tray'  # 打包后的 exe
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pythonw}" -m app.main --tray'


def is_installed() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, LABEL)
                return True
        except OSError:
            return False
    if sys.platform.startswith("linux"):
        return os.path.isfile(AUTOSTART_DESKTOP_PATH)
    return os.path.isfile(PLIST_PATH)


def install() -> str:
    """安装并立即生效。返回说明文本（macOS 是 plist 路径，Windows 是命令行）。"""
    if sys.platform == "win32":
        import winreg

        cmd = _windows_command()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, LABEL, 0, winreg.REG_SZ, cmd)
        return cmd
    if sys.platform.startswith("linux"):
        os.makedirs(AUTOSTART_DESKTOP_DIR, exist_ok=True)
        with open(AUTOSTART_DESKTOP_PATH, "w", encoding="utf-8") as f:
            f.write(DESKTOP_TEMPLATE.format(exe=_linux_command()))
        return AUTOSTART_DESKTOP_PATH
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
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, LABEL)
        except FileNotFoundError:
            pass
        return
    if sys.platform.startswith("linux"):
        try:
            os.remove(AUTOSTART_DESKTOP_PATH)
        except FileNotFoundError:
            pass
        return
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
