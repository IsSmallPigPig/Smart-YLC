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


# 平台字体栈：Windows 用 Segoe UI + Microsoft YaHei UI（后者是为界面字号调过
# hinting 的那一版，小字比旧 Microsoft YaHei 干净）；macOS 用苹方。
FONTS = {
    "darwin": ["PingFang SC"],
    "win32": ["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"],
}


def _apply_font(families: list[str] | None) -> None:
    """统一默认字体，并显式打开抗锯齿。

    qfluentwidgets 自带的字体栈以 'Segoe UI' 开头，macOS 上不存在，会触发字体
    别名填充（每次启动 ~140ms）。这里连同应用默认字体一起设成同一套族栈，
    自绘控件（图表、磁贴、banner 标题）才不会落回通用的 Sans Serif。

    抗锯齿显式钉住：系统里关掉「平滑屏幕字体边缘」（虚拟机、远程桌面常见）时
    Qt 会照做，界面上的字就是硬边毛刺；本来开着的系统不受影响。"""
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    font = QApplication.font()
    if families:
        font.setFamilies(families)
    font.setStyleStrategy(font.styleStrategy() | QFont.StyleStrategy.PreferAntialias)
    QApplication.setFont(font)


def setup_app() -> None:
    """按平台设置组件字体栈，并按配置应用明暗模式（默认跟随系统）。"""
    families = FONTS.get(sys.platform)
    if families:
        setFontFamilies(families)
    _apply_font(families)

    from app.services import store

    apply_theme_mode(store.load().get("themeMode", "auto"))
    setThemeColor("#0078d4")
