"""档案结构体检与预览页的回归测试。

用真实参考档案目录（若存在）做端到端；否则用程序生成的 PDF 做阳性/阴性对照。
"""

from __future__ import annotations

import pathlib

import pytest

from app.services import archive_exam as ae


REF_DIR = pathlib.Path("/Users/ankoqaq/Downloads/Archives/124级高三入团积极分子")


def _make_pdf(tmp_path, pages: list[str], dark: bool = False,
              blank_pages: tuple[int, ...] = ()):
    """生成和参考档案同形状的扫描件：每页一张图，而不是文字页。

    dark=True 时整份近乎纯白，模拟空白签字页；blank_pages 指定个别空白页（1-based）。
    """
    import numpy as np
    from PIL import Image
    import pymupdf

    doc = pymupdf.open()
    for idx, text in enumerate(pages):
        arr = np.full((900, 640, 3), 255, dtype=np.uint8)
        # 模拟真实扫描的轻微噪声：纯白页 std 约 3（参考档案签字页实测）
        arr = np.clip(arr.astype(int) + np.random.RandomState(idx).randint(-6, 3, arr.shape), 0, 255).astype(np.uint8)
        if not dark and (idx + 1) not in blank_pages:
            # 画一点深色内容，让灰度标准差高于空白阈值；不同页内容不同
            arr[100:100 + 40 + idx * 10, 80:400] = 40
        img = Image.fromarray(arr)
        buf = __import__("io").BytesIO()
        img.save(buf, format="JPEG", quality=85)
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=buf.getvalue())
    path = tmp_path / "sample.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def test_exam_ok_normal_pdf(tmp_path):
    path = _make_pdf(tmp_path, ["第1页", "第2页"])
    r = ae.examine(path, expected_pages=2)
    assert r.ok and r.pages == 2 and not r.blocking_issues


def test_exam_catches_page_count_mismatch(tmp_path):
    path = _make_pdf(tmp_path, ["第1页", "第2页"])
    r = ae.examine(path, expected_pages=3)
    assert not r.ok
    assert any(i.page == 0 and i.kind == "broken" and "页数" in i.detail
               for i in r.blocking_issues)


def test_exam_catches_blank_page_as_notice(tmp_path):
    path = _make_pdf(tmp_path, ["有内容", "空白"], dark=True)
    r = ae.examine(path, expected_pages=2)
    # 近乎空白只提示，不拦截（签字页常见）
    assert r.ok
    assert any(i.page == 2 and i.kind == "blank" and not i.blocking
               for i in r.notices)


def test_exam_ignores_blank_tail_pages(tmp_path):
    """13 页档案的第 11–13 页是备注与说明，近乎空白也不提示。"""
    path = _make_pdf(tmp_path, [f"第{i}页" for i in range(1, 14)],
                     blank_pages=(11, 12, 13))
    r = ae.examine(path, expected_pages=13)
    assert r.ok
    assert not any(i.kind == "blank" for i in r.issues)

    # 正常正文页空白仍要提示：不能把「末尾页例外」变成整份都不查
    path2 = _make_pdf(tmp_path, [f"第{i}页" for i in range(1, 14)],
                      blank_pages=(5,))
    r2 = ae.examine(path2, expected_pages=13)
    assert any(i.page == 5 and i.kind == "blank" for i in r2.notices)


def test_exam_catches_unreadable(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf")
    r = ae.examine(str(path))
    assert not r.ok
    assert any(i.kind == "unreadable" for i in r.blocking_issues)


def test_exam_does_not_touch_file(tmp_path):
    path = _make_pdf(tmp_path, ["第1页"])
    before = pathlib.Path(path).read_bytes()
    ae.examine(path)
    assert pathlib.Path(path).read_bytes() == before


def test_preview_view_builds(qapp_session, tmp_path):
    """预览页能构造：面包屑、无 banner、异常按钮、翻页控件齐全。"""
    from app.ui.archive_preview import ArchivePreviewView

    path = _make_pdf(tmp_path, ["第1页", "第2页"])
    host = __import__("PyQt5.QtWidgets", fromlist=["QWidget"]).QWidget()
    host.resize(900, 700)
    view = ArchivePreviewView(host, path, "张三+13800000000+入团志愿书.pdf",
                              expected_pages=2)
    assert view.crumb is not None
    assert view.abnormal_btn.text() == "存在异常"
    assert view.is_abnormal is False
    # 子页面无 banner：布局顶部边距是常规 16，不是 PageBanner.HEIGHT+16
    assert view.vBoxLayout.contentsMargins().top() == 16
    view.deleteLater(); host.deleteLater()


def test_ocr_compare_name_strict():
    """手写 OCR 有误识风险：空识别与疑似不一致都不能判通过。"""
    from app.services.archive_ocr import compare_name

    ok, msg = compare_name("任思澳", "任思澳")
    assert ok and "一致" in msg
    ok, msg = compare_name("", "任思澳")
    assert not ok and "人工核对" in msg
    ok, msg = compare_name("刘回延", "刘炯延")
    assert not ok and "疑似不一致" in msg
    # 无比对来源时按识别结果提示，不判错
    ok, msg = compare_name("任思澳", "")
    assert ok
