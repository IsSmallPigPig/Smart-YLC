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

from typing import Callable

from PyQt5.QtCore import QEvent, Qt, QThread, QTimer, pyqtSignal as Signal
from PyQt5.QtGui import QFont, QImage, QPixmap
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QScrollArea
from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
        PushButton,
    StrongBodyLabel,
    ToolButton,
)

from app.services.archive_exam import ArchiveExam, examine
from app.services.form_cells import FormCellCheck, check as check_cells
from app.ui import palette as P
from app.ui.org_manage_page import CrumbBar
from app.ui.page import FluentPage, note_label, theme_font


# 缩放档位：1.0 = 整页适配视口，往上放大、往下缩小。
# 上限 4 倍足够看清 13 页扫描件上的手写字；再大只是马赛克。
_MIN_ZOOM = 0.6
_MAX_ZOOM = 4.0
_ZOOM_STEP = 1.25


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
                 on_mark_abnormal: Callable[[str, bool], None] | None = None,
                 on_ignore_blanks: Callable[[str], None] | None = None,
                 blanks_ignored: bool = False):
        super().__init__("page-archive-preview", upload_page)
        self.upload_page = upload_page
        self.path = path
        self.name = name
        self.expected_pages = expected_pages
        self._on_back = on_back or (lambda: None)
        self._on_mark = on_mark_abnormal or (lambda path, bad: None)
        self._on_ignore_blanks = on_ignore_blanks or (lambda path: None)
        self._blanks_ignored = bool(blanks_ignored)
        self._page = 1
        self._total = 0
        self._worker: QThread | None = None
        self._abnormal = False
        self._cells: FormCellCheck | None = None
        self._zoom = 1.0            # 相对「整页适配」的倍数
        # 缩放后要等布局跑完才能摆滚动位置。用挂在页面下的 QTimer 而不是
        # QTimer.singleShot(lambda: ...)：页面先被销毁时，singleShot 仍会回调到
        # 已释放的控件上，直接段错误（测试里连跑就会踩到）。
        self._pending_scroll: tuple[float, float] | None = None
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._restore_pending_scroll)
        self._exam_result: ArchiveExam | None = None

        lay = self.vBoxLayout
        lay.setContentsMargins(32, 16, 32, 24)   # 子页面无 banner，顶部常规边距
        lay.setSpacing(10)

        # 面包屑：上传档案 › 姓名。子页面不挂 banner，返回只走这一条
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
        set_tip(self.abnormal_btn, "标记异常：把「压缩」里的副本挪到工作目录的「异常」，不再上传；再点一次移回，原件始终不动")
        head.addWidget(self.abnormal_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)

        # 检查结论
        self.exam_label = BodyLabel("正在检查页面结构…", self)
        self.exam_label.setWordWrap(True)
        lay.addWidget(self.exam_label)

        cell_row = QHBoxLayout()
        cell_row.setSpacing(8)
        self.cell_label = BodyLabel("正在检查表格空格…", self)
        self.cell_label.setWordWrap(True)
        cell_row.addWidget(self.cell_label, 1)
        # 漏填只是提示，不参与上传判断；不想看到可以关掉
        self.ignore_blanks_btn = PushButton("忽略漏填", self)
        self.ignore_blanks_btn.setToolTip("只隐藏这条提示，不影响上传")
        self.ignore_blanks_btn.clicked.connect(self._ignore_blanks)
        self.ignore_blanks_btn.hide()
        cell_row.addWidget(self.ignore_blanks_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(cell_row)

        # PDF 预览区：滚动容器里一张 QLabel，翻页时替换
        self.preview_lbl = QLabel(self)
        self.preview_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.preview_lbl.setMinimumSize(360, 520)
        self.preview_lbl.setText("正在渲染第 1 页…")
        self.scroll = QScrollArea(self)
        # 不用 widgetResizable：它会把内容强行塞回视口大小，放大后滚动条
        # 永远没有范围。改成由 label 自己的尺寸决定内容大小，小于视口时居中。
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.preview_lbl)
        # 滚动视口默认用 palette 的 base 填底（深色主题下是一整块近黑），
        # 浅色窗口里看着像另一套皮肤；让它跟着窗口底色走
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.viewport().setStyleSheet("background: transparent")
        # 滚轮缩放要拦在视口上：默认 wheel 会被 QScrollArea 拿去滚内容
        self.scroll.viewport().installEventFilter(self)
        self.preview_lbl.installEventFilter(self)
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
        self.page_label.setWordWrap(False)   # 换行会把「第 1 / 13 页」拆成两行
        self.zoom_out_btn = ToolButton(FIF.ZOOM_OUT, self)
        self.zoom_out_btn.setToolTip("缩小（Ctrl + 滚轮，或双击页面切换）")
        self.zoom_out_btn.clicked.connect(lambda: self.set_zoom(self._zoom / _ZOOM_STEP))
        self.zoom_label = note_label("100%")
        self.zoom_label.setWordWrap(False)
        self.zoom_label.setMinimumWidth(44)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_in_btn = ToolButton(FIF.ZOOM_IN, self)
        self.zoom_in_btn.setToolTip("放大（Ctrl + 滚轮，或双击页面切换）")
        self.zoom_in_btn.clicked.connect(lambda: self.set_zoom(self._zoom * _ZOOM_STEP))
        self.fit_btn = ToolButton(FIF.FIT_PAGE, self)
        self.fit_btn.setToolTip("适应窗口")
        self.fit_btn.clicked.connect(lambda: self.set_zoom(1.0))
        nav.addStretch(1)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.page_label)
        nav.addWidget(self.next_btn)
        nav.addSpacing(18)
        for w in (self.zoom_out_btn, self.zoom_label, self.zoom_in_btn, self.fit_btn):
            nav.addWidget(w)
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
            # 结构异常默认标记不上传，用户仍可手动取消
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
            self.ignore_blanks_btn.hide()
        elif result.blanks:
            count = len(result.blanks)
            if self._blanks_ignored:
                self.cell_label.setText(f"已忽略 {count} 处空格，不影响上传")
                self.ignore_blanks_btn.hide()
            else:
                pages = sorted({c.page for c in result.blanks})
                self.cell_label.setText(
                    f"表格里有 {count} 处空格（第 "
                    f"{'、'.join(map(str, pages))} 页已用红框标出），"
                    "请翻页核对这些格子是否漏填；漏填不影响上传")
                self.ignore_blanks_btn.show()
        else:
            self.cell_label.setText("表格里没有发现空格")
            self.ignore_blanks_btn.hide()
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

    def _fit_size(self, full: QPixmap) -> tuple[int, int]:
        """整页适配视口时的尺寸（即 1.0 倍）。"""
        vp = self.scroll.viewport()
        w, h = vp.width() - 24, vp.height() - 24
        if w < 300 or h < 300:
            # 页面刚压进栈、还没走完第一次布局：视口是默认小尺寸，
            # 照它缩会得到一张 240px 宽的缩略图。退回用页面自身几何估。
            w, h = self.width() - 64, self.height() - 200
        w, h = max(w, 240), max(h, 300)
        scale = min(1.0, w / max(1, full.width()), h / max(1, full.height()))
        return max(1, int(full.width() * scale)), max(1, int(full.height() * scale))

    def _render_display(self):
        """按当前缩放把原图缩到显示尺寸。

        始终从原图缩放，不在已经缩小过的图上二次缩放——那样放大只会糊。
        """
        full = getattr(self, "_full_pixmap", None)
        if full is None or full.isNull():
            return
        fw, fh = self._fit_size(full)
        tw = max(1, int(round(fw * self._zoom)))
        th = max(1, int(round(fh * self._zoom)))
        if (tw, th) == (full.width(), full.height()):
            pix = full
        else:
            pix = full.scaled(tw, th, Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        self._base_pixmap = pix
        self.preview_lbl.setFixedSize(pix.width(), pix.height())
        self.preview_lbl.setPixmap(self._with_blank_marks(pix, self._page))
        self._sync_zoom_ui()

    def _sync_zoom_ui(self):
        fitted = self._zoom <= 1.001
        # 百分比恒定显示，不写「适应」——右边那个 FIT_PAGE 按钮已经是这个意思，
        # 两个都写「适应」看着像重复控件
        self.zoom_label.setText(f"{round(self._zoom * 100)}%")
        self.zoom_label.setToolTip("相对「适应窗口」的缩放比例")
        self.zoom_out_btn.setEnabled(self._zoom > _MIN_ZOOM + 1e-3)
        self.zoom_in_btn.setEnabled(self._zoom < _MAX_ZOOM - 1e-3)
        self.fit_btn.setEnabled(not fitted)

    def set_zoom(self, zoom: float):
        """切缩放，并让视口中心仍然落在同一块内容上。"""
        zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, float(zoom)))
        if abs(zoom - self._zoom) < 0.01:
            return
        vp = self.scroll.viewport()
        label_w = max(1, self.preview_lbl.width())
        label_h = max(1, self.preview_lbl.height())
        hx = (self.scroll.horizontalScrollBar().value() + vp.width() / 2) / label_w
        vy = (self.scroll.verticalScrollBar().value() + vp.height() / 2) / label_h
        self._zoom = zoom
        self._render_display()
        # 新尺寸要等这一轮布局跑完才生效，滚动位置延后一拍再摆
        self._pending_scroll = (hx, vy)
        self._scroll_timer.start(0)

    def _restore_pending_scroll(self):
        pending, self._pending_scroll = self._pending_scroll, None
        if pending:
            self._restore_scroll(*pending)

    def _restore_scroll(self, hx: float, vy: float):
        vp = self.scroll.viewport()
        self.scroll.horizontalScrollBar().setValue(
            int(hx * self.preview_lbl.width() - vp.width() / 2))
        self.scroll.verticalScrollBar().setValue(
            int(vy * self.preview_lbl.height() - vp.height() / 2))

    def wheel_zoom(self, delta: int) -> bool:
        """Ctrl/⌘ + 滚轮缩放；普通滚轮留给 QScrollArea 滚内容。"""
        if not delta:
            return False
        self.set_zoom(self._zoom * (_ZOOM_STEP if delta > 0 else 1 / _ZOOM_STEP))
        return True

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.Wheel and obj is self.scroll.viewport():
            if event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                    | Qt.KeyboardModifier.MetaModifier):
                if self.wheel_zoom(event.angleDelta().y()):
                    return True
        elif kind == QEvent.Type.MouseButtonDblClick and obj is self.preview_lbl:
            # 双击在「适应」和 2 倍之间切：看手写小字最常用的两档
            self.set_zoom(1.0 if self._zoom > 1.05 else 2.0)
            return True
        return super().eventFilter(obj, event)

    def _show_image(self, img: QImage, page_no: int):
        if page_no != self._page:
            return
        self._full_pixmap = QPixmap.fromImage(img)
        self._render_display()
        self.page_label.setText(f"第 {page_no} / {self._total or '?'} 页")
        self.prev_btn.setEnabled(page_no > 1)
        self.next_btn.setEnabled(not self._total or page_no < self._total)

    def showEvent(self, e):
        super().showEvent(e)
        # 第一次显示时几何才定下来：图片可能是在布局之前就算好的，按真实视口重排
        self._render_display()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._render_display()

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

    def _ignore_blanks(self):
        if not self._cells or not self._cells.blanks:
            return
        self._blanks_ignored = True
        self.cell_label.setText(
            f"已忽略 {len(self._cells.blanks)} 处空格，不影响上传")
        self.ignore_blanks_btn.hide()
        self._on_ignore_blanks(self.path)

    # ---- 异常标记 ----

    def _toggle_abnormal(self, checked: bool):
        self._set_abnormal(checked)

    def _set_abnormal(self, bad: bool):
        self._abnormal = bad
        self.abnormal_btn.setChecked(bad)
        self.abnormal_btn.setText("已标记异常" if bad else "存在异常")
        self._on_mark(self.path, bad)

    def stop_workers(self):
        """关页面前收掉后台线程：先断信号，再等它跑完。

        只 wait 不够——worker 是跨线程 queued 信号，emit 之后页面可能已经
        deleteLater，排队中的槽会访问到已释放的控件（段错误）。断开后信号直接丢弃。
        """
        for attr in ("_worker", "_exam", "_cells_worker", "_ocr_worker"):
            thread = getattr(self, attr, None)
            if thread is None:
                continue
            for name in ("done", "failed", "rendered", "finished"):
                sig = getattr(thread, name, None)
                if sig is not None:
                    try:
                        sig.disconnect()
                    except (TypeError, RuntimeError):
                        pass          # 没连过就 disconnect() 会抛，忽略
            if thread.isRunning():
                thread.wait(2500)

    def retarget(self, path: str):
        """改指向另一份文件（原件永不移动，正常浏览路径不变）。"""
        self.path = path

    @property
    def is_abnormal(self) -> bool:
        return self._abnormal


def set_tip(widget, text: str):
    widget.setToolTip(text)
