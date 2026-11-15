"""系统通知：Windows 走托盘气泡，macOS 用 osascript。

托盘气泡支持点击（`messageClicked`），传了 `on_click` 就在气泡被点时执行一次。
macOS 的 osascript 通知点击会打开「脚本编辑器」——通知归属发通知的进程，改不了，
也拿不到点击回调，所以带点击需求的后台提醒不走这里，改用 `app.ui.bg_toast`。

Windows 的 toast 必须挂在托盘图标上，而主界面模式本身可能还没建托盘，所以这里
按需借一个：没有现成托盘就临时建一个（只建一次）。没有这层回落时，Windows 上的
新站内信、更新提醒、测试通知全部静默丢掉了。
"""

from __future__ import annotations

import platform
import subprocess
from collections import deque

_FALLBACK_TRAY = None
# 气泡不携带「是哪一条被点了」的信息，按发送顺序排队，点一次取一条
_PENDING_CLICKS: deque = deque()


def _bind_click(tray) -> None:
    """只绑一次：重复 showMessage 不重复连接，否则一次点击会触发多条。"""
    if getattr(tray, "_click_bound", False):
        return
    tray._click_bound = True
    tray.messageClicked.connect(_fire_pending_click)


def _fire_pending_click() -> None:
    if not _PENDING_CLICKS:
        return
    callback = _PENDING_CLICKS.popleft()
    try:
        callback()
    except Exception:
        pass


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


def send(title: str, body: str, tray=None, on_click=None) -> bool:
    """发送系统通知。tray 可选传 QSystemTrayIcon；不传时非 macOS 按需借一个。

    on_click: 气泡被点时执行一次的回调。macOS 的 osascript 通知无法绑定，
    传了回调就直接返回 False，由调用方换成应用内浮窗。"""
    if platform.system() == "Darwin":
        if on_click is not None:
            return False
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
        if on_click is not None:
            _bind_click(target)
            _PENDING_CLICKS.append(on_click)
        return True
    except Exception:
        return False
