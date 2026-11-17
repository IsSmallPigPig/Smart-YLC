"""运行日志与崩溃报告。

日志是用户报障时唯一的现场证据，所以要验证三件事：落在配置目录（不污染真实
home）、崩溃报告含可定位的信息、安装 excepthook 后未处理异常会被记录并弹窗。
"""

from __future__ import annotations

import logging
import os
import sys

import pytest


@pytest.fixture
def fresh_log(monkeypatch, tmp_path):
    """把日志模块重置到一份临时目录，避免用例之间互相污染。"""
    from app import constants
    from app.services import app_log

    fake_cfg = tmp_path / "config.json"
    monkeypatch.setattr(constants, "CONFIG_FILE", str(fake_cfg))

    old_logger = app_log._logger
    old_hook = app_log._original_excepthook
    old_thread_hook = app_log._original_thread_excepthook
    old_fault_file = app_log._fault_file
    old_qt_installed = app_log._qt_handler_installed
    app_log._logger = None
    app_log._original_excepthook = None
    app_log._original_thread_excepthook = None
    app_log._fault_file = None
    app_log._qt_handler_installed = False
    yield app_log
    # 清理 handler，别让关闭的 tmp 目录留在全局 logger 上
    if app_log._logger is not None:
        for h in list(app_log._logger.handlers):
            h.close()
            app_log._logger.removeHandler(h)
    if app_log._fault_file is not None:
        app_log._fault_file.close()
    app_log._logger = old_logger
    app_log._original_excepthook = old_hook
    app_log._original_thread_excepthook = old_thread_hook
    app_log._qt_handler_installed = old_qt_installed
    app_log._fault_file = old_fault_file
    sys.excepthook = old_hook or sys.__excepthook__


def test_log_file_follows_config_dir(fresh_log, tmp_path):
    log = fresh_log.setup_logging()
    assert os.path.dirname(fresh_log._log_file()) == str(tmp_path / "logs")
    fresh_log.info("启动测试 %d", 7)
    for h in log.handlers:
        h.flush()
    with open(fresh_log._log_file(), encoding="utf-8") as f:
        text = f.read()
    assert "启动测试 7" in text
    assert "版本" in text and "启动" in text


def test_logging_is_idempotent(fresh_log):
    """重复初始化不叠 handler：否则每条日志写 3 遍，文件很快撑满。"""
    first = fresh_log.setup_logging()
    n = len(first.handlers)
    assert first is fresh_log.setup_logging()
    assert len(first.handlers) == n


def test_report_has_environment_stack_and_recent_log(fresh_log):
    fresh_log.setup_logging()
    fresh_log.info("关键前置事件")
    for h in fresh_log._logger.handlers:
        h.flush()

    try:
        raise ValueError("演示用崩溃")
    except ValueError:
        report = fresh_log.build_report(*sys.exc_info())

    assert "版本" in report
    assert "ValueError: 演示用崩溃" in report
    assert "崩溃报告" in report
    assert "关键前置事件" in report
    assert "最近日志" in report


def test_excepthook_logs_crash(fresh_log, monkeypatch):
    """未处理异常写进日志；弹窗在测试里替换掉，避免 offscreen 卡住。"""
    called = []
    monkeypatch.setattr(fresh_log, "_show_crash_dialog",
                        lambda report: called.append(report))
    fresh_log.install_crash_handler()
    assert sys.excepthook is fresh_log._crash_hook

    try:
        raise RuntimeError("未捕获的崩溃")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())

    for h in fresh_log._logger.handlers:
        h.flush()
    with open(fresh_log._log_file(), encoding="utf-8") as f:
        text = f.read()
    assert "RuntimeError: 未捕获的崩溃" in text
    assert "未处理异常" in text
    assert len(called) == 1


def test_export_logs_includes_all_saved_logs(fresh_log, tmp_path):
    """导出的是日志目录的全部 app.log* 与 fatal.log，不含 config.json。"""
    log = fresh_log.setup_logging()
    fresh_log.info("最新一条")
    for h in log.handlers:
        h.flush()
    with open(fresh_log._log_file(), "w", encoding="utf-8") as f:
        f.write("最新日志内容\n")
    (tmp_path / "logs" / "app.log.1").write_text("上一轮日志\n", encoding="utf-8")
    (tmp_path / "logs" / "fatal.log").write_text("原生崩溃栈\n", encoding="utf-8")
    (tmp_path / "config.json").write_text("账号密码", encoding="utf-8")

    dest = tmp_path / "exported.txt"
    assert fresh_log.export_logs(str(dest)) == 3
    text = dest.read_text(encoding="utf-8")
    assert "最新日志内容" in text
    assert "上一轮日志" in text
    assert "原生崩溃栈" in text
    assert "账号密码" not in text


def test_qt_warning_goes_to_log(fresh_log):
    """Qt 的原生警告要落盘：--windowed 没有 stderr，abort 前就靠这行定位。"""
    from PyQt5.QtCore import qWarning

    fresh_log.install_crash_handler()
    qWarning("qt warning for log")
    for h in fresh_log._logger.handlers:
        h.flush()
    assert "qt warning for log" in open(
        fresh_log._log_file(), encoding="utf-8").read()


def test_keyboard_interrupt_not_treated_as_crash(fresh_log, monkeypatch):
    """Ctrl+C 是用户主动中断，不该弹「程序崩溃」。"""
    called = []
    monkeypatch.setattr(fresh_log, "_show_crash_dialog",
                        lambda report: called.append(report))
    fresh_log.install_crash_handler()
    try:
        raise KeyboardInterrupt()
    except KeyboardInterrupt:
        sys.excepthook(*sys.exc_info())
    assert called == []
