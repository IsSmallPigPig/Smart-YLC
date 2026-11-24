"""从 Allo 官方可变字体生成 Windows 用的 TrueType 静态字重。

背景：Qt 5 在 Windows 上渲染 CFF/OTF 轮廓会出现毛边，同一份字体的 TrueType
轮廓没有这个问题；macOS 用 CoreText，原 OTF 正常，不必替换。

同时做两处构建期修改（字形轮廓不变）：
  * 把 cmap 的 0-9 指向 `tnum` 替换字形，数字默认等宽（理由见 patch_allo_digits.py）
  * 把行高/基线度量对齐 MiSans，避免与汉字混排时数字上下错位

用法：
    python scripts/build_allo_ttf.py /path/to/AlloVariable.ttf app/assets/fonts
"""

from __future__ import annotations

import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

from patch_allo_digits import tnum_mapping

WEIGHTS = {"Regular": 400, "Medium": 500, "SemiBold": 600, "Bold": 700}
SUFFIX = "-tt"


def _metrics(cjk: TTFont, upm: int) -> dict[str, int]:
    """按 em 把 MiSans 的度量换算到 Allo 的 upm，四舍五入到整数。"""
    scale = upm / cjk["head"].unitsPerEm
    hhea, os2 = cjk["hhea"], cjk["OS/2"]
    return {
        "ascent": round(hhea.ascent * scale),
        "descent": round(hhea.descent * scale),
        "typo_ascent": round(os2.sTypoAscender * scale),
        "typo_descent": round(os2.sTypoDescender * scale),
        "typo_gap": round(os2.sTypoLineGap * scale),
        "win_ascent": round(os2.usWinAscent * scale),
        "win_descent": round(os2.usWinDescent * scale),
    }


def _rename(font: TTFont, weight_name: str) -> None:
    """可变字体报告族名 Allo Variable；改回 Allo，字体栈才匹配得上。"""
    values = {
        1: "Allo",
        2: weight_name,
        4: f"Allo {weight_name}",
        6: f"Allo-{weight_name}",
        16: "Allo",
        17: weight_name,
    }
    table = font["name"]
    for record in list(table.names):
        value = values.get(record.nameID)
        if value:
            table.setName(value, record.nameID, record.platformID,
                          record.platEncID, record.langID)


def build(src: Path, dst_dir: Path, cjk: Path) -> None:
    cjk_font = TTFont(str(cjk))
    for name, weight in WEIGHTS.items():
        font = TTFont(str(src))
        instantiateVariableFont(font, {"wght": weight}, inplace=True, optimize=True)
        _rename(font, name)
        upm = font["head"].unitsPerEm
        mapping = tnum_mapping(font)
        for table in font["cmap"].tables:
            if not table.isUnicode():
                continue
            for code in range(0x30, 0x3A):
                glyph = table.cmap.get(code)
                if glyph and glyph in mapping:
                    table.cmap[code] = mapping[glyph]
        widths = {font["hmtx"][font.getBestCmap()[c]][0] for c in range(0x30, 0x3A)}
        if len(widths) != 1:
            raise SystemExit(f"{name}: 数字宽度仍不一致 {sorted(widths)}")

        m = _metrics(cjk_font, upm)
        hhea, os2 = font["hhea"], font["OS/2"]
        hhea.ascent, hhea.descent = m["ascent"], m["descent"]
        os2.sTypoAscender, os2.sTypoDescender = m["typo_ascent"], m["typo_descent"]
        os2.sTypoLineGap = m["typo_gap"]
        os2.usWinAscent, os2.usWinDescent = m["win_ascent"], m["win_descent"]

        out = dst_dir / f"Allo-{name}{SUFFIX}.ttf"
        font.save(str(out))
        print(f"{out.name}: wght={weight} 数字宽={widths.pop()} 行高={m['ascent']}/{m['descent']}")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip())
        return 2
    build(Path(argv[1]), Path(argv[2]), Path(argv[2]) / "MiSans-Regular.ttf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
