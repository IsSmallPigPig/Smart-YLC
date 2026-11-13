"""应用级初始化（GUI 与托盘共用）：字体栈 + 主题。

qfluentwidgets 默认字体栈以 'Segoe UI' 开头（Windows 字体），macOS 上不存在，
必须按平台设置，否则每次启动触发 qt.qpa.fonts 别名填充告警（~140ms 开销）。
"""

from __future__ import annotations

import sys

from qfluentwidgets import setFontFamilies, setTheme, Theme, setThemeColor


def setup_app() -> None:
    """按平台设置组件字体栈，并启用跟随系统的深浅色主题。"""
    if sys.platform == "darwin":
        setFontFamilies(["PingFang SC"])
    elif sys.platform == "win32":
        setFontFamilies(["Segoe UI", "Microsoft YaHei"])
    setTheme(Theme.AUTO)
    setThemeColor("#0078d4")
