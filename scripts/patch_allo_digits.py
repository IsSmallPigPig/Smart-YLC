"""把 Allo 的数字改成默认等宽（tabular figures）。

Allo 的 `tnum` 特性需要调用方开启，而 PyQt5 5.15 没有 `QFont.setFeature`。
这里在构建期把 cmap 的 U+0030–U+0039 指向 `tnum` 替换后的字形，默认就是等宽，
不依赖运行时特性开关。字形轮廓不动。

用法（需 fontTools，且已解压官方 Allo-1.0.3.zip）：
    python scripts/patch_allo_digits.py /path/to/Allo-1.0.3/fonts/otf app/assets/fonts
"""

from __future__ import annotations

import sys
from pathlib import Path

WEIGHTS = ("Regular", "Medium", "SemiBold", "Bold")


def tnum_mapping(font) -> dict[str, str]:
    """tnum 特性里的 单替换 映射（glyph → 替身）。"""
    gsub = font["GSUB"].table
    indices: set[int] = set()
    for record in gsub.FeatureList.FeatureRecord:
        if record.FeatureTag == "tnum":
            indices.update(record.Feature.LookupListIndex)
    mapping: dict[str, str] = {}
    for index in sorted(indices):
        for sub in gsub.LookupList.Lookup[index].SubTable:
            sub = getattr(sub, "ExtSubTable", sub)
            if hasattr(sub, "mapping"):
                mapping.update(sub.mapping)
    return mapping


def patch(src: Path, dst: Path) -> None:
    from fontTools.ttLib import TTFont

    font = TTFont(src)
    mapping = tnum_mapping(font)
    hmtx = font["hmtx"]
    widths = set()
    for table in font["cmap"].tables:
        if not table.isUnicode():
            continue
        for code in range(0x30, 0x3A):
            glyph = table.cmap.get(code)
            if glyph and glyph in mapping:
                table.cmap[code] = mapping[glyph]
    for code in range(0x30, 0x3A):
        glyph = font.getBestCmap()[code]
        widths.add(hmtx[glyph][0])
    if len(widths) != 1:
        raise SystemExit(f"{src.name}: 数字宽度仍不一致 {sorted(widths)}")
    font.save(dst)
    print(f"{dst.name}: 数字等宽 {widths.pop()} units")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1])
        return 2
    src_dir, dst_dir = Path(argv[1]), Path(argv[2])
    for weight in WEIGHTS:
        src = src_dir / f"Allo-{weight}.otf"
        if not src.is_file():
            raise SystemExit(f"缺少 {src}")
        patch(src, dst_dir / src.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
