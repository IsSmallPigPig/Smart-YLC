"""视觉 Token 门禁：字号阶梯、圆角、禁用中点分隔符。

这三条是「不会崩、但会让界面越改越乱」的规则。散着写没人拦得住，
所以用测试盯着：新写死的字号与圆角会被直接判红。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication

APP_UI = Path(__file__).resolve().parent.parent / "app" / "ui"
# 首页的欢迎大字按视口比例算字号（构图需要），是唯一允许写死像素的地方；
# 排版保持不变，其余规则照样适用。


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


# 欢迎大字按视口比例算像素（34～60px 之间浮动），不属于阶梯内的固定档位
VIEWPORT_SCALED_FONTS = {"home_page.py"}


def _sources():
    for p in sorted(APP_UI.glob("*.py")):
        yield p, p.read_text(encoding="utf-8")


def test_no_hardcoded_font_pixels():
    """字号必须走 P.FONT，不许在调用点写死像素值。

    原来散落着 12/13/14/15/16/19/22/26/28/30/36 十一种值，同一页里字号互相打架。
    theme_font 的第二个参数写死数字就是回退信号。"""
    bad = []
    pat = re.compile(r"theme_font\([^,]+,\s*\d+")
    for path, src in _sources():
        for n, line in enumerate(src.splitlines(), 1):
            if pat.search(line) and not line.strip().startswith("#"):
                bad.append(f"{path.name}:{n}: {line.strip()}")
    assert not bad, "字号写死了，应改用 P.FONT[...]：\n" + "\n".join(bad)


def test_no_font_below_12px():
    """12px 是硬下限：1080p / 100% 缩放下更小的字笔画会丢。"""
    bad = []
    pat = re.compile(r"setPixelSize\((\d+)\)")
    for path, src in _sources():
        if path.name in VIEWPORT_SCALED_FONTS:
            continue
        for n, line in enumerate(src.splitlines(), 1):
            m = pat.search(line)
            if m and int(m.group(1)) < 12:
                bad.append(f"{path.name}:{n}: {line.strip()}")
    assert not bad, "存在小于 12px 的字号：\n" + "\n".join(bad)


def test_no_hardcoded_border_radius():
    """圆角必须走 P.RADIUS，避免同一层级出现 8/10/12 混写。"""
    bad = []
    pat = re.compile(r"setBorderRadius\((\d+)\)")
    for path, src in _sources():
        for n, line in enumerate(src.splitlines(), 1):
            if pat.search(line):
                bad.append(f"{path.name}:{n}: {line.strip()}")
    assert not bad, "圆角写死了，应改用 P.RADIUS[...]：\n" + "\n".join(bad)


def test_no_middle_dot_separator():
    """界面文案禁用中点「·」：各系统字体下中点基线不一致，看起来忽上忽下。"""
    bad = []
    for path, src in _sources():
        for n, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("--"):
                continue
            # 只查字符串字面量里的中点
            for lit in re.findall(r'"[^"]*"|\'[^\']*\'', line):
                if "·" in lit:
                    bad.append(f"{path.name}:{n}: {lit}")
    assert not bad, "文案里还有中点分隔符：\n" + "\n".join(bad)


def test_font_scale_is_compact(app):
    """阶梯本身保持在 5 档以内，多了就失去分层意义。"""
    from app.ui import palette as P

    assert 4 <= len(set(P.FONT.values())) <= 5
    assert min(P.FONT.values()) >= 12


def test_fonts_are_bundled_and_registered(app):
    """内置字体随程序打包并注册，不依赖系统装了什么。

    枚举系统字体（Consolas / Menlo / ...）在没装的机器上会静默回退成比例字体，
    数字宽度照旧不等，各人机器结果还不一样——等于没修。"""
    from PyQt5.QtGui import QFontDatabase
    from PyQt5.QtWidgets import QLabel

    from app.setup import CJK_FAMILY, FONT_FAMILY, FONT_FILES, register_fonts
    from app.ui.brand import assets_dir
    from app.ui.widgets import tabular_label

    folder = assets_dir() / "fonts"
    for name in FONT_FILES:
        assert (folder / name).is_file(), f"打包缺字体文件：{folder / name}"
    assert (folder / "LICENSE-Allo.txt").is_file(), "字体许可证要随包分发"
    assert (folder / "LICENSE-MiSans.txt").is_file(), "字体许可证要随包分发"

    # 只比名字不够：注册失败时代码会回落成系统族名，这里确认字体真在库里
    assert register_fonts() == FONT_FAMILY
    assert FONT_FAMILY in QFontDatabase().families(), f"字体未注册到 Qt：{FONT_FAMILY}"
    assert CJK_FAMILY in QFontDatabase().families(), f"中文回落字体未注册：{CJK_FAMILY}"
    lbl = tabular_label(QLabel("42/260（16%）"))
    assert lbl.font().family() == FONT_FAMILY


def test_bundled_font_has_monospaced_digits(app):
    """数字必须等宽，字母保持 Allo 的比例字形。

    数字等宽是硬要求：统计值实时跳动时宽度不变，`245 / 260`、`13 / 52 人`
    这类并排数值才成列。字母若被误烧成等宽，说明 cmap 改过头了。
    用字体度量实测，不信字体名字。
    """
    from PyQt5.QtGui import QFont, QFontMetricsF

    from app.setup import CJK_FAMILY, FONT_FAMILY, register_fonts

    assert register_fonts() == FONT_FAMILY
    font = QFont()
    font.setFamilies([FONT_FAMILY, CJK_FAMILY])
    font.setPixelSize(14)
    fm = QFontMetricsF(font)

    digits = [fm.horizontalAdvance(c) for c in "0123456789"]
    assert max(digits) - min(digits) < 0.5, f"数字不等宽：{digits}"

    # 汉字由 MiSans 回落，每个汉字同宽，这是 CJK 排版的基础
    cjk = [fm.horizontalAdvance(c) for c in "团员状况信息错误欠缴"]
    assert max(cjk) - min(cjk) < 0.5, f"中文不等宽：{cjk}"

    latin = {c: fm.horizontalAdvance(c) for c in "iW"}
    assert latin["W"] - latin["i"] > 1.0, f"字母应为比例字形：{latin}"


def test_every_bundled_weight_resolves(app):
    """四个字重都要能精确匹配到文件。

    只注册 Regular 时 Qt 会合成假粗体：笔画发糊，而且字宽与真粗体不同，
    等宽数字的对齐也会跟着歪。
    """
    from PyQt5.QtGui import QFont, QFontDatabase, QFontInfo

    from app.setup import FONT_FAMILY, register_fonts

    assert register_fonts() == FONT_FAMILY
    db = QFontDatabase()
    # 四档字重要落到四个不同的字体文件。判据用字体库给的样式名，不用字宽：
    # 静态字体各字重刻意共用同一字宽，按宽度判断会在真字重齐全的机器上误报。
    styles = set()
    for weight in (QFont.Weight.Normal, QFont.Weight.Medium,
                   QFont.Weight.DemiBold, QFont.Weight.Bold):
        font = QFont(FONT_FAMILY)
        font.setPixelSize(14)
        font.setWeight(weight)
        assert QFontInfo(font).exactMatch(), f"字重 {weight} 没有匹配到字体文件"
        styles.add(db.styleString(font))
    assert "" not in styles and len(styles) == 4, f"字重没有真正生效：{sorted(styles)}"


def test_inner_radius_is_concentric():
    """嵌套圆角按同心圆推导，不是随手减一个数。

    两个圆角的圆心重合时才是同一套语言；把外层半径直接抄给内层，内层弧线
    比外层更靠内、四角看着不平行。内层小到装不下圆角时返回 0。
    """
    from app.ui import palette as P

    assert P.inner_radius(10, 0) == 10
    assert P.inner_radius(10, 4) == 6
    assert P.inner_radius(10, 8) == 2
    # 内边距超过外层半径：装不下，直接直角，别挤出畸形弧
    assert P.inner_radius(10, 18) == 0
    assert P.RADIUS["window"] >= P.RADIUS["card"], "窗口圆角不该小于卡片圆角"


def test_latin_and_cjk_split_between_bundled_fonts(app):
    """字母数字来自 Allo，汉字回落 MiSans——这是字体栈的契约。

    两个族名都注册了不等于栈生效：丢掉族栈后，汉字会静默改用系统中文
    （PingFang / 微软雅黑），同一版程序在各人机器上长得不一样。按渲染像素比对。
    """
    import sys

    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QColor, QFont, QImage, QPainter

    from app.setup import CJK_FAMILY, FONT_FAMILY, FONTS, _apply_font, register_fonts

    assert register_fonts() == FONT_FAMILY
    _apply_font(FONTS[sys.platform])

    def shot(text, font):
        img = QImage(420, 48, QImage.Format_ARGB32)
        img.fill(QColor("white"))
        painter = QPainter(img)
        painter.setFont(font)
        painter.setPen(QColor("black"))
        painter.drawText(img.rect(), Qt.AlignLeft | Qt.AlignVCenter, text)
        painter.end()
        return bytes(img.constBits().asstring(img.byteCount()))

    def single(family):
        font = QFont()
        font.setFamilies([family])
        font.setPixelSize(16)
        return font

    applied = QApplication.font()
    applied.setPixelSize(16)

    assert shot("智慧团委", applied) == shot("智慧团委", single(CJK_FAMILY)), "汉字没有走 MiSans"
    assert shot("Smart 42", applied) == shot("Smart 42", single(FONT_FAMILY)), "字母数字没有走 Allo"
    mixed = shot("团员 42 人", applied)
    assert mixed != shot("团员 42 人", single(FONT_FAMILY))
    assert mixed != shot("团员 42 人", single(CJK_FAMILY))


def test_windows_fonts_are_truetype_and_tabular(app):
    """Windows 用 TrueType 轮廓的 Allo：CFF/OTF 在 Qt5 的 Windows 渲染里出毛边。"""
    from fontTools.ttLib import TTFont

    from app.fonts import WINDOWS_FONT_FILES, font_files, fonts_dir
    from app.ui.brand import assets_dir

    assert font_files("win32") == WINDOWS_FONT_FILES
    assert font_files("darwin") == tuple(
        name for name in font_files("darwin") if name.endswith((".otf", ".ttf")))
    assert "Allo-Regular-tt.ttf" in WINDOWS_FONT_FILES
    assert "Allo-Regular.otf" not in WINDOWS_FONT_FILES

    folder = fonts_dir()
    for name in WINDOWS_FONT_FILES:
        assert (folder / name).is_file(), f"缺 Windows 字体：{name}"

    cjk = TTFont(str(assets_dir() / "fonts" / "MiSans-Regular.ttf"))
    factor = 2048 / cjk["head"].unitsPerEm
    expected = (round(cjk["hhea"].ascent * factor),
                round(cjk["hhea"].descent * factor))
    for name in WINDOWS_FONT_FILES:
        if not name.startswith("Allo-"):
            continue
        font = TTFont(str(folder / name))
        assert "glyf" in font, f"{name} 仍是 CFF 轮廓"
        assert (font["hhea"].ascent, font["hhea"].descent) == expected, \
            f"{name} 行高未对齐 MiSans"
        widths = {font["hmtx"][font.getBestCmap()[c]][0] for c in range(0x30, 0x3A)}
        assert len(widths) == 1, f"{name} 数字不等宽"


def test_windows_disables_subpixel_antialiasing(app, monkeypatch):
    """半透明材质上 ClearType 的彩色子像素会留下彩边，Windows 改灰度抗锯齿。"""
    from types import SimpleNamespace

    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    from app import setup

    monkeypatch.setattr(setup, "sys", SimpleNamespace(platform="win32"))
    setup._apply_font(None)
    strategy = QApplication.font().styleStrategy()
    assert strategy & QFont.StyleStrategy.NoSubpixelAntialias
    assert strategy & QFont.StyleStrategy.PreferAntialias


def test_windows_allo_ttf_registers_as_allo(app):
    """生成的 TTF 必须仍叫 Allo，否则字体栈第一项匹配不上，等于没换。"""
    from PyQt5.QtGui import QFontDatabase

    from app.fonts import FONT_FAMILY, fonts_dir

    fid = QFontDatabase.addApplicationFont(str(fonts_dir() / "Allo-Regular-tt.ttf"))
    assert fid != -1
    assert FONT_FAMILY in QFontDatabase.applicationFontFamilies(fid)
