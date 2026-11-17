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


def test_mono_font_is_bundled_and_registered(app):
    """等宽数字字体随程序打包并注册，不依赖系统装了什么。

    枚举系统字体（Consolas / Menlo / ...）在没装的机器上会静默回退成比例字体，
    数字宽度照旧不等，各人机器结果还不一样——等于没修。"""
    from app.ui.brand import assets_dir
    from app.ui.widgets import mono_family, tabular_label
    from PyQt5.QtWidgets import QLabel

    font_file = assets_dir() / "fonts" / "JetBrainsMono-Regular.ttf"
    assert font_file.is_file(), f"打包缺字体文件：{font_file}"
    assert (assets_dir() / "fonts" / "OFL.txt").is_file(), "字体许可证要随包分发"

    from PyQt5.QtGui import QFontDatabase

    family = mono_family()
    # 只比名字不够：注册失败时代码会回落成同名默认值，这里确认字体真在库里
    assert family in QFontDatabase().families(), f"字体未注册到 Qt：{family}"
    lbl = tabular_label(QLabel("42/260（16%）"))
    assert lbl.font().family() == family
