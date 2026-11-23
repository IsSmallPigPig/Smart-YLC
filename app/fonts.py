"""内置字体的族名与文件清单。

单独放一个模块是为了让命令行（`app/cli.py`）能读到这些常量而不 import Qt：
`app.setup` 一被 import 就会拉起 qfluentwidgets，它的欢迎横幅会写到标准输出，
JSON 就没法解析了。
"""

from __future__ import annotations

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
FONTS = {
    "darwin": [FONT_FAMILY, CJK_FAMILY, "PingFang SC"],
    "win32": [FONT_FAMILY, CJK_FAMILY, "Microsoft YaHei UI", "Microsoft YaHei"],
}
