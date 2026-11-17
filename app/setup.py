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


# 内置字体：MiSans（小米，全球免费商用，随程序打包）。
#
# 中文、拉丁字母用 MiSans 原字形（比例字形，排版自然）；数字用字体自带的
# `.tf`（tabular figures）字形，天然等宽——统计值实时跳动时不会左右抖，
# `245 / 260`、`13 / 52 人` 这类并排数值成列。
#
# 数字等宽不动用 OpenType 特性：PyQt5 5.15 没有 QFont.setFeature，开不了
# `tnum`。打包前已把 cmap 的 U+0030–U+0039 指到 `.tf` 字形（见同目录
# LICENSE-MiSans.txt），字形轮廓未改。
#
# 注册四个字重而不是只注册 Regular：界面用了 Normal / Medium / DemiBold / Bold
# 四档，只给一个文件的话 Qt 会合成假粗体（笔画糊、字宽也变）。
FONT_FAMILY = "MiSans"
FONT_FILES = (
    "MiSans-Regular.ttf",
    "MiSans-Medium.ttf",
    "MiSans-Demibold.ttf",
    "MiSans-Bold.ttf",
)
FONTS = {
    "darwin": [FONT_FAMILY, "PingFang SC"],
    "win32": [FONT_FAMILY, "Microsoft YaHei UI", "Microsoft YaHei"],
}

_UNSET = object()
_REGISTERED_FAMILY = _UNSET


def register_fonts() -> str | None:
    """注册内置字体，返回实际族名；失败返回 None（调用方回退系统字体）。

    只注册一次：QFontDatabase 会累积，重复注册同一个文件会占内存。
    四个字重都要注册，否则 Qt 只能拿 Regular 合成假粗体。
    """
    global _REGISTERED_FAMILY
    if _REGISTERED_FAMILY is not _UNSET:
        return _REGISTERED_FAMILY or None
    _REGISTERED_FAMILY = ""
    try:
        from PyQt5.QtGui import QFontDatabase

        from app.ui.brand import assets_dir

        folder = assets_dir() / "fonts"
        found: list[str] = []
        for name in FONT_FILES:
            path = folder / name
            if not path.is_file():
                continue
            fid = QFontDatabase.addApplicationFont(str(path))
            if fid == -1:
                continue
            families = QFontDatabase.applicationFontFamilies(fid)
            if families and families[0] not in found:
                found.append(families[0])
        if found:
            # 各字重报告的族名可能带字重后缀，优先取干净的那个
            family = FONT_FAMILY if FONT_FAMILY in found else found[0]
            _REGISTERED_FAMILY = family
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
