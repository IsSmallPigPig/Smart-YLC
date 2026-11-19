"""PDF 批量压缩引擎（PyMuPDF 光栅化 + Pillow JPEG 迭代）。

策略：整页渲染为 JPEG 重建 PDF，按 (DPI, 质量) 梯度迭代，直到 ≤ 目标字节。
站点硬限 10MB，默认目标 9.5MB 留余量。
"""

from __future__ import annotations

import io
import os

import pymupdf
from PIL import Image

from app.constants import LIMITS

MAX_ITERS = LIMITS["MAX_COMPRESS_ITERATIONS"]
MIN_DPI = LIMITS["MIN_DPI"]
MIN_QUALITY = LIMITS["MIN_JPEG_QUALITY"]

# (dpi, quality) 从高到低；命中目标即停
SCHEDULE = [
    (150, 80), (150, 70), (132, 70), (120, 65), (110, 60),
    (96, 60), (96, 50), (84, 50), (72, 45), (MIN_DPI, 40), (MIN_DPI, MIN_QUALITY),
]


def compress_pdf(src: str, dst: str, target_bytes: int = LIMITS["PDF_DEFAULT_TARGET_BYTES"],
                 progress=None) -> dict:
    """压缩 src → dst。返回 {src_size, dst_size, dpi, quality, iterations}。"""
    src_size = os.path.getsize(src)
    if src_size <= target_bytes:
        # 已达标：原样复制，不动文件
        with open(src, "rb") as fi, open(dst, "wb") as fo:
            fo.write(fi.read())
        return {"src_size": src_size, "dst_size": src_size, "dpi": 0, "quality": 0, "iterations": 0,
                "skipped": True}

    doc = pymupdf.open(src)
    best: tuple[bytes, int, int] | None = None

    for i, (dpi, quality) in enumerate(SCHEDULE[:MAX_ITERS]):
        out = pymupdf.open()
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality, optimize=True)
            jpeg = buf.getvalue()
            sp = out.new_page(width=page.rect.width, height=page.rect.height)
            sp.insert_image(sp.rect, stream=jpeg)
        data = out.tobytes(deflate=True, garbage=4)
        out.close()
        if progress:
            progress(i + 1, len(SCHEDULE))
        best = (data, dpi, quality)
        if len(data) <= target_bytes:
            break
    doc.close()

    assert best is not None
    data, dpi, quality = best
    with open(dst, "wb") as fo:
        fo.write(data)
    return {
        "src_size": src_size,
        "dst_size": len(data),
        "dpi": dpi,
        "quality": quality,
        "iterations": i + 1,
        "skipped": False,
        "over": len(data) > target_bytes,
    }
