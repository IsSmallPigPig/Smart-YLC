"""后台提醒：只走系统原生通道，不用应用内浮窗。

macOS 走系统原生通知（UNUserNotificationCenter）：点击回调由 delegate 交回应用，
再切到对应页面；授权在启动时由 `macos_notify.request_permission()` 主动弹出，
不等第一条提醒。被系统关掉时不再弹自定义浮窗，返回 False 由调用方提示一次
「去系统设置开启」。Windows / Linux 走托盘气泡（原生、支持 messageClicked）。

浮窗与系统通知长得几乎一样，两条通道混用会让人分不清提醒是真是假，也掩盖了
「权限没开」这个需要用户亲自解决的事实，所以整条回落路径连同 Toast 一起移除。
"""

from __future__ import annotations

import platform

_default_action = None


def set_default_action(fn):
    """通知被点时如果没有具体动作，做什么（默认：把主界面叫出来）。"""
    global _default_action
    _default_action = fn


def alert(title: str, body: str = "", tone: str = "",
          tray=None, on_click=None) -> bool:
    """发一条后台提醒，返回是否已送达。

    tone 只为兼容旧调用点保留：原生通道自己带样式，这里用不上。
    原生通道不可用时只返回 False，不弹任何应用内浮窗。"""
    action = on_click or _default_action
    if platform.system() == "Darwin":
        try:
            from app.services import macos_notify

            return bool(macos_notify.send(title, body, on_click=action))
        except Exception:
            return False
    from app.services import notify

    try:
        return bool(notify.send(title, body, tray, action))
    except Exception:
        return False
