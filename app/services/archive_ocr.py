"""档案 OCR 校验：只识别第 1 页的「申请人姓名」，与文件名/在线名单比对。

为什么只查第 1 页：
  参考档案 49 份实测，每页都是纯扫描图（零文本层）。要核对"填写的姓名和
  文件名/在线数据是否一致"，其实只有封面第 1 页有"申请人姓名"字段。
  全页 OCR 没必要——13 页 × 49 份在垃圾 CPU 上要跑一小时；只查封面
  1 页 × 49 份约 2 分钟，且 RapidOCR 对手写姓名实测置信度 0.86。

分层：
  * 结构检查（archive_exam）：默认，毫秒级，判页数/破损/空白页；
  * OCR 校验（本模块）：可选，用户点"核对信息"才跑，判姓名一致性。
  两者都不修改文件。

依赖：rapidocr-onnxruntime（约 30MB 模型，纯 CPU，onnxruntime 推理）。
未安装时给出明确提示，不假装通过。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 封面「申请人姓名」后面跟的是手写姓名；OCR 常把「申请」误认成「人」等，
# 用宽松匹配：包含「姓名」或「人姓名」，后面 2~5 个中文
_NAME_RE = re.compile(r"(?:申请人?姓名|姓名)\s*[：:_]?\s*([\u4e00-\u9fff]{2,5})")
# 发展团员编号（12 位数字）
_CODE_RE = re.compile(r"发展团员编号\s*[：:_]?\s*(\d{10,14})")


@dataclass
class OcrResult:
    page: int = 1
    name: str = ""
    code: str = ""
    texts: list[str] | None = None
    elapsed: float = 0.0
    available: bool = True
    error: str = ""


def _engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def read_cover(path: str, dpi: int = 150) -> OcrResult:
    """只 OCR 封面第 1 页，抽出姓名与发展团员编号。"""
    import time

    result = OcrResult()
    t0 = time.perf_counter()
    try:
        import numpy as np
        import pymupdf

        engine = _engine()
        doc = pymupdf.open(path)
        pix = doc[0].get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        doc.close()
        rows, _ = engine(img)
        result.texts = [str(r[1]) for r in (rows or [])]
        for text in result.texts:
            compact = text.replace(" ", "")
            if not result.name:
                m = _NAME_RE.search(compact)
                if m:
                    result.name = m.group(1)
            if not result.code:
                m = _CODE_RE.search(compact)
                if m:
                    result.code = m.group(1)
    except ImportError:
        result.available = False
        result.error = "缺少 rapidocr-onnxruntime，无法核对填写信息"
    except Exception as exc:  # noqa: BLE001
        result.error = f"OCR 失败：{exc}"
    result.elapsed = time.perf_counter() - t0
    return result


def compare_name(ocr_name: str, file_name: str, member_name: str = "") -> tuple[bool, str]:
    """比对 OCR 姓名、文件名姓名、在线名单姓名。

    返回 (是否一致, 说明)。缺任何一方时给出明确说明，不静默判通过。
    """
    ocr_name, file_name, member_name = (ocr_name.strip(), file_name.strip(),
                                          member_name.strip())
    if not ocr_name:
        # 手写 OCR 不是 100% 可靠，没认出来绝不能当"通过"——否则真正的漏填
        # 会被静默放过。明确要求人工核对。
        return False, "封面姓名未能识别，请人工核对是否漏填"

    pairs = [("文件名", file_name), ("在线名单", member_name)]
    labels = [("档案填写", ocr_name)] + [(k, v) for k, v in pairs if v]
    detail = "; ".join(f"{k}={v or '未识别'}" for k, v in labels)

    # 手写体 OCR 实测有约 1/3 的字误识（"炯"→"回"、"玮"→"王玮"），
    # 且中文姓名 2-3 字时单字错误概率不低。因此严格相等才算通过，
    # 不等只报"疑似不一致"交人工确认，绝不自动判错拦截上传。
    refs = [v for _, v in pairs if v]
    if not refs:
        return True, f"识别到姓名「{ocr_name}」，无可比对来源"
    if all(ocr_name == ref for ref in refs):
        return True, f"姓名一致：{ocr_name}"
    return False, f"疑似不一致（手写 OCR 可能误识）：{detail}"
