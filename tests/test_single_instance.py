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
