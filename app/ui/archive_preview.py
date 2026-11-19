"""档案详情预览页：面包屑导航 + PDF 逐页预览 + 结构检查 + 异常标记。

设计约束（用户明确要求）：
* 用面包屑「上传档案 › 姓名+手机号」，不是主窗口级导航；
* 子页面不显示 banner（头图），顶部走常规页边距；
* 顶部有「存在异常」按钮，点开列出这份档案的所有问题；
* 有结构异常的档案不参与上传——本页给出明确结论，不静默拦。

PDF 渲染复用 pymupdf（项目已锁 1.24.11），缩略图用低 dpi 逐页生成，
翻页才渲染当前页，不一次把 13 页全画出来——垃圾 CPU 也能顺滑翻。
"""

from __future__ import annotations

import os
from typing import Callable

from PyQt5.QtCore import Qt, QThread, pyqtSignal as Signal
from PyQt5.QtGui import QFont, QImage, QPixmap
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    ToolButton,
)

from app.services.archive_exam import ArchiveExam, examine
from app.services.form_cells import FormCellCheck, check as check_cells
from app.ui import palette as P
from app.ui.org_manage_page import CrumbBar
from app.ui.page import FluentPage, note_label, theme_font


class _PreviewWorker(QThread):
    """在后台渲染当前页，避免大扫描图把界面卡死。"""
    rendered = Signal(QImage, int)   # image, page number (1-based)
    failed = Signal(str)

    def __init__(self, path: str, page_no: int, dpi: int = 110, parent=None):
        super().__init__(parent)
        self.path, self.page_no, self.dpi = path, page_no, dpi

    def run(self):
        try:
            import pymupdf

            doc = pymupdf.open(self.path)
            if not 1 <= self.page_no <= len(doc):
                raise ValueError(f"页码 {self.page_no} 超出范围（共 {len(doc)} 页）")
            pix = doc[self.page_no - 1].get_pixmap(dpi=self.dpi)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                         QImage.Format.Format_RGB888 if pix.n == 3
                         else QImage.Format.Format_RGBA8888)
            self.rendered.emit(img.copy(), self.page_no)
            doc.close()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class _ExamWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, path: str, expected_pages: int, parent=None):
        super().__init__(parent)
        self.path, self.expected_pages = path, expected_pages

    def run(self):
        try:
            self.done.emit(examine(self.path, self.expected_pages))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class _CellWorker(QThread):
    """逐格检查（OpenCV），单页几十毫秒，整份 0.3~0.7 秒，放后台不挡翻页。"""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            self.done.emit(check_cells(self.path))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class ArchivePreviewView(FluentPage):
    """一份档案的详情页。由 UploadPage 压入 stackedWidget，返回时弹出。"""

    def __init__(self, upload_page, path: str, name: str,
                 expected_pages: int = 0,
                 on_back: Callable[[], None] | None = None,
                 on_mark_abnormal: Callable[[str, bool], None] | None = None):
        super().__init__("page-archive-preview", upload_page)
        self.upload_page = upload_page
        self.path = path
        self.name = name
        self.expected_pages = expected_pages
        self._on_back = on_back or (lambda: None)
        self._on_mark = on_mark_abnormal or (lambda path, bad: None)
        self._page = 1
        self._total = 0
        self._worker: QThread | None = None
        self._abnormal = False
        self._cells: FormCellCheck | None = None
        self._exam_result: ArchiveExam | None = None

        lay = self.vBoxLayout
        lay.setContentsMargins(32, 16, 32, 24)   # 子页面无 banner，顶部常规边距
        lay.setSpacing(10)

        # 面包屑：上传档案 › 姓名（点击首页返回）
        self.crumb = CrumbBar(self)
        self.crumb.addItem("upload", "上传档案")
        self.crumb.addItem("archive", name.replace("+入团志愿书.pdf", "").replace(".pdf", ""))
        self.crumb.currentItemChanged.connect(self._on_crumb)
        lay.addWidget(self.crumb)

        # 标题 + 操作
        head = QHBoxLayout()
        head.setSpacing(8)
        # 标题只显示人，全文件名放在悬停提示里：面包屑已经带了姓名+手机号，
        # 标题再铺一遍 22 个字会把「核对信息」「存在异常」挤到第二行
        short = name.split("+")[0] if "+" in name else name.replace(".pdf", "")
        title = StrongBodyLabel(short, self)
        title.setToolTip(name)
        theme_font(title, P.FONT['title'], int(QFont.Weight.DemiBold))
        title.setWordWrap(True)
        head.addWidget(title, 1)
        self.ocr_btn = PushButton(FIF.INFO, "核对信息", self)
        self.ocr_btn.clicked.connect(self._run_ocr)
        set_tip(self.ocr_btn, "识别封面手写姓名，与文件名和在线名单比对（辅助提示）")
        head.addWidget(self.ocr_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.abnormal_btn = PushButton(FIF.INFO, "存在异常", self)
        self.abnormal_btn.setCheckable(True)
        self.abnormal_btn.clicked.connect(self._toggle_abnormal)
        set_tip(self.abnormal_btn, "标记异常：档案移入「异常档案」子文件夹，不参与上传；再点一次移回原处")
        head.addWidget(self.abnormal_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)

        # 检查结论
        self.exam_label = BodyLabel("正在检查页面结构…", self)
        self.exam_label.setWordWrap(True)
        lay.addWidget(self.exam_label)

        self.cell_label = BodyLabel("正在检查表格空格…", self)
        self.cell_label.setWordWrap(True)
        lay.addWidget(self.cell_label)

        # PDF 预览区：滚动容器里一张 QLabel，翻页时替换
        self.preview_lbl = QLabel(self)
        self.preview_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.preview_lbl.setMinimumSize(360, 520)
        self.preview_lbl.setText("正在渲染第 1 页…")
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.preview_lbl)
        lay.addWidget(self.scroll, 1)

        # 翻页
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.prev_btn = ToolButton(FIF.LEFT_ARROW, self)
        self.prev_btn.setToolTip("上一页")
        self.prev_btn.clicked.connect(lambda: self.goto(self._page - 1))
        self.next_btn = ToolButton(FIF.RIGHT_ARROW, self)
        self.next_btn.setToolTip("下一页")
        self.next_btn.clicked.connect(lambda: self.goto(self._page + 1))
        self.page_label = note_label("第 0 / 0 页")
        nav.addStretch(1)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.page_label)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        lay.addLayout(nav)

        self._start_exam()
        self._start_cell_check()
        self._render()

    # ---- 面包屑 / 返回 ----

    def _on_crumb(self, route_key: str):
        if route_key == "upload":
            self._on_back()

    # ---- 结构检查 ----

    def _start_exam(self):
        self._exam = _ExamWorker(self.path, self.expected_pages, self)
        self._exam.done.connect(self._exam_done)
        self._exam.failed.connect(lambda m: self.exam_label.setText(f"检查失败：{m}"))
        self._exam.start()

    def _exam_done(self, exam: ArchiveExam):
        self._exam_result = exam
        self._total = exam.pages or 0
        self.page_label.setText(f"第 1 / {self._total} 页")
        if exam.ok:
            note = f"结构检查通过：{exam.summary()}"
            if exam.notices:
                note += f"；{len(exam.notices)} 处需人工确认（多为签字页）"
            self.exam_label.setText(note)
        else:
            details = "；".join(f"第{i.page}页 {i.detail}" for i in exam.blocking_issues)
            self.exam_label.setText(f"存在 {len(exam.blocking_issues)} 处异常：{details}")
            # 异常档案默认标记不上传，用户仍可手动取消
            self._set_abnormal(True)

    def _start_cell_check(self):
        self._cells_worker = _CellWorker(self.path, self)
        self._cells_worker.done.connect(self._cells_done)
        self._cells_worker.failed.connect(
            lambda m: self.cell_label.setText(f"表格检查失败：{m}"))
        self._cells_worker.start()

    def _cells_done(self, result: FormCellCheck):
        self._cells = result
        if result.skipped:
            self.cell_label.setText(result.skipped)
        elif result.blanks:
            pages = sorted({c.page for c in result.blanks})
            self.cell_label.setText(
                f"表格里有 {len(result.blanks)} 处空格（第 "
                f"{'、'.join(map(str, pages))} 页已用红框标出），请翻页核对这些格子是否漏填")
        else:
            self.cell_label.setText("表格里没有发现空格")
        # 结果回来时当前页已经画好了，重画一次把红框补上
        self._refresh_overlay()

    def _bank_blanks_of(self, page_no: int):
        if self._cells is None:
            return []
        return self._cells.by_page().get(page_no, [])

    # ---- PDF 渲染 ----

    def goto(self, page_no: int):
        if page_no < 1 or (self._total and page_no > self._total):
            return
        self._page = page_no
        self._render()

    def _render(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self.preview_lbl.setText(f"正在渲染第 {self._page} 页…")
        self._worker = _PreviewWorker(self.path, self._page, parent=self)
        self._worker.rendered.connect(self._show_image)
        self._worker.failed.connect(lambda m: self.preview_lbl.setText(f"渲染失败：{m}"))
        self._worker.start()

    def _show_image(self, img: QImage, page_no: int):
        if page_no != self._page:
            return
        pix = QPixmap.fromImage(img)
        # 窄窗口时缩到可视宽度，避免横向滚动
        avail = max(self.scroll.viewport().width() - 24, 200)
        if pix.width() > avail:
            pix = pix.scaledToWidth(avail, Qt.TransformationMode.SmoothTransformation)
        self._base_pixmap = pix
        self.preview_lbl.setPixmap(self._with_blank_marks(pix, page_no))
        self.page_label.setText(f"第 {page_no} / {self._total or '?'} 页")
        self.prev_btn.setEnabled(page_no > 1)
        self.next_btn.setEnabled(not self._total or page_no < self._total)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 宽度变化后已有图片按比例重缩放，红框要一起重画
        base = getattr(self, "_base_pixmap", None)
        if base is not None:
            avail = max(self.scroll.viewport().width() - 24, 200)
            pix = base
            if pix.width() > avail:
                pix = pix.scaledToWidth(avail, Qt.TransformationMode.SmoothTransformation)
            self._base_pixmap = pix
            self.preview_lbl.setPixmap(self._with_blank_marks(pix, self._page))

    def _refresh_overlay(self):
        base = getattr(self, "_base_pixmap", None)
        if base is not None:
            self.preview_lbl.setPixmap(self._with_blank_marks(base, self._page))

    def _with_blank_marks(self, pix: QPixmap, page_no: int) -> QPixmap:
        """把这一页的空格用红框标出来。坐标从 1.0 倍渲染坐标换算到当前缩放。"""
        blanks = self._bank_blanks_of(page_no)
        if not blanks or self._cells is None:
            return pix
        size = self._cells.page_sizes.get(page_no)
        if not size or not size[0]:
            return pix
        from PyQt5.QtGui import QColor, QPainter, QPen

        ratio = pix.width() / float(size[0])
        out = QPixmap(pix)
        painter = QPainter(out)
        pen = QPen(QColor(216, 58, 58, 220))
        pen.setWidth(2)
        painter.setPen(pen)
        for cell in blanks:
            x, y, w, h = cell.box
            painter.drawRect(int(x * ratio), int(y * ratio),
                             max(2, int(w * ratio)), max(2, int(h * ratio)))
        painter.end()
        return out

    # ---- OCR 信息核对（可选，慢） ----

    def _run_ocr(self):
        from app.services.archive_ocr import compare_name, read_cover

        self.ocr_btn.setEnabled(False)
        self.exam_label.setText("正在识别封面姓名，约需 2-5 秒…")

        def job():
            from app.domain.naming import analyze_file_name

            r = read_cover(self.path)
            expected = analyze_file_name(self.name)
            file_name = expected.parsed[0] if expected.parsed else ""
            same, msg = compare_name(r.name, file_name)
            return same, msg, r.available

        def done(result):
            same, msg, available = result
            self.ocr_btn.setEnabled(True)
            prefix = "信息核对：" if same else "信息待确认："
            self.exam_label.setText(prefix + msg + ("（手写识别可能误识，仅供参考）"
                                                    if not available or not same else ""))

        from PyQt5.QtCore import QThread, pyqtSignal as Signal

        class W(QThread):
            finished = Signal(object)

            def run(self):
                try:
                    self.finished.emit(job())
                except Exception as exc:  # noqa: BLE001
                    self.finished.emit((False, f"核对失败：{exc}", False))

        self._ocr_worker = W(self)
        self._ocr_worker.finished.connect(done)
        self._ocr_worker.start()

    # ---- 异常标记 ----

    def _toggle_abnormal(self, checked: bool):
        self._set_abnormal(checked)

    def _set_abnormal(self, bad: bool):
        self._abnormal = bad
        self.abnormal_btn.setChecked(bad)
        self.abnormal_btn.setText("已标记异常" if bad else "存在异常")
        self._on_mark(self.path, bad)

    def retarget(self, path: str):
        """标记异常会把档案搬进「异常档案」文件夹：路径跟着换，页面不用重渲染。"""
        self.path = path

    @property
    def is_abnormal(self) -> bool:
        return self._abnormal


def set_tip(widget, text: str):
    widget.setToolTip(text)
