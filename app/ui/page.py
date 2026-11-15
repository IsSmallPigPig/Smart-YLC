"""页面基类：官方 gallery 同款骨架。

页面本身即 ScrollArea（FluentWindow 的 addSubInterface 直接接受 QWidget 子类），
内容放在透明的 scrollWidget 中，窗口变小时自动出现滚动条，表格与卡片不会被裁切。
"""

from __future__ import annotations

import os

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import QHeaderView, QHBoxLayout, QTableView, QVBoxLayout, QWidget

from app.ui.hidpi import hidpi_canvas, ui_dpr
from qfluentwidgets import CaptionLabel, CardWidget, FluentIcon as FIF, IconWidget, ScrollArea, StrongBodyLabel, TransparentToolButton, isDarkTheme, qconfig

PAGE_MARGINS = (32, 24, 32, 24)


def note_label(text: str) -> CaptionLabel:
    """说明文字：12px 灰色小字，比正文再弱一档，标题与备注分层。

    深浅主题各用一档更暗的灰，避免与正文抢视线（比旧版 #9ca3af 更收敛）。"""
    lbl = CaptionLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextColor(QColor("#3f4751"), QColor("#7e8896"))
    return lbl


def chevron_left_icon(size: int = 16, enabled: bool = True) -> QIcon:
    """细左 chevron「<」：只有尖角，不带中间那根横线。

    fluent 只给了两种左侧箭头——实心三角 CARE_LEFT_SOLID 和带杆的 LEFT_ARROW，
    都不合适；把向右手 chevron 旋转 180° 就是「<」。
    enabled=False 时返回 40% 透明的版本：qfluentwidgets 的按钮不会自动套用
    QIcon.Disabled，不自己调透明度的话置灰状态看不出来。
    主题切换后要重新调用（颜色在生成位图时就定死了）。
    """
    dpr = ui_dpr()
    px = max(1, round(size * dpr))
    img = FIF.CHEVRON_RIGHT.icon().pixmap(px, px).toImage()
    normal = QPixmap.fromImage(img.transformed(QTransform().rotate(180)))
    normal.setDevicePixelRatio(dpr)
    if enabled:
        return QIcon(normal)
    def _faded(painter: QPainter) -> None:
        painter.setOpacity(0.4)
        painter.drawPixmap(0, 0, normal)

    return QIcon(hidpi_canvas(size, _faded))


def chevron_icon(expanded: bool) -> QIcon:
    """折叠箭头：统一用 fluent 的细 chevron，展开时朝上。

    fluent 没有 chevron-up 图标，朝上就是把向下 chevron 旋转 180°。
    """
    icon = FIF.CHEVRON_DOWN_MED.icon()
    if expanded:
        dpr = ui_dpr()
        px = max(1, round(16 * dpr))
        img = icon.pixmap(px, px).toImage().transformed(QTransform().rotate(180))
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        return QIcon(pm)
    return icon


def triangle_icon(direction: str = "right", size: int = 16) -> QIcon:
    """纯三角箭头：无外框、无圆圈，只有一个实心三角。

    fluent 的 PAGE_LEFT / PAGE_RIGHT 自带一圈圆环，这里换成手绘三角。
    颜色随深浅主题变化，主题切换后需要重新生成（见 messages_page.apply_theme）。
    """
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPolygonF

    color = QColor("#d6d6d6") if isDarkTheme() else QColor("#3a3a3a")

    def _paint(p: QPainter) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        w = size * 0.42
        h = size * 0.72
        cx, cy = size / 2, size / 2
        if direction == "left":
            pts = [QPointF(cx + w / 2, cy - h / 2), QPointF(cx - w / 2, cy),
                   QPointF(cx + w / 2, cy + h / 2)]
        else:
            pts = [QPointF(cx - w / 2, cy - h / 2), QPointF(cx + w / 2, cy),
                   QPointF(cx - w / 2, cy + h / 2)]
        p.drawPolygon(QPolygonF(pts))

    return QIcon(hidpi_canvas(size, _paint))


def set_tip(widget, text: str, delay: int = 300):
    """设置悬浮提示，并套上 fluent 的提示样式。

    默认的 QToolTip 是系统原生气泡，样式和界面不搭；fluent 的 ToolTipFilter
    会画成跟卡片一致的浮层。
    """
    from qfluentwidgets import ToolTipFilter

    widget.setToolTip(text)
    # 表格条目之类不是控件，没有事件过滤器，只能用原生提示
    if hasattr(widget, "installEventFilter") and not getattr(widget, "_fluent_tip_installed", False):
        widget.installEventFilter(ToolTipFilter(widget, delay))
        widget._fluent_tip_installed = True
    return widget


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


def page_title(text: str) -> StrongBodyLabel:
    """页面主标题：整页最大最重的一行，卡片标题与正文都要明显弱于它。"""
    return theme_font(StrongBodyLabel(text), 26, int(QFont.Weight.Bold))


class PageBanner(QWidget):
    """页面头图：通栏贴顶的 banner，承载页面标题（大字号白字）与右下角的操作按钮。

    仿首页头图配色、参考 FluentUI 页头排版——标题在上、说明在其下，
    功能按钮统一落在 banner 右下角（不居中），各页位置一致。
    底边向下渐隐到全透明，与页面底色平滑过渡，不做硬切。

    背景与首页 Banner 同源：未设置头图时用同色系蓝→深蓝渐变；
    设置了头图（系统配置里统一设置）后用 cover 铺满。左上角切圆角。

    它取代 PageHeader 成为页面头：沿用同样的接口（title_label / desc_label /
    set_title / set_desc / add_control / slot）。"""

    CORNER_RADIUS = 10
    HEIGHT = 196

    def __init__(self, title: str = "", desc: str = "", parent=None):
        super().__init__(parent)
        self._pixmap = None
        self.setFixedHeight(self.HEIGHT)
        self.setContentsMargins(0, 0, 0, 0)

        lay = QVBoxLayout(self)
        # 左右与内容区同边距（32），标题与按钮的左右边缘因此和下方卡片对齐
        lay.setContentsMargins(32, 18, 32, 16)
        lay.setSpacing(6)
        lay.addStretch(1)  # 标题整体略高于中线

        head = QHBoxLayout()
        head.setSpacing(12)
        self.title_label = page_title(title)
        self._set_light(self.title_label, 24)
        head.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        lay.addLayout(head)

        self.desc_label = note_label("")
        self._set_light(self.desc_label, dim=True)
        lay.addWidget(self.desc_label)

        self.slot = QHBoxLayout()  # 面包屑之类插这里，紧跟标题
        self.slot.setSpacing(8)
        lay.addLayout(self.slot)
        self.set_desc(desc)

        lay.addStretch(1)

        # 功能按钮：右下角一行（不居中）
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.controls = QHBoxLayout()
        self.controls.setSpacing(8)
        bottom.addLayout(self.controls)
        lay.addLayout(bottom)

    @staticmethod
    def _set_light(label, size: int = 0, dim: bool = False):
        if dim:
            label.setTextColor(QColor(255, 255, 255, 222), QColor(255, 255, 255, 222))
        else:
            label.setTextColor(QColor(255, 255, 255), QColor(255, 255, 255))
        if size:
            f = label.font()
            f.setPixelSize(size)
            f.setWeight(QFont.Weight.Bold)
            label.setFont(f)

    def set_title(self, text: str):
        self.title_label.setText(text)

    def set_desc(self, text: str):
        self.desc_label.setText(text)
        self.desc_label.setVisible(bool(text))

    def add_control(self, widget):
        self.controls.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return widget

    def set_image_path(self, path: str):
        if path and os.path.isfile(path):
            self._pixmap = QPixmap(path)
        else:
            self._pixmap = None
        self.update()

    def paintEvent(self, e):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        # 先画进一层 QImage，再对整层做 alpha 渐隐：底边柔和过渡到页面底色，不硬切
        layer = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        layer.fill(Qt.GlobalColor.transparent)
        p = QPainter(layer)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setPen(Qt.PenStyle.NoPen)

        # 左上角圆角：与整窗圆角语言一致
        path = QPainterPath()
        r = self.CORNER_RADIUS
        path.moveTo(r, 0)
        path.lineTo(w, 0)
        path.lineTo(w, h)
        path.lineTo(0, h)
        path.lineTo(0, r)
        path.arcTo(QRectF(0, 0, r * 2, r * 2), 180, -90)
        path.closeSubpath()
        p.setClipPath(path)

        if self._pixmap and not self._pixmap.isNull():
            pm = self._pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((pm.width() - w) // 2 * -1, (pm.height() - h) // 2 * -1, pm)
        else:
            top = QColor(38, 84, 140) if isDarkTheme() else QColor(166, 199, 235)
            bottom = QColor(24, 42, 68) if isDarkTheme() else QColor(84, 134, 196)
            grad = QLinearGradient(0, 0, w * 0.25, h)
            grad.setColorAt(0, top)
            grad.setColorAt(1, bottom)
            p.fillRect(layer.rect(), grad)

        # 底边渐隐：只改 alpha，从 78% 处开始柔和淡出到全透明（与页面底色平滑衔接）
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        fade = QLinearGradient(0, h * 0.78, 0, h)
        fade.setColorAt(0, QColor(0, 0, 0, 255))
        fade.setColorAt(0.55, QColor(0, 0, 0, 180))
        fade.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(layer.rect(), fade)
        p.end()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawImage(0, 0, layer)



class PageHeader(QWidget):
    """页面标题区：标题与说明直接写在背景上，右侧放这一页的操作控件。

    页面不再用卡片装标题——卡片留给真正成组的内容，标题和工具条自己站在背景上，
    与列表卡片形成两层，控件一律挤在一行。"""

    def __init__(self, title: str, desc: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 6, 2, 8)
        lay.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.title_label = page_title(title)
        row.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.slot = QHBoxLayout()  # 详情态的面包屑之类插这里，紧跟标题
        self.slot.setSpacing(8)
        row.addLayout(self.slot)
        row.addStretch(1)
        self.controls = QHBoxLayout()
        self.controls.setSpacing(8)
        row.addLayout(self.controls)
        lay.addLayout(row)
        self.desc_label = note_label("")
        lay.addWidget(self.desc_label)
        self.set_desc(desc)

    def add_control(self, widget: QWidget):
        self.controls.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return widget

    def set_desc(self, text: str):
        self.desc_label.setText(text)
        self.desc_label.setVisible(bool(text))

    def set_title(self, text: str):
        self.title_label.setText(text)


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


# 右侧控件列的上限宽度：约等于一个六字按钮。控件按内容取宽、右边缘对齐，
# 放不下的次要项收进「设置」图标弹窗，而不是把这一列撑成一条宽跑道。
CONTROL_MAX_WIDTH = 190
CONTROL_WIDTH = CONTROL_MAX_WIDTH  # 兼容旧调用点


class SettingRow(CardRow):
    """左图标 + 标题（可带一行说明），右侧控件列固定同一宽度。

    控件列用固定宽度而不是最小宽度，各行的控件才会左右两边都对成一条线；
    control 可以是单个控件，也可以是先包好的 QWidget（多个控件时）。"""

    def __init__(self, title: str, desc: str = "", control: QWidget | None = None,
                 icon: FIF | None = None, control_width: int | None = None,
                 separator: bool = True, parent=None):
        super().__init__(separator, parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 5, 0, 5)
        lay.setSpacing(14)
        if icon is not None:
            ic = IconWidget(icon)
            ic.setFixedSize(18, 18)
            lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
        text_lay = QVBoxLayout()
        text_lay.setContentsMargins(0, 0, 0, 0)
        text_lay.setSpacing(2)
        text_lay.addWidget(theme_font(StrongBodyLabel(title), 14, int(QFont.Weight.DemiBold)))
        self._text_lay = text_lay
        self.desc_label = None
        if desc:
            self.desc_label = note_label(desc)
            text_lay.addWidget(self.desc_label)
        lay.addLayout(text_lay, 1)
        if control is not None:
            holder = QWidget(self)
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(8)
            hl.addStretch(1)
            hl.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
            if control_width:
                holder.setFixedWidth(control_width)
            else:
                holder.setMaximumWidth(CONTROL_MAX_WIDTH)
            self.control_holder = holder  # 供测试核对「所有行同一列宽」
            lay.addWidget(holder, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_desc(self, text: str):
        """更新说明小字；动态状态直接接在标题下面，不用再单独占一行。"""
        if self.desc_label is None:
            if not text:
                return
            self.desc_label = note_label(text)
            self._text_lay.addWidget(self.desc_label)
        else:
            self.desc_label.setText(text)
            self.desc_label.setVisible(bool(text))


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
    content.body_lay 里加 SettingRow / FullRow 即可，行间分割线由行自己画。
    tags 是标题右侧的胶囊标签 [(文字, 色调)]，份数、学段这类属性放这里，
    不再用「·」拼进标题。"""

    def __init__(self, title: str, expanded: bool = True,
                 tags: list[tuple[str, str]] | None = None, parent=None):
        super().__init__(parent)
        # 圆角与窗口圆角一致（约 10px），卡片四角与窗口四角同心，视觉更整
        self.setBorderRadius(10)
        self._expanded = bool(expanded)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 12, 20, 12)
        outer.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.title_label = section_title(title)
        header.addWidget(self.title_label)
        self._tag_lay = header
        self._tags: list = []
        header.addStretch(1)
        self.toggle_btn = TransparentToolButton()
        set_tip(self.toggle_btn, "展开或收起")
        self.toggle_btn.clicked.connect(self.toggle)
        header.addWidget(self.toggle_btn)
        outer.addLayout(header)
        self.set_tags(tags or [])
        self.body = QWidget(self)
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(0)
        outer.addWidget(self.body)
        self._sync()
        # 主题切换时箭头颜色要按新主题重烘焙（旋转图标是像素级副本）
        qconfig.themeChanged.connect(self._sync)

    def set_tags(self, tags: list[tuple[str, str]]):
        """替换标题右侧的标签；插在标题与弹簧之间，靠左紧贴标题。"""
        from app.ui.widgets import PillLabel

        for pill in self._tags:
            self._tag_lay.removeWidget(pill)
            pill.deleteLater()
        self._tags = []
        stretch_index = self._tag_lay.count() - 2  # 弹簧与箭头之间
        for text, tone in tags:
            pill = PillLabel(str(text), tone)
            self._tag_lay.insertWidget(stretch_index, pill, 0, Qt.AlignmentFlag.AlignVCenter)
            stretch_index += 1
            self._tags.append(pill)

    def toggle(self):
        self.set_expanded(not self._expanded)

    def set_expanded(self, value: bool, animate: bool = True):
        """点击箭头时展开/收起带高度过渡；首次构造和主题刷新走静态路径。"""
        value = bool(value)
        if value == self._expanded:
            self._sync()
            return
        self._expanded = value
        self._sync_chevron()
        if animate and self.isVisible():
            from app.ui.anim import animate_expand

            animate_expand(self.body, value, duration=190)
        else:
            self._sync()

    def _sync(self, *_):
        # 动画进行中不要改可见性与最大高度，否则会把补间打断
        animating = getattr(self.body, "_expand_ani", None) is not None
        if not animating:
            self.body.setVisible(self._expanded)
            self.body.setMaximumHeight(16777215)
        # 分割线颜色按主题取，主题切换时由卡片统一重绘各行
        for i in range(self.body_lay.count()):
            w = self.body_lay.itemAt(i).widget()
            if w is not None:
                w.update()
        self._sync_chevron()

    def _sync_chevron(self, *_):
        self.toggle_btn.setIcon(chevron_icon(self._expanded))


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
        # fluent 的单元格委托左右各留约 24px 内边距（实测：55px 字形 hint 94px，
        # 91px 字形 hint 137px），这里按同样的留白再加 48px，长账号和备注才不会被省略
        w = fm.horizontalAdvance(item.text()) + 52 if item else 88
        for row in range(table.rowCount()):
            it = table.item(row, col)
            if it is not None:
                w = max(w, fm.horizontalAdvance(it.text()) + 48)
        table.setColumnWidth(col, max(min(w, max_col_width), 88))
    if stretch_last and cols:
        header.setSectionResizeMode(cols - 1, QHeaderView.ResizeMode.Stretch)
    elif cols:
        header.setSectionResizeMode(cols - 1, QHeaderView.ResizeMode.Interactive)


class BatchedBuild:
    """把一批控件分批建出来，避免一次性构建几十个卡片把界面卡住。

    200 份档案时同步建 40 个支部卡片实测 580ms，用户在重建期间完全点不动；
    这里第一批同步建好（首帧就有内容、测试里的小批量也照旧同步完成），
    其余按事件循环的一帧建 `batch` 个，期间界面照常响应点击与滚动。

    每次 start() 递增 token，旧批次自动作废：连续搜索时不会出现两批卡片
    交叉插进同一个布局。
    """

    def __init__(self, owner: QWidget, batch: int = 4):
        self._owner = owner
        self._batch = max(1, batch)
        self._token = 0
        self._items: list = []
        self._step = None
        self._next = 0
        self._timer = QTimer(owner)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._pump)

    def start(self, items, step) -> None:
        """items 逐个交给 step(item)；step 负责建控件并加进布局。"""
        self._token += 1
        self._timer.stop()
        self._items = list(items)
        self._step = step
        self._next = 0
        self._pump()

    def cancel(self) -> None:
        self._token += 1
        self._timer.stop()
        self._items = []
        self._step = None

    def _pump(self) -> None:
        step = self._step
        if step is None:
            return
        end = min(self._next + self._batch, len(self._items))
        while self._next < end:
            step(self._items[self._next])
            self._next += 1
        if self._next < len(self._items):
            self._timer.start()
        else:
            self._timer.stop()


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
        self.vBoxLayout.setSpacing(14)
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
