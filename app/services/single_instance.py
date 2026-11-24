"""单实例：第二次启动不再开一套窗口，只把已有实例叫到前面。

两层：先用 QLockFile 抢一把文件锁，抢到才算本进程是唯一的实例；再用
QLocalServer / QLocalSocket 把「show」发给已有实例。

只用命名管道是不够的：连接有 350ms 超时，双方都在启动时都可能连不上，
第二个进程于是也去 listen——Windows 上就出现两份托盘图标、两套窗口，
双击托盘有时叫出的是另一份。文件锁由内核保证互斥，没有这个窗口期。

服务名与锁文件都按登录用户区分，同一台机器上不同用户各开一份互不影响。

`--tray` 与普通启动共用这一把锁：先有托盘、再双击图标时，由托盘进程
打开主窗口；先有主窗口、再启动托盘时，同样只把现有窗口叫出来。
"""

from __future__ import annotations

import getpass
import os
import time
from pathlib import Path

from PyQt5.QtCore import QLockFile, QObject, pyqtSignal as Signal
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


def lock_path() -> Path:
    """锁文件与配置同目录：测试与 --config-dir 换目录时自动跟着换。"""
    from app.services import store

    return Path(store.CONFIG_FILE).parent / f"instance{'-' + SUFFIX if SUFFIX else ''}.lock"


def notify_running(command: bytes = COMMAND_SHOW, timeout: int = 350) -> bool:
    """已有实例在跑就发一条命令并返回 True；没有则返回 False。"""
    sock = QLocalSocket()
    sock.connectToServer(server_name())
    if not sock.waitForConnected(timeout):
        return False
    sock.write(command)
    sock.flush()
    sock.waitForBytesWritten(timeout)
    sock.disconnectFromServer()
    return True


def _notify_with_retry(command: bytes = COMMAND_SHOW,
                       tries: int = 12, gap: float = 0.25) -> bool:
    """对方刚抢到锁时可能还没开始 listen，隔一会儿重试，别把命令丢掉。"""
    for attempt in range(tries):
        if notify_running(command):
            return True
        if attempt + 1 < tries:
            time.sleep(gap)
    return False


class SingleInstance(QObject):
    """监听第二个实例发来的命令；handler 可以晚于监听设置。"""

    command_received = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._handler = None
        self._pending: list[str] = []
        self._lock: QLockFile | None = None
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)

    def attach(self) -> bool:
        """拿到锁返回 True；已有实例返回 False（调用方应直接退出）。"""
        path = lock_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass        # 目录建不出来时下面的锁会失败，走退路
        self._lock = QLockFile(str(path))
        # 默认 30 秒的「过期」只是兜底：QLockFile 会先看持有者进程还在不在，
        # 被任务管理器强杀留下的锁文件下次启动照样能拿走。
        if not self._lock.tryLock(120):
            if not path.exists():
                # 锁文件根本建不出来（配置目录只读）：退回只看命名管道，
                # 不能因为拿不到锁就不让程序启动
                self._lock = None
                if notify_running():
                    return False
            else:
                _notify_with_retry()
                return False
        # 上一次进程异常退出可能留下 socket 文件；先清掉再监听
        QLocalServer.removeServer(server_name())
        if not self._server.listen(server_name()):
            # 锁拿到了却抢不到名字：旧版本进程还活着（它没有文件锁），让位给它
            if notify_running():
                self._release_lock()
                return False
            raise RuntimeError(f"无法建立单实例通道：{self._server.errorString()}")
        return True

    def _release_lock(self) -> None:
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None

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
        # 对端可能在连上 readyRead 之前就把数据写完并断开，此后再不会有新数据，
        # 信号永远不发——命令就丢了。Windows 命名管道写完立刻可用，更容易踩到。
        if sock.bytesAvailable():
            self._read(sock)

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
        self._release_lock()
