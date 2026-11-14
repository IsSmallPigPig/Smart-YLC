"""系统通知：macOS 用 osascript（无第三方依赖），Windows 走托盘气泡。

Windows 的 toast 必须挂在托盘图标上，而主界面模式本身不建托盘，所以这里按需
借一个：没有现成托盘就临时建一个（只建一次）。没有这层回落时，Windows 上的
新站内信、更新提醒、测试通知全部静默丢掉了。
"""

from __future__ import annotations

import platform
import subprocess

_FALLBACK_TRAY = None


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _fallback_tray():
    """非 macOS 的托盘回落：拿不到就返回 None，由调用方判失败。"""
    global _FALLBACK_TRAY
    if _FALLBACK_TRAY is not None:
        return _FALLBACK_TRAY
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    if QApplication.instance() is None or not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    try:
        from app.ui.brand import app_icon
        from app.ui.page import set_tip

        icon = QSystemTrayIcon(app_icon())
        set_tip(icon, "智慧团建管理员辅助")
        icon.show()
        _FALLBACK_TRAY = icon
    except Exception:
        return None
    return _FALLBACK_TRAY


def send(title: str, body: str, tray=None) -> bool:
    """发送系统通知。tray 可选传 QSystemTrayIcon；不传时非 macOS 按需借一个。"""
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
    target = tray or _fallback_tray()
    if target is None:
        return False
    try:
        from PySide6.QtWidgets import QSystemTrayIcon

        # showMessage 只接受 MessageIcon 或 QIcon；传 qfluentwidgets 的
        # InfoBarIcon 会直接 TypeError，通知被静默丢掉（Windows 上就是全哑）
        target.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 8000)
        return True
    except Exception:
        return False
