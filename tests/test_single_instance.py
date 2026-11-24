"""单实例通知：第二条命令不能因为「数据先到、信号后连」而丢掉。

Windows 命名管道上对端写完立刻可读，newConnection 回调里再连 readyRead 时，
数据往往已经全部躺在缓冲区里，之后不会再有新数据到达——信号一次都不发。
macOS 的 Unix socket 时序更松，所以这个缺陷只在 Windows 上暴露。
"""

from __future__ import annotations


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def fire(self):
        for slot in list(self.slots):
            slot()


class _Sock:
    """连接建立时数据已在缓冲区，且不会再触发 readyRead。"""

    def __init__(self, data: bytes = b"show"):
        self._data = data
        self.readyRead = _Signal()
        self.disconnected = _Signal()

    def bytesAvailable(self) -> int:
        return len(self._data)

    def readAll(self) -> bytes:
        data, self._data = self._data, b""
        return data

    def deleteLater(self):
        pass


class _Server:
    def __init__(self, sock: _Sock):
        self._sock = sock

    def nextPendingConnection(self):
        return self._sock


def test_connection_with_buffered_command_is_read_immediately():
    from app.services.single_instance import SingleInstance

    got = []
    instance = SingleInstance()
    instance.set_handler(got.append)
    instance._server = _Server(_Sock(b"show"))
    instance._on_connection()

    assert got == ["show"]


def test_handler_set_later_still_receives_early_command():
    from app.services.single_instance import SingleInstance

    instance = SingleInstance()
    instance._server = _Server(_Sock(b"show"))
    instance._on_connection()
    assert instance._pending == ["show"]

    got = []
    instance.set_handler(got.append)
    assert got == ["show"]


def test_empty_connection_stays_quiet():
    """没有数据就什么都不做，等信号；不能把空串当命令发给 handler。"""
    from app.services.single_instance import SingleInstance

    got = []
    instance = SingleInstance()
    instance.set_handler(got.append)
    instance._server = _Server(_Sock(b""))
    instance._on_connection()

    assert got == []


def _prepare(tmp_path, monkeypatch):
    from PyQt5.QtCore import QCoreApplication

    from app.services import single_instance, store

    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    # 对方还没 listen 时才需要重试；测试里不等这 3 秒
    monkeypatch.setattr(single_instance, "_notify_with_retry", lambda *a, **k: False)
    return single_instance


def test_second_instance_exits_even_if_server_not_listening(tmp_path, monkeypatch):
    """第一个进程还在启动、没来的及 listen 时，第二个也必须退出。

    只看命名管道能不能连上时，这一瞬间两边都连不上，于是各建一套托盘与窗口——
    Windows 上双击托盘叫出的是另一份，用户看到的就是「打开多个程序」。
    """
    from PyQt5.QtCore import QLockFile

    single_instance = _prepare(tmp_path, monkeypatch)
    holder = QLockFile(str(single_instance.lock_path()))
    assert holder.tryLock(100)

    guard = single_instance.SingleInstance()
    assert guard.attach() is False


def test_close_releases_lock_for_next_launch(tmp_path, monkeypatch):
    """自己退出后锁要放开，下一次启动才能正常建窗口。"""
    single_instance = _prepare(tmp_path, monkeypatch)

    first = single_instance.SingleInstance()
    assert first.attach() is True
    first.close()

    second = single_instance.SingleInstance()
    assert second.attach() is True
    second.close()


def test_unwritable_lock_dir_does_not_block_startup(tmp_path, monkeypatch):
    """配置目录只读时程序仍要能起：锁拿不到就退回只看命名管道，不能拒绝启动。"""
    single_instance = _prepare(tmp_path, monkeypatch)
    readonly = tmp_path / "readonly"
    readonly.mkdir()
    readonly.chmod(0o500)
    monkeypatch.setattr(single_instance, "lock_path",
                        lambda: readonly / "instance.lock")
    guard = single_instance.SingleInstance()
    try:
        assert guard.attach() is True
    finally:
        guard.close()
        readonly.chmod(0o700)
