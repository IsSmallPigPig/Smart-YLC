"""内置字体的族名与文件清单。

单独放一个模块是为了让命令行（`app/cli.py`）能读到这些常量而不 import Qt：
`app.setup` 一被 import 就会拉起 qfluentwidgets，它的欢迎横幅会写到标准输出，
JSON 就没法解析了。
"""

from __future__ import annotations

from pathlib import Path

# Allo（OFL）负责拉丁字母与数字，MiSans 负责中文：字体栈按 Allo → MiSans 排。
FONT_FAMILY = "Allo"
CJK_FAMILY = "MiSans"
FONT_FILES = (
    "Allo-Regular.otf",
    "Allo-Medium.otf",
    "Allo-SemiBold.otf",
    "Allo-Bold.otf",
    "MiSans-Regular.ttf",
    "MiSans-Medium.ttf",
    "MiSans-Demibold.ttf",
    "MiSans-Bold.ttf",
)
# Qt 5 在 Windows 上把 CFF/OTF 轮廓渲成毛边；同一套 Allo 的 TrueType 版本
# 由 scripts/build_allo_ttf.py 从官方可变字体生成，度量也对齐了 MiSans。
WINDOWS_FONT_FILES = tuple(
    name.replace(".otf", "-tt.ttf") if name.startswith("Allo-") else name
    for name in FONT_FILES
)
FONTS = {
    "darwin": [FONT_FAMILY, CJK_FAMILY, "PingFang SC"],
    "win32": [FONT_FAMILY, CJK_FAMILY, "Microsoft YaHei UI", "Microsoft YaHei"],
}


def font_files(platform: str) -> tuple[str, ...]:
    """当前平台该注册的字体文件；macOS 用原来的 OTF，Windows 用 TrueType。"""
    return WINDOWS_FONT_FILES if platform == "win32" else FONT_FILES


def fonts_dir() -> Path:
    """字体目录：源码在 app/assets/fonts，打包后在 _MEIPASS/assets/fonts。

    命令行与界面共用这份资源；不能按 app/cli.py 的 __file__ 推，冻结后
    __file__ 指向 PYZ 里的虚拟路径，会误报字体缺失。
    """
    import sys

    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        packed = Path(frozen) / "assets" / "fonts"
        if packed.is_dir():
            return packed
    return Path(__file__).resolve().parent / "assets" / "fonts"
