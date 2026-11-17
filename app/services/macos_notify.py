"""macOS 原生通知：UNUserNotificationCenter + 点击回跳。

osascript 发出的通知归属「脚本编辑器」，点开只会跳去脚本编辑器，也拿不到
点击回调；这里改用系统的 UserNotifications 框架，通知归属打包后的 .app，
点击后由 delegate 把回调交回应用，再切到对应页面。

只有带 bundle identifier 的 .app 才能用：源码里跑 python 时 bundle id 是
空的，`available()` 返回 False，通知发不出去；调用方不回落应用内浮窗，
只提示一次「去系统设置开启」。
"""

from __future__ import annotations

import sys
import threading
import uuid

_LOCK = threading.Lock()
_CENTER = None
_DELEGATE = None
_AUTH = None          # None=未询问, True=允许, False=拒绝
_AUTH_REQUESTED = False
_PENDING: list[tuple] = []
_CALLBACKS: dict[str, object] = {}


def available() -> bool:
    """打包后的 .app 才有归属；源码运行时返回 False。

    不能只看 bundle id 有没有值：python.org 的框架版解释器本身就有
    `org.python.python`，那样通知会挂在「Python」名下，点开也回不到主界面。
    所以先看进程是否被 PyInstaller 冻结，再看 .app 的 bundle id。"""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return False
    try:
        import Foundation

        return bool(Foundation.NSBundle.mainBundle().bundleIdentifier())
    except Exception:
        return False


def app_name() -> str:
    """系统设置里这条通知挂在哪个名字下面：打包版是「智慧团委」，源码运行是「Python」。"""
    if sys.platform != "darwin":
        return ""
    try:
        import Foundation

        info = Foundation.NSBundle.mainBundle().infoDictionary() or {}
        name = str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or "")
        return name or "Python"
    except Exception:
        return "Python"


def permission_state() -> str:
    """系统通知权限：ok / denied / undetermined / unavailable。

    源码运行时进程的 bundle id 是 Python 自己的，权限记在「Python」名下；
    被关掉时通知发不出去，调用方要引导用户去系统设置开启。"""
    if sys.platform != "darwin":
        return "unavailable"
    if not available():
        return "unavailable"
    try:
        import Foundation
        import UserNotifications as UN

        center = UN.UNUserNotificationCenter.currentNotificationCenter()
        done = threading.Event()
        box: dict[str, int] = {}

        def _read(settings):
            box["status"] = int(settings.authorizationStatus())
            done.set()

        center.getNotificationSettingsWithCompletionHandler_(_read)
        if not done.wait(2.0):
            return "undetermined"
        return {2: "ok", 1: "denied"}.get(box.get("status", 0), "undetermined")
    except Exception:
        return "unavailable"


def open_settings() -> None:
    """打开「系统设置 → 通知」，用户自己去把本应用的通知打开。"""
    if sys.platform != "darwin":
        return
    try:
        import subprocess

        subprocess.Popen(
            ["open", "x-apple.systempreferences:com.apple.preference.notifications"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _ensure(center, UN, Foundation):
    """建 delegate 并挂到通知中心；只做一次。"""
    global _DELEGATE

    if _DELEGATE is not None:
        return _DELEGATE

    class Delegate(Foundation.NSObject):
        def userNotificationCenter_willPresentNotification_withCompletionHandler_(
                self, _center, _notification, completion):
            # 应用在前台时也显示横幅，否则收不到提醒
            completion(
                UN.UNNotificationPresentationOptionBanner
                | UN.UNNotificationPresentationOptionList
                | UN.UNNotificationPresentationOptionSound
            )

        def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
                self, _center, response, completion):
            try:
                info = dict(
                    response.notification().request().content().userInfo() or {})
                key = str(info.get("id") or "")
                callback = _CALLBACKS.pop(key, None)
                if callback is not None:
                    callback()
                else:
                    import AppKit

                    AppKit.NSApp.activateIgnoringOtherApps_(True)
            finally:
                completion()

    _DELEGATE = Delegate.alloc().init()
    center.setDelegate_(_DELEGATE)
    return _DELEGATE


def _deliver(center, UN, payload):
    title, body, on_click = payload
    key = uuid.uuid4().hex
    if on_click is not None:
        with _LOCK:
            _CALLBACKS[key] = on_click
            # 通知长期不点时不让回调表无限增长
            while len(_CALLBACKS) > 64:
                _CALLBACKS.pop(next(iter(_CALLBACKS)), None)

    content = UN.UNMutableNotificationContent.alloc().init()
    content.setTitle_(title)
    content.setBody_(body)
    content.setSound_(UN.UNNotificationSound.defaultSound())
    content.setUserInfo_({"id": key})
    request = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(
        key, content, None)
    center.addNotificationRequest_withCompletionHandler_(request, lambda _e: None)


def _auth_done(granted: bool, error) -> None:
    global _AUTH
    _AUTH = bool(granted)
    pending, _PENDING[:] = list(_PENDING), []
    if not _AUTH:
        with _LOCK:
            _CALLBACKS.clear()
        return
    center, UN, _ = _load()
    if center is None:
        return
    for payload in pending:
        _deliver(center, UN, payload)


def request_permission() -> None:
    """启动时主动弹出系统授权框（只在未决定时弹，一次为限）。

    不等第一条提醒再问：提醒可能几小时后才来，那时用户已经忘了为什么弹窗。
    已决定过的机器上系统不会重复弹，这里只是把状态读回来缓存。"""
    global _AUTH_REQUESTED

    if not available():
        return
    with _LOCK:
        if _AUTH_REQUESTED:
            return
        _AUTH_REQUESTED = True
    center, UN, _ = _load()
    if center is None:
        return
    center.requestAuthorizationWithOptions_completionHandler_(
        UN.UNAuthorizationOptionAlert | UN.UNAuthorizationOptionSound,
        _auth_done,
    )


def _load():
    import UserNotifications as UN
    import Foundation

    try:
        center = UN.UNUserNotificationCenter.currentNotificationCenter()
        _ensure(center, UN, Foundation)
        return center, UN, Foundation
    except Exception:
        return None, UN, Foundation


def send(title: str, body: str, on_click=None) -> bool:
    """发一条原生通知；不可用时返回 False，由调用方提示用户开启系统通知。"""
    if not available():
        return False
    center, UN, _ = _load()
    if center is None:
        return False

    payload = (title, body, on_click)
    global _AUTH_REQUESTED
    request_auth = False
    with _LOCK:
        if _AUTH is True:
            pass
        elif _AUTH is False:
            return False
        else:
            _PENDING.append(payload)
            if not _AUTH_REQUESTED:
                _AUTH_REQUESTED = True
                request_auth = True
            payload = None

    if payload is not None:
        _deliver(center, UN, payload)
    elif request_auth:
        center.requestAuthorizationWithOptions_completionHandler_(
            UN.UNAuthorizationOptionAlert | UN.UNAuthorizationOptionSound,
            _auth_done,
        )
    return True
