"""运行日志 + 崩溃报告。

用户遇到闪退、数据不对时能把一份完整报告发过来，不用反复追问「复现一下」「截个图」。
所以两件事一起做：

* **运行时**持续记录关键节点（启动、切页、同步结果、异常）到滚动日志文件。
* **崩溃时**装 `sys.excepthook`，把 traceback 写进同一份日志，再弹一个窗口显示
  报告全文，并提供「复制报告」「打开日志目录」。窗口用原生 QMessageBox 而不是
  fluent 弹窗——崩溃路径上主题/QSS 可能还没就绪，越少依赖越可靠。

路径跟配置目录走（`~/.gd12355-helper/logs/`），因此 --demo / 冒烟测试改了 HOME
后日志也落在那份临时目录里，不污染用户真实数据。文件用 RotatingFileHandler
限 3×1MB，长期挂着托盘也不会把磁盘写满。
"""

from __future__ import annotations

import logging
import os
import sys
import traceback
from datetime import datetime

from app import __version__
from app import constants

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

_logger: logging.Logger | None = None
_original_excepthook = None
_started = datetime.now()


def _log_dir() -> str:
    """延迟解析，不缓存：--demo / 测试改了 CONFIG_FILE 后日志要跟着走。

    模块级写死目录会让 --demo 的日志落到用户真实 home 里，正是 demo 模式承诺
    「不碰真实配置」要避免的事。
    """
    return os.path.join(os.path.dirname(constants.CONFIG_FILE), "logs")


def _log_file() -> str:
    return os.path.join(_log_dir(), "app.log")


def _environment_line() -> str:
    """报告头：版本、平台、Python，排查时先确认这三样对不对得上。"""
    import platform

    return (f"版本 {__version__} | {platform.system()} {platform.release()} "
            f"({platform.machine()}) | Python {platform.python_version()}")


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化日志。多次调用幂等：Qt 里入口被重复进入是常事，别叠一堆 handler。

    日志写文件失败（目录只读、磁盘满）时降级到 stderr——绝不让「记录日志」本身
    把程序弄崩。返回 logger 供各模块直接使用。
    """
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger("smartyouth")
    logger.setLevel(level)
    logger.propagate = False  # 不往 root 冒，避免第三方库把 root 配成 DEBUG 后刷屏
    fmt = logging.Formatter(_LOG_FORMAT)

    try:
        os.makedirs(_log_dir(), exist_ok=True)
        from logging.handlers import RotatingFileHandler

        fh = RotatingFileHandler(_log_file(), maxBytes=1024 * 1024,
                                 backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass

    if not logger.handlers:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    _logger = logger
    logger.info("=" * 60)
    logger.info("启动 | %s", _environment_line())
    logger.info("日志目录 %s", _log_dir())
    return logger


def log(level: int, message: str, *args, **kwargs) -> None:
    """记录一条日志；日志系统没初始化时静默跳过。"""
    if _logger is not None:
        _logger.log(level, message, *args, **kwargs)


def info(message: str, *args, **kwargs) -> None:
    log(logging.INFO, message, *args, **kwargs)


def warning(message: str, *args, **kwargs) -> None:
    log(logging.WARNING, message, *args, **kwargs)


def error(message: str, *args, **kwargs) -> None:
    log(logging.ERROR, message, *args, **kwargs)


def exception(message: str, *args, **kwargs) -> None:
    log(logging.ERROR, message, *args, exc_info=True, **kwargs)


def _read_recent(limit: int = 80) -> str:
    """读最后 N 行日志，作为崩溃报告的一部分。"""
    try:
        with open(_log_file(), encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-limit:])
    except OSError:
        return "（无法读取日志文件）"


def build_report(exc_type, exc_value, tb) -> str:
    """生成可提交的完整报告：环境 + 异常堆栈 + 最近的日志。"""
    stack = "".join(traceback.format_exception(exc_type, exc_value, tb))
    duration = datetime.now() - _started
    head = (f"智慧团建管理员辅助 崩溃报告\n"
            f"生成时间 {datetime.now():%Y-%m-%d %H:%M:%S} | 已运行 {duration}\n"
            f"{_environment_line()}\n"
            f"日志路径 {_log_file()}\n\n"
            f"=== 异常 ===\n{stack}\n"
            f"=== 最近日志 ===\n")
    return head + _read_recent()


def _show_crash_dialog(report: str) -> None:
    """弹崩溃窗口。QApplication 不存在（启动早期崩）时只写 stderr。"""
    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox
    except ImportError:
        print(report, file=sys.stderr)
        return

    app = QApplication.instance()
    if app is None:
        print(report, file=sys.stderr)
        return

    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle("程序遇到错误")
    box.setText("程序发生未处理的错误，已记录崩溃日志。\n"
                "请把报告复制后提交给开发者，帮助定位问题。")
    box.setDetailedText(report)
    copy_btn = box.addButton("复制报告", QMessageBox.ButtonRole.ActionRole)
    dir_btn = box.addButton("打开日志目录", QMessageBox.ButtonRole.ActionRole)
    box.addButton("退出", QMessageBox.ButtonRole.RejectRole)

    def _copy():
        try:
            app.clipboard().setText(report)
            copy_btn.setText("已复制 ✓")
        except Exception:
            copy_btn.setEnabled(False)

    def _open_dir():
        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(_log_dir()))
        except Exception:
            dir_btn.setEnabled(False)

    copy_btn.clicked.connect(_copy)
    dir_btn.clicked.connect(_open_dir)
    box.exec_()


def _crash_hook(exc_type, exc_value, tb) -> None:
    """未处理异常的统一入口：落盘 → 弹窗 → 交回原 hook。"""
    # KeyboardInterrupt 是用户主动中断（Ctrl+C），不是崩溃
    if issubclass(exc_type, KeyboardInterrupt):
        if _original_excepthook is not None:
            _original_excepthook(exc_type, exc_value, tb)
        return

    stack = "".join(traceback.format_exception(exc_type, exc_value, tb))
    if _logger is not None:
        _logger.critical("未处理异常：\n%s", stack)
    else:
        print(stack, file=sys.stderr)

    try:
        report = build_report(exc_type, exc_value, tb)
        _show_crash_dialog(report)
    except Exception:
        # 弹窗本身失败不能再抛，否则 Python 打印 "Error in sys.excepthook" 后
        # 直接 abort，用户连日志文件都没机会找
        pass

    if _original_excepthook is not None:
        _original_excepthook(exc_type, exc_value, tb)


def install_crash_handler() -> None:
    """接管未处理异常。在 QApplication 创建之后调用，崩溃时才弹得出窗口。

    只装一次：Qt 的事件循环里重复安装 excepthook 没有意义，还会让原 hook 链越
    套越深。保留原 hook 以便交回默认行为（打印到 stderr、设置退出码）。
    """
    global _original_excepthook
    setup_logging()
    if _original_excepthook is None:
        _original_excepthook = sys.excepthook
        sys.excepthook = _crash_hook
        info("已安装崩溃处理器")
