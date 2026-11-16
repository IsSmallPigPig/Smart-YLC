"""macOS 原生通知：UNUserNotificationCenter + 点击回跳。

osascript 发出的通知归属「脚本编辑器」，点开只会跳去脚本编辑器，也拿不到
点击回调；这里改用系统的 UserNotifications 框架，通知归属打包后的 .app，
点击后由 delegate 把回调交回应用，再切到对应页面。

只有带 bundle identifier 的 .app 才能用：源码里跑 python 时 bundle id 是
空的，`available()` 返回 False，调用方回落到应用内浮窗或 osascript。
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
    """打包后的 .app 才有 bundle id；源码运行时返回 False。"""
    if sys.platform != "darwin":
        return False
    try:
        import Foundation

        return bool(Foundation.NSBundle.mainBundle().bundleIdentifier())
    except Exception:
        return False


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
    """发一条原生通知；不可用时返回 False，由调用方回落到应用内浮窗。"""
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
