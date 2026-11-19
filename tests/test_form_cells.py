"""表格逐格检查的单元测试。

用 pymupdf 画一个真表格（画线 + 写字），复现扫描件的两种情况：
  · 某一格空着、同列下方还有内容 → 这就是漏填，必须报出来；
  · 表格自带的空白填写行（连续空行）→ 不能误报。
"""

from __future__ import annotations

import pytest


def _make_form(path, rows: list[list[str | None]]):
    """画一个 rows x 2 的表格，None 表示这一格留空。"""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    x0, x1, x2 = 60, 240, 480
    top, row_h = 120, 46
    bottom = top + row_h * len(rows)

    for i in range(len(rows) + 1):
        y = top + row_h * i
        page.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x2, y),
                       color=(0, 0, 0), width=1.2)
    for x in (x0, x1, x2):
        page.draw_line(pymupdf.Point(x, top), pymupdf.Point(x, bottom),
                       color=(0, 0, 0), width=1.2)

    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            if not text:
                continue
            left = (x0, x1)[0] if c == 0 else x1
            page.insert_text((left + 12, top + row_h * r + 30), text,
                             fontsize=13, fontname="china-s")
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.mark.skipif(__import__("importlib").util.find_spec("cv2") is None,
                    reason="未安装 OpenCV")
def test_flags_blank_cell_above_filled_content(tmp_path):
    """第一行第二格空着、下面同列有内容 → 报一处空格。"""
    from app.services.form_cells import check

    path = _make_form(tmp_path / "form.pdf", [
        ["姓 名", None],
        ["性别", "女"],
        ["民族", "汉族"],
        [None, None],
        [None, None],
    ])
    result = check(path)
    assert len(result.blanks) == 1
    blank = result.blanks[0]
    assert blank.page == 1
    assert blank.row == 1 and blank.col == 2
    x, y, w, h = blank.box
    assert x >= 235 and w > 0 and h > 0


@pytest.mark.skipif(__import__("importlib").util.find_spec("cv2") is None,
                    reason="未安装 OpenCV")
def test_padding_rows_are_not_reported(tmp_path):
    """表格自带的连续空白行不能当成漏填。"""
    from app.services.form_cells import check

    path = _make_form(tmp_path / "form.pdf", [
        ["姓 名", "张三"],
        ["性 别", "男"],
        [None, None],
        [None, None],
        [None, None],
    ])
    assert check(path).blanks == []


@pytest.mark.skipif(__import__("importlib").util.find_spec("cv2") is None,
                    reason="未安装 OpenCV")
def test_fully_filled_form_is_clean(tmp_path):
    from app.services.form_cells import check

    path = _make_form(tmp_path / "form.pdf", [
        ["姓 名", "张三"],
        ["性 别", "男"],
        ["民 族", "汉族"],
    ])
    result = check(path)
    assert result.blanks == []
    assert "未发现空格" in result.summary()


def test_missing_file_is_skipped_not_crashed(tmp_path):
    from app.services.form_cells import check

    result = check(str(tmp_path / "nope.pdf"))
    assert result.skipped
    assert result.ok


def test_summary_lists_pages():
    from app.services.form_cells import BlankCell, FormCellCheck

    result = FormCellCheck(path="x.pdf", name="x.pdf")
    result.blanks = [BlankCell(page=3, box=(0, 0, 10, 10), row=1, col=2),
                     BlankCell(page=4, box=(0, 0, 10, 10), row=1, col=2)]
    assert "2 处空格" in result.summary()
    assert "3、4" in result.summary()
    assert set(result.by_page()) == {3, 4}
