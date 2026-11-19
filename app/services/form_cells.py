"""表格逐格检查：找出扫描件表格里没有字的格子。

为什么单独一层：
  档案是纸质件扫描，零文本层。「页数对不对、整页是不是空白」由 archive_exam
  的结构层负责；「姓名/籍贯/电话这些格子填没填」必须看到格子这一级。

做法（纯 CPU，单页约 20~60ms，不需要 OCR 模型）：
  1. 自适应阈值 + 形态学抽出表格线，再用「线掩膜的反向」做连通域，
     每个闭合区域就是一个真实单元格——合并单元格天然是一格，不会切出假空格。
  2. 格子里数「字符连通块」（面积 ≥ 8px 的块）。空白格是 0 块；
     填入一个「1」也有 1 块，所以判据不受字号粗细影响。
  3. 只有同时满足这三条才算可疑空格：
       · 本格 0 块；
       · 同一行还有其他有字的格（否则是整行备用行）；
       · 本格下方还有有字的格（否则是表尾的空白备用行）。
  4. 结果只作提示：扫描件的倾斜、透印、光斑都可能造成误判，
     由人在预览页一眼核对，不自动判异常、不拦截上传。

设计原则与 archive_exam 一致：只读、不修改文件、每条结论都带坐标。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# 渲染倍率。1.0 时 A4 约 595x842：字符高度 10~20px，够数块，
# 单页 20~60ms，垃圾 CPU 也能接受。
_SCALE = 1.0
# 自适应阈值的邻域与偏置（OpenCV 惯例参数，25/15 对扫描件稳定）
_ADAPTIVE_BLOCK = 25
_ADAPTIVE_C = 15
# 视为「字符块」的最小面积。8px 以下基本是扫描噪点与线条残渣
_MIN_BLOB_AREA = 8
# 小于这个尺寸的闭合区域是线条交叉/碎片，不是格子
_MIN_CELL_W = 18
_MIN_CELL_H = 10
# 页面上至少这么多格才认为是表格页，否则跳过（正文页、封面页）
_MIN_CELLS_PER_PAGE = 6
# 每份档案只看前几页：主信息表在前 4 页，后面的志愿正文是整段手写
_MAX_PAGES = 4


@dataclass
class BlankCell:
    page: int                       # 1-based
    box: tuple[int, int, int, int]  # (x, y, w, h)，页面渲染坐标（_SCALE 倍）
    row: int                        # 同一页内自上而下的行序号（1-based）
    col: int                        # 同一行自左向右的格序号（1-based）

    @property
    def center(self) -> tuple[int, int]:
        x, y, w, h = self.box
        return x + w // 2, y + h // 2


@dataclass
class FormCellCheck:
    path: str
    name: str
    pages: int = 0                  # PDF 总页数
    checked_pages: list[int] = field(default_factory=list)
    # 每页渲染尺寸：{页码: (宽, 高)}。预览页按 pixmap 宽度换算画框比例
    page_sizes: dict[int, tuple[int, int]] = field(default_factory=dict)
    blanks: list[BlankCell] = field(default_factory=list)
    elapsed: float = 0.0
    skipped: str = ""               # 跳过原因（未装 opencv 等），空表示真的检查过

    @property
    def ok(self) -> bool:
        return not self.blanks

    def by_page(self) -> dict[int, list[BlankCell]]:
        out: dict[int, list[BlankCell]] = {}
        for cell in self.blanks:
            out.setdefault(cell.page, []).append(cell)
        return out

    def summary(self) -> str:
        if self.skipped:
            return self.skipped
        if not self.blanks:
            return "表格未发现空格"
        pages = sorted({c.page for c in self.blanks})
        return f"{len(self.blanks)} 处空格（第 {'、'.join(map(str, pages))} 页）"


def _line_mask(bw, width: int, height: int):
    """只保留表格线：长横线与长竖线。"""
    import cv2

    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, width // 20), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, height // 20)))
    return (cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk)
            | cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk))


def _cells(bw, lines) -> list[dict]:
    """闭合区域 = 单元格；顺带数出格内的字符块数量。"""
    import cv2
    import numpy as np

    free = cv2.bitwise_not(cv2.dilate(lines, np.ones((3, 3), np.uint8)))
    total, _labels, stats, _cent = cv2.connectedComponentsWithStats(
        free, connectivity=4)
    h_img, w_img = free.shape
    cells: list[dict] = []
    for i in range(1, total):
        x, y, w, h, _area = stats[i]
        # 触到画面边缘的是纸外背景，不是格子
        if x <= 1 or y <= 1 or x + w >= w_img - 1 or y + h >= h_img - 1:
            continue
        if w < _MIN_CELL_W or h < _MIN_CELL_H:
            continue
        # 整页外框也算闭合区域，它不是表格格子：面积超过整页 35% 的一律排除，
        # 否则行分组会被它撑乱（行号列号全错）。
        if w * h > w_img * h_img * 0.35:
            continue
        inner = bw[y + 4:y + h - 4, x + 4:x + w - 4]
        if inner.size == 0:
            continue
        count, _l, bstats, _c = cv2.connectedComponentsWithStats(
            inner, connectivity=8)
        blobs = 0
        for k in range(1, count):
            bw_, bh_, ba = bstats[k][2], bstats[k][3], bstats[k][4]
            if ba < _MIN_BLOB_AREA or bw_ <= 1 or bh_ <= 1:
                continue
            blobs += 1
        cells.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                      "blobs": blobs})
    return cells


def _row_index(cells: list[dict]) -> dict[int, int]:
    """按 y 给格子分行：行高的一半作为容差，兼容行高不齐的扫描件。"""
    order = sorted(cells, key=lambda c: c["y"])
    rows: list[list[dict]] = []
    for cell in order:
        cy = cell["y"] + cell["h"] / 2
        if rows:
            last = rows[-1]
            ref = last[0]["y"] + last[0]["h"] / 2
            if abs(cy - ref) <= max(12, last[0]["h"] * 0.6):
                last.append(cell)
                continue
        rows.append([cell])
    index: dict[int, int] = {}
    for ridx, group in enumerate(rows, start=1):
        for cell in group:
            index[id(cell)] = ridx
    return index


def check_page(gray, page_no: int = 1) -> list[BlankCell]:
    """检查一页的表格空格。gray 是灰度 numpy 数组（HxW）。"""
    import cv2

    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY_INV,
                               _ADAPTIVE_BLOCK, _ADAPTIVE_C)
    lines = _line_mask(bw, gray.shape[1], gray.shape[0])
    cells = _cells(bw, lines)
    if len(cells) < _MIN_CELLS_PER_PAGE:
        return []

    rows = _row_index(cells)
    by_row: dict[int, list[dict]] = {}
    for cell in cells:
        by_row.setdefault(rows.get(id(cell), 0), []).append(cell)
    for group in by_row.values():
        group.sort(key=lambda c: c["x"])

    # 备用行（表格自带的空白填写行）成串出现：同一列的上下相邻格都是空的。
    # 真正的漏填是孤立的——这一格空着，但同一列下方还有内容。
    # 因此只保留「下方同列有内容」的空格，成串的空白块整段丢弃。
    blanks_raw: list[tuple[int, int, dict]] = []
    for ridx, group in sorted(by_row.items()):
        if not any(c["blobs"] >= 1 for c in group):
            continue                      # 整行空：备用行
        for col, cell in enumerate(group, start=1):
            if cell["blobs"] >= 1:
                continue
            blanks_raw.append((ridx, col, cell))

    blanks: list[BlankCell] = []
    for ridx, col, cell in blanks_raw:
        cx0, cx1 = cell["x"], cell["x"] + cell["w"]
        same_col_below = [
            c for c in cells
            if c["blobs"] >= 1 and c["y"] > cell["y"] + cell["h"] * 0.5
            and not (c["x"] + c["w"] < cx0 + 8 or c["x"] > cx1 - 8)]
        if not same_col_below:
            continue                      # 下方同列没有内容：备用行的空白块
        blanks.append(BlankCell(page=page_no,
                                box=(cell["x"], cell["y"],
                                     cell["w"], cell["h"]),
                                row=ridx, col=col))
    return blanks


def check(path: str, max_pages: int = _MAX_PAGES) -> FormCellCheck:
    """检查档案前几页的表格空格。只读，不改文件。"""
    import time

    t0 = time.perf_counter()
    result = FormCellCheck(path=path, name=os.path.basename(path))
    try:
        import numpy as np
        import pymupdf
    except Exception:                        # 缺 PyMuPDF 时整份跳过
        result.skipped = "缺少 PDF 渲染组件"
        return result
    try:
        import cv2  # noqa: F401 - 只用来判断装没装
    except Exception:
        result.skipped = "未安装 OpenCV，跳过逐格检查"
        result.elapsed = time.perf_counter() - t0
        return result

    try:
        doc = pymupdf.open(path)
    except Exception:
        result.skipped = "PDF 无法打开"
        result.elapsed = time.perf_counter() - t0
        return result

    try:
        result.pages = len(doc)
        for pno in range(min(len(doc), max_pages)):
            pix = doc[pno].get_pixmap(matrix=pymupdf.Matrix(_SCALE, _SCALE),
                                      colorspace=pymupdf.csGRAY)
            if pix.width == 0 or pix.height == 0:
                continue
            gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width)
            result.checked_pages.append(pno + 1)
            result.page_sizes[pno + 1] = (pix.width, pix.height)
            result.blanks.extend(check_page(gray, pno + 1))
    finally:
        doc.close()

    result.elapsed = time.perf_counter() - t0
    return result


def page_image(path: str, page_no: int, scale: float = _SCALE):
    """渲染某一页为 PIL 图像，供预览页画框。失败返回 None。"""
    try:
        import pymupdf
        from PIL import Image

        doc = pymupdf.open(path)
        try:
            pix = doc[page_no - 1].get_pixmap(
                matrix=pymupdf.Matrix(scale, scale))
            return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        finally:
            doc.close()
    except Exception:
        return None
