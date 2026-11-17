"""工具首页：头图铺满顶部并柔和融进窗口底色，欢迎大字 + 概览磁贴 + 导出入口。

头图在系统配置设置，持久化到配置文件 headerImage 键，未设置时显示同色系占位渐变。
概览数字来自配置缓存（离线汇总），30 秒同步后自动刷新。
"""

from __future__ import annotations

import os

from PyQt5.QtCore import QRectF, Qt, pyqtSignal as Signal
from PyQt5.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PyQt5.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import CardWidget, FluentIcon as FIF
from qfluentwidgets import SubtitleLabel, TitleLabel, isDarkTheme

from app.ui.anim import fade_out
from app.ui.charts import BarRowWidget, ProgressDonut
from app.ui.page import (BusyOverlay, FluentPage, banner_stops, note_label,
                         page_bg_color)
from app.ui.widgets import StatTile
from app.ui import palette as P

BANNER_RATIO = 0.40  # 头图占视口高度的比例，铺满上方页面

TILES = (
    # (键, 标题, 官方 color 图标名, 卡片色调)
    # 图标用 microsoft/fluentui-system-icons 的彩色渐变实心图标，原色渲染
    ("upload", "档案上传", "upload", "info"),
    ("total", "团员总数", "total", "info"),
    ("branches", "支部数量", "branches", "info"),
    ("unread", "未读消息", "unread", "info"),
)
KIND_CHARTS = (
    # 四类异常统一走冷色系色阶：原橙色 / 紫色在此替换为协调的蓝→青→蓝绿
    ("identity", "信息错误", P.KIND["identity"]),
    ("report", "没有报道", P.KIND["report"]),
    ("archive", "没有档案", P.KIND["archive"]),
    ("fees", "欠缴团费", P.KIND["fees"]),
)


class Banner(QWidget):
    """头图：通栏贴顶，图片 cover 铺满，下沿按 alpha 渐隐到全透明。

    不再把图片压到某个猜出来的背景色上，而是让它直接淡出到真实窗口底色
    （纯色、毛玻璃、明暗主题都一致），交界处因此没有颜色对不上的硬切线。

    左上角切一个圆角：头图是内容区的底子，方角会在侧边栏与页面交界处
    顶出一个生硬直角，圆角才和整窗的圆角语言一致。
    """

    CORNER_RADIUS = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None

    def set_image_path(self, path: str):
        if path and os.path.isfile(path):
            self._pixmap = QPixmap(path)
        else:
            self._pixmap = None
        self.update()

    def _corner_path(self, w: int, h: int) -> QPainterPath:
        """左上角圆角、其余为直角的内容区轮廓。"""
        r = self.CORNER_RADIUS
        path = QPainterPath()
        path.moveTo(r, 0)
        path.lineTo(w, 0)
        path.lineTo(w, h)
        path.lineTo(0, h)
        path.lineTo(0, r)
        path.arcTo(QRectF(0, 0, r * 2, r * 2), 180, -90)
        path.closeSubpath()
        return path

    def paintEvent(self, e):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        layer = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        layer.fill(Qt.GlobalColor.transparent)
        p = QPainter(layer)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        # 左上角圆角：先裁形状，再往里画图
        path = self._corner_path(w, h)
        p.setClipPath(path)

        if self._pixmap and not self._pixmap.isNull():
            pm = self._pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((pm.width() - w) // 2 * -1, (pm.height() - h) // 2 * -1, pm)
        else:
            top, bottom = banner_stops()
            grad = QLinearGradient(0, 0, w * 0.35, h)
            grad.setColorAt(0, top)
            grad.setColorAt(1, bottom)
            p.fillRect(layer.rect(), grad)

        # DestinationIn 只改 alpha：下沿拉长渐隐，头图自然化进窗口底色
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        fade = QLinearGradient(0, 0, 0, h)
        fade.setColorAt(0, QColor(0, 0, 0, 255))
        fade.setColorAt(0.66, QColor(0, 0, 0, 255))
        fade.setColorAt(0.84, QColor(0, 0, 0, 170))
        fade.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(layer.rect(), fade)
        p.end()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawImage(0, 0, layer)


class LoadingOverlay(BusyOverlay):
    """首页加载过渡：概览快照在后台跑时盖住尚未就绪的内容，快照回来后再淡出。"""

    def __init__(self, parent=None):
        super().__init__(parent, "正在加载概览…")
        lay = self.layout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)


class HomePage(FluentPage):
    stats_ready = Signal()  # 首屏快照已应用 → 主窗口据此收起启动闪屏

    def __init__(self, window, parent=None):
        super().__init__('page-home', parent)
        self.window_ref = window
        self._workers: list = []
        lay = self.vBoxLayout
        # 头图叠加在页面底层（不占布局），正文铺满整页：欢迎块以整个视口垂直居中，
        # 正好落在头图与下方背景的交界处
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.banner = Banner(self.scrollWidget)
        self.banner.lower()

        body = QWidget()
        body_lay = QVBoxLayout(body)
        # 整组内容都缩进 36px（图标也收进 inner 里，和欢迎文字同一根左边线），
        # 只有 body 自身不缩进，让内容区铺满视口、由 inner 统一控制左右留白。
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(8)
        body_lay.addStretch(2)
        self.hello_label = SubtitleLabel("欢迎，")
        self.title_label = TitleLabel("团委管理员")
        # 两行标题收在一个盒子里：默认布局会在它们之间留一整行空隙
        greet = QWidget()
        greet_lay = QVBoxLayout(greet)
        greet_lay.setContentsMargins(0, 0, 0, 0)
        greet_lay.setSpacing(0)
        greet_lay.addWidget(self.hello_label)
        greet_lay.addWidget(self.title_label)

        # 图标与欢迎文字统一左对齐到同一根 36px 缩进线：之前图标单独顶到侧边栏
        # 右缘、和文字错开一截，看着像没对齐。同时放大到与标题协调的尺寸。
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(36, 0, 36, 0)
        inner_lay.setSpacing(8)
        from PyQt5.QtWidgets import QLabel

        from app.ui.brand import app_tile_pixmap

        logo = QLabel(inner)
        # 用裁掉留白的底板（64 ≈ 原来 80px 位图里那 63px 可见方块），
        # 方块左缘才能和下面的欢迎文字落在同一条 36px 缩进线上
        logo.setPixmap(app_tile_pixmap(64))
        logo.setFixedSize(64, 64)
        inner_lay.addWidget(logo)
        inner_lay.addSpacing(4)
        inner_lay.addWidget(greet)
        inner_lay.addSpacing(12)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        self.tiles: dict[str, StatTile] = {}
        for index, (key, title, icon, tone) in enumerate(TILES):
            tile = StatTile(title, "—", "", icon=icon, tone=tone)
            tile.clicked.connect(lambda k=key: self._open_for(k))
            self.tiles[key] = tile
            grid.addWidget(tile, 0, index)

        chart = CardWidget()
        chart.setBorderRadius(P.RADIUS["card"])
        chart_lay = QHBoxLayout(chart)
        chart_lay.setContentsMargins(18, 12, 18, 12)
        chart_lay.setSpacing(24)
        self.donut = ProgressDonut()
        chart_lay.addWidget(self.donut, 0)
        bars = QVBoxLayout()
        bars.setSpacing(0)
        self.bars: dict[str, BarRowWidget] = {}
        for key, label, color in KIND_CHARTS:
            bar = BarRowWidget(label, color)
            self.bars[key] = bar
            bars.addWidget(bar)
        chart_lay.addLayout(bars, 1)
        grid.addWidget(chart, 1, 0, 1, len(TILES))
        inner_lay.addLayout(grid)

        self.summary = note_label("")
        inner_lay.addWidget(self.summary)
        body_lay.addWidget(inner)
        body_lay.addStretch(1)

        row = QHBoxLayout()
        row.setContentsMargins(36, 0, 36, 24)
        row.addStretch(1)
        lay.addWidget(body, 1)
        lay.addLayout(row)

        self._stats_shown = False
        self._apply_font()
        self._load_header()
        self.refresh_stats(animate=False, show_loading=False)

    # ---------- 概览 ----------

    def showEvent(self, event):
        super().showEvent(event)
        self._align_hero_ink()
        # 已经算过一次就不再铺过渡页：从托盘回来、或从别的页切回首页时，
        # 概览本来就在屏幕上，盖一层不透明底只会让整页先变白再长回来。
        # 首次显示（还没算过）才需要这层遮住未就绪的内容。
        self.refresh_stats(animate=not self._stats_shown,
                           show_loading=not self._stats_shown)

    def _align_hero_ink(self):
        """把「团委管理员」的墨迹左缘对齐到「欢迎，」那一行。

        两个标签的控件左缘相同，但 CJK 字形的左侧留白随字号变大（实测 2px vs 7px），
        大字号那行的墨迹就整体右移。字号要等控件 polish 之后才定型（构造时只拿到
        一半大），所以放在 showEvent 里按当时的字体度量重算；减 1 是因为度量把抗锯齿
        边缘也算进去了，直接减会过冲到控件左缘外侧。
        """
        gap = (self.title_label.fontMetrics().boundingRect(self.title_label.text()).left()
               - self.hello_label.fontMetrics().boundingRect(self.hello_label.text()).left())
        nudge = max(0, gap - 1)
        if self.title_label.contentsMargins().left() != -nudge:
            self.title_label.setContentsMargins(-nudge, 0, 0, 0)

    def refresh_stats(self, animate: bool = True, show_loading: bool = True):
        """概览统计放到后台线程。

        快照要读整份配置（memberCache 装着所有团员）并逐个跑检查，量大时在
        界面线程里做会卡住切页；切到首页时先铺一层加载过渡页遮住未就绪的内容，
        快照回来再淡出，避免延迟与布局未稳造成的错位被看到。
        show_loading=False 用于后台静默同步，不弹过渡页。"""
        from app.services import org_snapshot, store
        from app.ui.page import CallbackWorker

        self._stats_token = getattr(self, "_stats_token", 0) + 1
        token = self._stats_token

        if show_loading:
            self._show_loading()

        def _snapshot():
            return org_snapshot.snapshot(store.load())

        worker = CallbackWorker(_snapshot, self)
        # 上下文挂在 worker 上、槽用绑定方法：闭包槽不会随控件析构自动断开，
        # 上一个页面的 worker 迟到投递打进已删除的 C++ 对象就是段错误
        worker._token = token
        worker._show_loading = show_loading
        worker._animate = animate
        worker.ok.connect(self._on_stats_done)
        # 快照读配置失败时也要收掉过渡页：否则整页停在加载态，看起来就是没渲染出来。
        # 旧数字仍然有效（只是没刷新），不弹提示，等下一轮同步重试。
        worker.fail.connect(self._on_stats_error)
        self._workers.append(worker)
        worker.start()

    def _on_stats_done(self, data):
        worker = self.sender()
        token = getattr(worker, "_token", None)
        if token is None or token != getattr(self, "_stats_token", None):
            return
        if getattr(worker, "_show_loading", True):
            self._hide_loading()
        self._apply_stats(data, getattr(worker, "_animate", True))
        self.stats_ready.emit()

    def _on_stats_error(self, _msg):
        worker = self.sender()
        token = getattr(worker, "_token", None)
        if token is None or token != getattr(self, "_stats_token", None):
            return
        self._stop_loading()

    def _on_stats_failed(self, token: int):
        if token != self._stats_token:
            return
        self._stop_loading()

    def _show_loading(self):
        """铺一层加载过渡页：盖住首页内容，挡住快照延迟与布局未稳。

        启动闪屏（MainWindow.splash）已经盖住整窗时不再叠第二层。"""
        if getattr(self, "_startup_loading", False):
            return
        if getattr(self, "_loading", None) is None:
            self._loading = LoadingOverlay(self)
        ov = self._loading
        # 清掉上一次淡出残留的透明度，确保这一轮从全不透明开始
        ov.setGraphicsEffect(None)
        ov.setGeometry(self.viewport().geometry())
        ov.raise_()
        ov.show()
        ov.ring.start()

    def _hide_loading(self):
        """快照完成：停止转圈并淡出过渡页，露出已就绪的内容。"""
        ov = getattr(self, "_loading", None)
        if ov is None or not ov.isVisible():
            return
        ov.ring.stop()
        fade_out(ov, duration=160, on_done=lambda: ov.hide())

    def _stop_loading(self):
        """异常路径直接撤掉过渡页，别让页面卡在加载态。"""
        ov = getattr(self, "_loading", None)
        if ov is None:
            return
        ov.ring.stop()
        ov.setGraphicsEffect(None)
        ov.hide()

    def _apply_stats(self, data: dict, animate: bool = True):
        self._stats_shown = True
        total = data["total"]
        self.tiles["upload"].set_value(
            f"{data['uploaded']}/{total}" if total else "—",
            f"已上传 / 在册 {total} 人", animate=False)
        self.tiles["total"].set_value(total, f"覆盖 {data['branches']} 个支部", animate)
        self.tiles["branches"].set_value(data["branches"], "已配置账号", animate)
        self.tiles["unread"].set_value(data["unread"], "本地缓存未读", animate)
        self.donut.set_value(data["uploaded"], total)
        for key, bar in self.bars.items():
            bar.set_value(data["by_kind"].get(key, 0), max(total, 1))
        stamp = data["updated"].replace("T", " ")[5:16] if data["updated"] else ""
        self.summary.setText(
            f"转接申请 {data['applied']} 人"
            + (f"，最近同步 {stamp}" if stamp else "，还没有同步过数据"))

    def _open_for(self, key: str):
        window = self.window_ref
        if key == "unread":
            window.switchTo(window.messages_page)
        elif key == "upload":
            window.switchTo(window.upload_page)
        else:
            window.switchTo(window.org_manage_page)

    # ---------- 头图持久化（设置入口在系统配置） ----------

    def _load_header(self):
        from app.services import store

        self.banner.set_image_path(store.load().get("headerImage", ""))

    # ---------- 字号与字重 ----------

    def _apply_font(self):
        """保留原来的大字构图，只把字重收轻、字距放松，去掉 Black 的笨重感。"""
        vh = max(420, self.viewport().height())
        title_px = max(34, int(vh * 0.115))
        hello_px = max(26, int(vh * 0.085))

        # 两行用同一个字距：AbsoluteSpacing 会在每个字形**之前**也加一份间隙，
        # 两行字距不同的话首字左缘的偏移量也不同，_align_hero_ink 的补偿永远差一截
        f = self.title_label.font()
        f.setPixelSize(title_px)
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        self.title_label.setFont(f)

        f2 = self.hello_label.font()
        f2.setPixelSize(hello_px)
        f2.setWeight(QFont.Weight.Medium)
        f2.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        self.hello_label.setFont(f2)
        # 字号变了要重算墨迹对齐（showEvent 里那次是按旧字号算的）
        self._align_hero_ink()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.banner.setFixedSize(self.scrollWidget.width(),
                                 max(200, int(self.viewport().height() * BANNER_RATIO)))
        self.banner.move(0, 0)
        self.banner.lower()
        self._apply_font()
        # 过渡页要始终盖住可视区，尺寸变化时跟着重铺
        ov = getattr(self, "_loading", None)
        if ov is not None and ov.isVisible():
            ov.setGeometry(self.viewport().geometry())
