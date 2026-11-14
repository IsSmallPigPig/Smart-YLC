"""页面基类：官方 gallery 同款骨架。

页面本身即 ScrollArea（FluentWindow 的 addSubInterface 直接接受 QWidget 子类），
内容放在透明的 scrollWidget 中，窗口变小时自动出现滚动条，表格与卡片不会被裁切。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QHeaderView, QHBoxLayout, QTableView, QVBoxLayout, QWidget

from qfluentwidgets import CaptionLabel, CardWidget, FluentIcon as FIF, IconWidget, ScrollArea, StrongBodyLabel, TransparentToolButton, isDarkTheme, qconfig

PAGE_MARGINS = (36, 28, 36, 28)


def note_label(text: str) -> CaptionLabel:
    """说明文字：12px 灰色小字，浅色主题用 gray-600 保证对比度，标题与备注分层。"""
    lbl = CaptionLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextColor(QColor("#4b5563"), QColor("#9ca3af"))
    return lbl


def theme_font(lbl, pixel_size: int, weight: int = 0):
    """按像素设置字号/字重，不覆盖 fluent 的主题样式表。

    用 widget.setStyleSheet 写 font-* 会整体替换掉 fluent 的 qss，文字颜色
    退回 Qt 调色板——系统外观是深色时调色板文字为白色，浅色页面里就变成
    白字白底。QFont 只改字体属性，颜色仍由 fluent 按明暗模式联动。"""
    f = lbl.font()
    f.setPixelSize(pixel_size)
    if weight:
        f.setWeight(QFont.Weight(weight))
    lbl.setFont(f)
    return lbl


def section_title(text: str) -> StrongBodyLabel:
    """卡片区块标题：16px 加粗，比正文略大，与灰色说明文字拉开层级。"""
    return theme_font(StrongBodyLabel(text), 16, int(QFont.Weight.DemiBold))


def sub_title(text: str) -> StrongBodyLabel:
    """卡片内分组小标题：15px 加粗，弱于卡片标题一级。"""
    return theme_font(StrongBodyLabel(text), 15, int(QFont.Weight.DemiBold))


def _row_line_color() -> QColor:
    """卡片内分割线：明暗模式各取一档极淡的对比色。"""
    return QColor(255, 255, 255, 20) if isDarkTheme() else QColor(0, 0, 0, 22)


class CardRow(QWidget):
    """卡片内一块内容的基类，自己画底部分割线。

    separator=False 用在卡片最后一块，避免结尾多一条线；主题切换时重绘。"""

    def __init__(self, separator: bool = True, parent=None):
        super().__init__(parent)
        self._separator = separator

    def set_separator(self, value: bool):
        self._separator = value
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self._separator:
            return
        painter = QPainter(self)
        painter.setPen(_row_line_color())
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)


class SettingRow(CardRow):
    """左图标 + 标题（可带一行说明），右侧控件贴卡片右缘对齐。

    控件区有最小宽度，不同行的控件右边缘因此排成一条线；control 可以是
    单个控件，也可以是先包好的 QWidget（多个控件时）。"""

    def __init__(self, title: str, desc: str = "", control: QWidget | None = None,
                 icon: FIF | None = None, control_width: int = 300,
                 separator: bool = True, parent=None):
        super().__init__(separator, parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(14)
        if icon is not None:
            ic = IconWidget(icon)
            ic.setFixedSize(18, 18)
            lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
        text_lay = QVBoxLayout()
        text_lay.setContentsMargins(0, 0, 0, 0)
        text_lay.setSpacing(3)
        text_lay.addWidget(theme_font(StrongBodyLabel(title), 14, int(QFont.Weight.DemiBold)))
        if desc:
            text_lay.addWidget(note_label(desc))
        lay.addLayout(text_lay, 1)
        if control is not None:
            holder = QWidget(self)
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(8)
            hl.addStretch(1)
            hl.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
            holder.setMinimumWidth(control_width)
            lay.addWidget(holder, 0, Qt.AlignmentFlag.AlignVCenter)


class FullRow(CardRow):
    """占满卡片宽度的整块内容（表格、工具条）。用 row.lay 往里加控件。"""

    def __init__(self, separator: bool = True, parent=None):
        super().__init__(separator, parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 8, 0, 14)
        self.lay.setSpacing(10)


class CollapsibleCard(CardWidget):
    """折叠卡片：标题行 + 细线条 chevron 箭头（展开朝上 / 收起朝下，无按钮底色）。

    用法：content = CollapsibleCard("标题", expanded=True)；往
    content.body_lay 里加 SettingRow / FullRow 即可，行间分割线由行自己画。"""

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self._expanded = bool(expanded)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(section_title(title))
        header.addStretch(1)
        self.toggle_btn = TransparentToolButton()
        self.toggle_btn.setToolTip("展开 / 收起")
        self.toggle_btn.clicked.connect(self.toggle)
        header.addWidget(self.toggle_btn)
        outer.addLayout(header)
        self.body = QWidget(self)
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(0)
        outer.addWidget(self.body)
        self._sync()
        # 主题切换时箭头颜色要按新主题重烘焙（旋转图标是像素级副本）
        qconfig.themeChanged.connect(self._sync)

    def toggle(self):
        self.set_expanded(not self._expanded)

    def set_expanded(self, value: bool):
        self._expanded = bool(value)
        self._sync()

    def _sync(self, *_):
        self.body.setVisible(self._expanded)
        # 分割线颜色按主题取，主题切换时由卡片统一重绘各行（避免每行都挂全局信号）
        for i in range(self.body_lay.count()):
            w = self.body_lay.itemAt(i).widget()
            if w is not None:
                w.update()
        icon = FIF.CHEVRON_DOWN_MED.icon()
        if self._expanded:
            # fluent 没有 chevron-up 图标：向下 chevron 旋转 180°
            img = icon.pixmap(16, 16).toImage().transformed(QTransform().rotate(180))
            icon = QIcon(QPixmap.fromImage(img))
        self.toggle_btn.setIcon(icon)


def fit_table_columns(table: QTableView, stretch_last: bool = False,
                      skip_cols: set | None = None, max_col_width: int = 420):
    """按内容手动计算列宽（视觉效果同 ResizeToContents），但只在调用时算一次。

    不用 QHeaderView.ResizeToContents：它会在每次数据变化时逐格调用
    delegate.sizeHint（Python override）——PySide6 6.12 下在 queued 信号处理
    栈内 + 后台线程活跃时会触发 shiboken 空指针崩溃
    （QTableView::sizeHintForColumn → PepException_GetArgs，见 17:18/17:19 两份崩溃报告）。
    数据填充完成后调用一次即可。"""
    from PySide6.QtWidgets import QTableWidgetItem

    fm = QFontMetrics(table.font())
    header = table.horizontalHeader()
    skip = skip_cols or set()
    cols = table.columnCount()
    for col in range(cols):
        mode = header.sectionResizeMode(col)
        if mode in (QHeaderView.ResizeMode.Fixed, QHeaderView.ResizeMode.Stretch):
            continue
        item = table.horizontalHeaderItem(col)
        w = fm.horizontalAdvance(item.text()) + 28 if item else 70
        for row in range(table.rowCount()):
            it = table.item(row, col)
            if it is not None:
                w = max(w, fm.horizontalAdvance(it.text()) + 22)
        table.setColumnWidth(col, max(min(w, max_col_width), 70))
    if stretch_last and cols:
        header.setSectionResizeMode(cols - 1, QHeaderView.ResizeMode.Stretch)
    elif cols:
        header.setSectionResizeMode(cols - 1, QHeaderView.ResizeMode.Interactive)


class CallbackWorker(QThread):
    """把一个函数放到后台线程跑，结果经信号回 GUI 线程。

    用于界面上的文件解析/写盘等重活：ok(object) 带返回值，fail(str) 带异常信息。
    调用方需持有引用防 GC（存到 self._workers 之类）。"""

    ok = Signal(object)
    fail = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self.finished.connect(self.deleteLater)

    def run(self):
        try:
            self.ok.emit(self._fn())
        except Exception as e:
            self.fail.emit(str(e))


class FluentPage(ScrollArea):
    def __init__(self, object_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)

        self.scrollWidget = QWidget(self)
        self.scrollWidget.setObjectName(f"{object_name}-scroll")
        self.vBoxLayout = QVBoxLayout(self.scrollWidget)
        self.vBoxLayout.setContentsMargins(*PAGE_MARGINS)
        self.vBoxLayout.setSpacing(12)
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        # 官方 gallery 的透明背景处理：滚动区与内容容器都不画自己的底色
        self.setStyleSheet("QScrollArea{background: transparent; border: none}")
        self.scrollWidget.setStyleSheet(
            f"QWidget#{object_name}-scroll{{background: transparent}}"
        )

    def add_table(self, table, stretch: int = 0, min_height: int = 180):
        """表格在滚动容器里需要保底高度，窗口缩小时不会被压扁。

        列宽不在此设置（不用 ResizeToContents——它会在数据变化时调用
        delegate.sizeHint Python override，PySide6 6.12 下会崩溃，见
        fit_table_columns）；各页数据填充后调用 fit_table_columns 手动算宽。"""
        table.setMinimumHeight(min_height)
        self.vBoxLayout.addWidget(table, stretch)
        return table
