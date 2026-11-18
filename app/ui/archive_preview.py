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
        title = StrongBodyLabel(name, self)
        theme_font(title, P.FONT['title'], int(QFont.Weight.DemiBold))
        title.setWordWrap(True)
        head.addWidget(title, 1)
        self.abnormal_btn = PushButton(FIF.INFO, "存在异常", self)
        self.abnormal_btn.setCheckable(True)
        self.abnormal_btn.clicked.connect(self._toggle_abnormal)
        set_tip(self.abnormal_btn, "标记为异常后，这份档案不参与上传")
        head.addWidget(self.abnormal_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)

        # 检查结论
        self.exam_label = BodyLabel("正在检查页面结构…", self)
        self.exam_label.setWordWrap(True)
        lay.addWidget(self.exam_label)

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
        self.preview_lbl.setPixmap(pix)
        self.page_label.setText(f"第 {page_no} / {self._total or '?'} 页")
        self.prev_btn.setEnabled(page_no > 1)
        self.next_btn.setEnabled(not self._total or page_no < self._total)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 宽度变化后已有图片按比例重缩放
        pix = self.preview_lbl.pixmap()
        if pix is not None:
            avail = max(self.scroll.viewport().width() - 24, 200)
            if pix.width() > avail:
                self.preview_lbl.setPixmap(
                    pix.scaledToWidth(avail, Qt.TransformationMode.SmoothTransformation))

    # ---- 异常标记 ----

    def _toggle_abnormal(self, checked: bool):
        self._set_abnormal(checked)

    def _set_abnormal(self, bad: bool):
        self._abnormal = bad
        self.abnormal_btn.setChecked(bad)
        self.abnormal_btn.setText("已标记异常" if bad else "存在异常")
        self._on_mark(self.path, bad)

    @property
    def is_abnormal(self) -> bool:
        return self._abnormal


def set_tip(widget, text: str):
    widget.setToolTip(text)
