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


# 内置字体：Maple Mono CN（OFL 许可，随程序打包）。
#
# 选它的三个理由：中英文与数字**全部等宽**（中文恰为西文 2 倍宽），
# 界面里数字跳动、表头对齐、代码风格文案都不会左右抖；一套字族覆盖全站，
# 不必在中英混排时切换字体；文件自带中文，不依赖系统装了什么。
#
# FONTS 保留成「首选内置，回退系统」，字体文件缺失时界面仍然可读。
INTERNAL_FONT = "Maple Mono CN"
FONTS = {
    "darwin": [INTERNAL_FONT, "PingFang SC"],
    "win32": [INTERNAL_FONT, "Microsoft YaHei UI", "Microsoft YaHei"],
}
FONT_FILE = "MapleMono-CN-Regular.ttf"

_UNSET = object()
_REGISTERED_FAMILY = _UNSET


def register_fonts() -> str | None:
    """注册内置字体，返回实际族名；失败返回 None（调用方回退系统字体）。

    只注册一次：QFontDatabase 会累积，重复注册同一个文件会占内存。
    """
    global _REGISTERED_FAMILY
    if _REGISTERED_FAMILY is not _UNSET:
        return _REGISTERED_FAMILY or None
    _REGISTERED_FAMILY = ""
    try:
        from PyQt5.QtGui import QFontDatabase

        from app.ui.brand import assets_dir

        path = assets_dir() / "fonts" / FONT_FILE
        if not path.is_file():
            return None
        fid = QFontDatabase.addApplicationFont(str(path))
        if fid == -1:
            return None
        families = QFontDatabase.applicationFontFamilies(fid)
        if families:
            _REGISTERED_FAMILY = families[0]
            return _REGISTERED_FAMILY
    except Exception:
        pass
    return None


def _apply_font(families: list[str] | None) -> None:
    """统一默认字体，并显式打开抗锯齿。

    qfluentwidgets 自带的字体栈以 'Segoe UI' 开头，macOS 上不存在，会触发字体
    别名填充（每次启动 ~140ms）。这里连同应用默认字体一起设成同一套族栈，
    自绘控件（图表、磁贴、banner 标题）才不会落回通用的 Sans Serif。

    抗锯齿显式钉住：系统里关掉「平滑屏幕字体边缘」（虚拟机、远程桌面常见）时
    Qt 会照做，界面上的字就是硬边毛刺；本来开着的系统不受影响。"""
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    font = QApplication.font()
    if families:
        # 用 setFamily 而不是 setFamilies：Qt5 在 macOS 上 setFamilies 对
        # QApplication.setFont 不生效（读回来还是 .AppleSystemUIFont），
        # 只有单族名才会真正换掉界面字体；族栈仍交给 setFontFamilies 兜底。
        font.setFamily(families[0])
    font.setStyleStrategy(font.styleStrategy() | QFont.StyleStrategy.PreferAntialias)
    QApplication.setFont(font)


def setup_app() -> None:
    """注册内置字体、套用字体栈，并按配置应用明暗模式（默认跟随系统）。"""
    # 先注册：注册成功时把实际族名放到字体栈最前，setFontFamilies 才认得到
    internal = register_fonts()
    families = list(FONTS.get(sys.platform) or [])
    if internal:
        families = [internal] + [f for f in families if f != internal]
    if families:
        setFontFamilies(families)
    _apply_font(families)

    from app.services import store

    apply_theme_mode(store.load().get("themeMode", "auto"))
    setThemeColor("#0078d4")
