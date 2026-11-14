"""应用级初始化（GUI 与托盘共用）：字体栈 + 主题。

qfluentwidgets 默认字体栈以 'Segoe UI' 开头（Windows 字体），macOS 上不存在，
必须按平台设置，否则每次启动触发 qt.qpa.fonts 别名填充告警（~140ms 开销）。
"""

from __future__ import annotations

import sys

from qfluentwidgets import setFontFamilies, setTheme, Theme, setThemeColor

THEME_MODES = {"auto": Theme.AUTO, "light": Theme.LIGHT, "dark": Theme.DARK}


def apply_theme_mode(mode: str) -> Theme:
    """按「auto / light / dark」应用主题；未知值回退跟随系统。"""
    theme = THEME_MODES.get(mode, Theme.AUTO)
    setTheme(theme)
    return theme


def setup_app() -> None:
    """按平台设置组件字体栈，并按配置应用明暗模式（默认跟随系统）。"""
    if sys.platform == "darwin":
        setFontFamilies(["PingFang SC"])
    elif sys.platform == "win32":
        setFontFamilies(["Segoe UI", "Microsoft YaHei"])

    from app.services import store

    apply_theme_mode(store.load().get("themeMode", "auto"))
    setThemeColor("#0078d4")
