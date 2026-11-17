"""单实例：第二次启动不再开一套窗口，只把已有实例叫到前面。

用 QLocalServer / QLocalSocket 做进程间通知：先试着连名字；连上就发送
show 并退出，连不上才在本进程监听。服务名按登录用户区分，同一台机器上
不同用户各开一份互不影响。

`--tray` 与普通启动共用这一把锁：先有托盘、再双击图标时，由托盘进程
打开主窗口；先有主窗口、再启动托盘时，同样只把现有窗口叫出来。
"""

from __future__ import annotations

import getpass
import os

from PyQt5.QtCore import QObject, pyqtSignal as Signal
from PyQt5.QtNetwork import QLocalServer, QLocalSocket

COMMAND_SHOW = b"show"
SERVER_PREFIX = "SmartYLC"
# 进程内后缀：--demo 会设成 "demo"，让演示实例与正式实例各用一把锁。
# 不加这个时，演示实例启动会把已经开着的正式窗口叫到前面然后自己退掉，
# 反过来也一样——两个用途完全不同，不该互相顶。
SUFFIX = ""


def server_name() -> str:
    """按用户隔离；Windows 的用户名可能带空格，QLocalServer 接受。"""
    user = getpass.getuser() or str(os.getpid())
    tail = f"-{SUFFIX}" if SUFFIX else ""
    return f"{SERVER_PREFIX}-{user}{tail}"


def notify_running(command: bytes = COMMAND_SHOW) -> bool:
    """已有实例在跑就发一条命令并返回 True；没有则返回 False。"""
    sock = QLocalSocket()
    sock.connectToServer(server_name())
    if not sock.waitForConnected(350):
        return False
    sock.write(command)
    sock.flush()
    sock.waitForBytesWritten(350)
    sock.disconnectFromServer()
    return True


class SingleInstance(QObject):
    """监听第二个实例发来的命令；handler 可以晚于监听设置。"""

    command_received = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._handler = None
        self._pending: list[str] = []
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)

    def attach(self) -> bool:
        """拿到锁返回 True；已有实例返回 False（调用方应直接退出）。"""
        if notify_running():
            return False
        # 上一次进程异常退出可能留下 socket 文件；先清掉再监听
        QLocalServer.removeServer(server_name())
        if not self._server.listen(server_name()):
            # 监听失败说明刚有另一个进程抢先；再通知一次，避免两个都退出
            if notify_running():
                return False
            raise RuntimeError(f"无法建立单实例通道：{self._server.errorString()}")
        return True

    def set_handler(self, handler) -> None:
        self._handler = handler
        pending, self._pending = self._pending, []
        for command in pending:
            handler(command)

    def _on_connection(self):
        sock = self._server.nextPendingConnection()
        if sock is None:
            return
        sock.readyRead.connect(lambda s=sock: self._read(s))
        sock.disconnected.connect(sock.deleteLater)

    def _read(self, sock: QLocalSocket):
        command = bytes(sock.readAll()).decode("utf-8", "replace").strip()
        if not command:
            return
        if self._handler is None:
            self._pending.append(command)
        else:
            self._handler(command)

    def close(self):
        self._server.close()
        QLocalServer.removeServer(server_name())
