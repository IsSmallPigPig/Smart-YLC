"""手机同步弹窗的线程模型：轮询可中断、HTTP 回调经信号回 GUI 线程。

这两条都曾是真故障：QThread.wait() 当 sleep 用是未定义行为；请求线程里
QTimer.singleShot 没有事件循环可挂，回调永不触发，收件界面不更新。
"""

from __future__ import annotations

import threading
import time

import pytest


def _dialog():
    """FluentPanelDialog 要按 parent 尺寸居中，给它一个宿主再构造。"""
    from PyQt5.QtWidgets import QWidget

    from app.ui.phone_sync_dialog import PhoneSyncDialog

    host = QWidget()
    dialog = PhoneSyncDialog(parent=host, auto_start=False)
    dialog._test_host = host   # 持住宿主，避免 GC 后 parent 失效
    return dialog


def test_relay_sleep_wakes_on_stop():
    """stop() 要能立刻唤醒周期等待，不用等满 interval。"""
    from app.ui.phone_sync_dialog import _RelayWorker

    worker = _RelayWorker({}, interval_ms=30_000)
    # 100ms 后 stop：若仍是"睡满 30s"的旧实现，这个测试会挂到超时
    timer = threading.Timer(0.1, worker.stop)
    timer.start()
    t0 = time.monotonic()
    try:
        cont = worker._sleep_or_wake()
    finally:
        timer.cancel()
    elapsed = time.monotonic() - t0
    assert cont is False, "stop() 后应返回 False 让 run 退出"
    assert elapsed < 2.0, f"stop() 唤醒耗时 {elapsed:.1f}s，应远小于一个周期"


def test_relay_sleep_full_cycle_when_not_stopped():
    """没被 stop 时睡满整个周期再返回 True。"""
    from app.ui.phone_sync_dialog import _RelayWorker

    worker = _RelayWorker({}, interval_ms=150)
    t0 = time.monotonic()
    assert worker._sleep_or_wake() is True
    assert time.monotonic() - t0 >= 0.12


def test_on_received_uses_signal_not_timer(monkeypatch):
    """HTTP 线程的收件回调走信号投递，不能再建 QTimer（那个线程无事件循环）。"""
    from PyQt5.QtCore import QTimer

    from app.services.lan_share import Received

    dialog = _dialog()
    try:
        calls = []
        monkeypatch.setattr(QTimer, "singleShot",
                            lambda *a, **k: calls.append((a, k)))
        shown = []
        dialog._http_received.connect(lambda item: shown.append(item))

        item = Received(name="张三.pdf", path="/tmp/张三.pdf", status="ok")
        dialog._on_received(item)

        # emit 是直接连到本对象的信号；此处验证它真的发出，且没退化成 singleShot
        assert shown == [item]
        assert calls == [], "不该用 QTimer.singleShot——请求线程没有事件循环"
    finally:
        dialog._cleanup()
        dialog.deleteLater()


def test_http_received_connected_to_show():
    """构造时就接好 _http_received → _show_received，emit 即更新界面。"""
    from PyQt5.QtWidgets import QApplication

    from app.services.lan_share import Received

    dialog = _dialog()
    try:
        item = Received(name="李四.pdf", path="/tmp/李四.pdf", status="ok", note="已保存")
        dialog._http_received.emit(item)
        QApplication.instance().processEvents()  # 同线程直接派发
        assert "李四.pdf" in dialog.result_label.text()
    finally:
        dialog._cleanup()
        dialog.deleteLater()


def test_stop_relay_orphans_running_worker():
    """请求未回来时关窗：线程要脱离弹窗并由模块级集合持有，否则析构 abort。"""
    from PyQt5.QtCore import QMutex, QMutexLocker, QThread, QWaitCondition

    from app.ui import phone_sync_dialog as psd

    class _Blocked(QThread):
        def __init__(self):
            super().__init__()
            self._m = QMutex()
            self._c = QWaitCondition()
            self._release = False

        def run(self):
            with QMutexLocker(self._m):
                while not self._release:
                    self._c.wait(self._m, 100)

        def stop(self):
            pass   # 模拟网络请求还没回来，stop() 无法立刻结束线程

        def release(self):
            with QMutexLocker(self._m):
                self._release = True
                self._c.wakeAll()

    dialog = _dialog()
    worker = _Blocked()
    worker.start()
    try:
        dialog._relay_worker = worker
        dialog._stop_relay()          # stop() 对 _Blocked 无效：仍在跑
        assert worker.parent() is None
        assert worker in psd._orphaned_relays
        assert dialog._relay_worker is None
    finally:
        worker.release()
        worker.wait(2000)
        psd._drop_orphan(worker)
        dialog.deleteLater()


def test_paired_signal_records_device_and_shows_hint(qapp_session, tmp_path, monkeypatch):
    """配对提醒走信号回 GUI 线程，并写入已绑定设备列表。"""
    from app.services import paired_devices
    import app.ui.bg_toast as bg_toast

    monkeypatch.setattr(paired_devices, "PAIRED_FILE", str(tmp_path / "paired.json"))
    alerts = []
    monkeypatch.setattr(bg_toast, "alert", lambda title, body="", **kw: alerts.append((title, body)))

    dialog = _dialog()
    try:
        dialog._paired.emit("我的手机", "192.168.1.9")
        qapp_session.processEvents()
        assert [d.name for d in paired_devices.list_devices()] == ["我的手机"]
        assert "我的手机" in dialog.result_label.text()
        assert alerts and "我的手机" in alerts[0][0] + alerts[0][1]
    finally:
        dialog._cleanup()
        dialog.deleteLater()


def test_forget_device_updates_list(qapp_session, tmp_path, monkeypatch):
    """解除绑定后列表立即刷新，磁盘记录也一并删除。"""
    from app.services import paired_devices

    monkeypatch.setattr(paired_devices, "PAIRED_FILE", str(tmp_path / "paired.json"))
    paired_devices.remember("旧手机", "10.0.0.5")
    dialog = _dialog()
    try:
        dialog._forget_device("旧手机", "10.0.0.5")
        assert paired_devices.list_devices() == []
        assert "还没有设备" in dialog.device_box.findChild(
            __import__("PyQt5.QtWidgets", fromlist=["QLabel"]).QLabel).text()
    finally:
        dialog._cleanup()
        dialog.deleteLater()


def test_dialog_widens_with_host(qapp_session):
    """横屏宿主更宽时弹窗构造出来就更宽，别把二维码挤在窄条里。"""
    from PyQt5.QtWidgets import QWidget

    from app.ui.phone_sync_dialog import PhoneSyncDialog

    small = QWidget()
    small.resize(500, 600)
    host = QWidget()
    host.resize(1000, 600)
    narrow = PhoneSyncDialog(small, auto_start=False)
    wide = PhoneSyncDialog(host, auto_start=False)
    assert narrow.widget.minimumWidth() >= 480
    assert wide.widget.minimumWidth() > narrow.widget.minimumWidth()
    narrow._cleanup()
    narrow.deleteLater()
    wide._cleanup()
    wide.deleteLater()
