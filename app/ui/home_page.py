"""工具首页：头图铺满顶部并柔和融进窗口底色，欢迎大字 + 概览磁贴 + 导出入口。

头图在系统配置设置，持久化到配置文件 headerImage 键，未设置时显示同色系占位渐变。
概览数字来自配置缓存（离线汇总），30 秒同步后自动刷新。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import CardWidget, FluentIcon as FIF
from qfluentwidgets import SubtitleLabel, TitleLabel, isDarkTheme

from app.ui.charts import BarRowWidget, ProgressDonut
from app.ui.page import FluentPage, note_label
from app.ui.widgets import StatTile

BANNER_RATIO = 0.40  # 头图占视口高度的比例，铺满上方页面

TILES = (
    ("upload", "档案上传", FIF.CLOUD, "info"),
    ("total", "团员总数", FIF.PEOPLE, "info"),
    ("branches", "支部数量", FIF.PIE_SINGLE, "info"),
    ("unread", "未读消息", FIF.MESSAGE, "warn"),
)
KIND_CHARTS = (
    ("identity", "信息错误", "#ef6c00"),
    ("report", "没有报道", "#c62828"),
    ("archive", "没有档案", "#00838f"),
    ("fees", "欠缴团费", "#8e24aa"),
)


def page_bg_color() -> QColor:
    """关闭材质时窗口绘制用的底色（窗口底色与 StackedWidget 半透明白叠加后）。"""
    base = QColor(32, 32, 32) if isDarkTheme() else QColor(240, 244, 249)
    overlay = QColor(255, 255, 255, 8 if isDarkTheme() else 128)
    return QColor(
        base.red() + (overlay.red() - base.red()) * overlay.alpha() // 255,
        base.green() + (overlay.green() - base.green()) * overlay.alpha() // 255,
        base.blue() + (overlay.blue() - base.blue()) * overlay.alpha() // 255,
    )


class Banner(QWidget):
    """头图：通栏贴顶，图片 cover 铺满，下沿按 alpha 渐隐到全透明。

    不再把图片压到某个猜出来的背景色上，而是让它直接淡出到真实窗口底色
    （纯色、毛玻璃、明暗主题都一致），交界处因此没有颜色对不上的硬切线。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None

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

        layer = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        layer.fill(Qt.GlobalColor.transparent)
        p = QPainter(layer)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        if self._pixmap and not self._pixmap.isNull():
            pm = self._pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((pm.width() - w) // 2 * -1, (pm.height() - h) // 2 * -1, pm)
        else:
            top = QColor(38, 84, 140) if isDarkTheme() else QColor(166, 199, 235)
            bottom = QColor(24, 42, 68) if isDarkTheme() else QColor(214, 230, 246)
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
        painter.drawImage(0, 0, layer)


class HomePage(FluentPage):
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
        body_lay.setContentsMargins(36, 0, 36, 0)
        body_lay.setSpacing(8)
        body_lay.addStretch(2)
        from PySide6.QtWidgets import QLabel

        from app.ui.brand import app_pixmap

        logo = QLabel(body)
        logo.setPixmap(app_pixmap(64))
        logo.setFixedSize(64, 64)
        body_lay.addWidget(logo)
        body_lay.addSpacing(6)
        self.hello_label = SubtitleLabel("欢迎，")
        self.title_label = TitleLabel("团委管理员")
        # 两行标题收在一个盒子里：默认布局会在它们之间留一整行空隙
        greet = QWidget()
        greet_lay = QVBoxLayout(greet)
        greet_lay.setContentsMargins(0, 0, 0, 0)
        greet_lay.setSpacing(0)
        greet_lay.addWidget(self.hello_label)
        greet_lay.addWidget(self.title_label)
        body_lay.addWidget(greet)
        body_lay.addSpacing(12)

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
        chart.setBorderRadius(8)
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
        body_lay.addLayout(grid)

        self.summary = note_label("")
        body_lay.addWidget(self.summary)
        body_lay.addStretch(1)

        row = QHBoxLayout()
        row.setContentsMargins(36, 0, 36, 24)
        row.addStretch(1)
        lay.addWidget(body, 1)
        lay.addLayout(row)

        self._apply_font()
        self._load_header()
        self.refresh_stats(animate=False)

    # ---------- 概览 ----------

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_stats()

    def refresh_stats(self, animate: bool = True):
        """概览统计放到后台线程。

        快照要读整份配置（memberCache 装着所有团员）并逐个跑检查，量大时在
        界面线程里做会卡住切页。"""
        from app.services import org_snapshot, store
        from app.ui.page import CallbackWorker

        self._stats_token = getattr(self, "_stats_token", 0) + 1
        token = self._stats_token

        def _snapshot():
            return org_snapshot.snapshot(store.load())

        worker = CallbackWorker(_snapshot, self)
        worker.ok.connect(lambda data: token == self._stats_token
                          and self._apply_stats(data, animate))
        self._workers.append(worker)
        worker.start()

    def _apply_stats(self, data: dict, animate: bool = True):
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

        f = self.title_label.font()
        f.setPixelSize(title_px)
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
        self.title_label.setFont(f)

        f2 = self.hello_label.font()
        f2.setPixelSize(hello_px)
        f2.setWeight(QFont.Weight.Medium)
        f2.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        self.hello_label.setFont(f2)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.banner.setFixedSize(self.scrollWidget.width(),
                                 max(200, int(self.viewport().height() * BANNER_RATIO)))
        self.banner.move(0, 0)
        self.banner.lower()
        self._apply_font()
