"""系统通知：macOS 用 osascript（无第三方依赖），其余平台回落到托盘通知。"""

from __future__ import annotations

import platform
import subprocess


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def send(title: str, body: str, tray=None) -> bool:
    """发送系统通知。tray 可选传 QSystemTrayIcon 作为非 macOS 的回落通道。"""
    if platform.system() == "Darwin":
        try:
            script = (
                f'display notification "{_escape(body)}" '
                f'with title "{_escape(title)}" sound name "default"'
            )
            subprocess.run(["osascript", "-e", script], check=True, timeout=10)
            return True
        except (subprocess.SubprocessError, OSError):
            return False
    if tray is not None:
        try:
            from qfluentwidgets import InfoBarIcon

            tray.showMessage(title, body, InfoBarIcon.INFORMATION, 8000)
            return True
        except Exception:
            return False
    return False
