"""轻量 QPainter 图表组件（深浅色自适应）。"""

from __future__ import annotations

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QWidget

from qfluentwidgets import isDarkTheme, qconfig

from app.ui import palette as P


def _bind_theme(widget: QWidget) -> None:
    """跟随 fluent 明暗主题。不能用 palette().window() 判断——页面滚动容器
    背景透明时调色板 window 色是透明黑（lightness=0），浅色模式会被误判成
    暗色而画出白字；主题切换时刷新重绘。"""
    qconfig.themeChanged.connect(widget.update)


class BarRowWidget(QWidget):
    """横向条形图（单条）：标签 + 轨道条 + 数值。多条由页面纵向排布。"""

    def __init__(self, label: str, color: str, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = QColor(color)
        self._value = 0
        self._display = 0.0
        self._maximum = 1
        _bind_theme(self)
        self.setFixedHeight(34)
        self.setMinimumWidth(360)

    def set_value(self, value: int, maximum: int | None = None):
        """maximum 为本次调用的权威分母（不足 value 时抬到 value）；不传则沿用旧分母只增不减。"""
        self._value = value
        self._display = float(value)
        if maximum is not None:
            self._maximum = max(maximum, value, 1)
        else:
            self._maximum = max(self._maximum, value, 1)
        self.update()

    def set_value_animated(self, value: int, maximum: int | None = None,
                           duration: int = 520):
        """详情页打开时条形从零增长，数字同步滚动。"""
        from app.ui.anim import value_animation

        self._value = value
        if maximum is not None:
            self._maximum = max(maximum, value, 1)
        else:
            self._maximum = max(self._maximum, value, 1)
        start = self._display
        value_animation(self, start, float(value),
                        lambda v: (setattr(self, "_display", float(v)), self.update()),
                        duration=duration)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = isDarkTheme()
        fg = P.fg("fg")
        dim = P.fg("fg2")
        track = QColor(255, 255, 255, 28) if dark else QColor(0, 0, 0, 18)

        label_w, value_w = 76, 40
        h = 8
        y = (self.height() - h) / 2
        bar_w = self.width() - label_w - value_w - 16

        p.setPen(QPen(fg, 1))
        f = p.font()
        f.setPixelSize(P.FONT["caption"])
        p.setFont(f)
        p.drawText(0, 0, label_w - 8, self.height(), Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(int(label_w), int(y), int(bar_w), h, h / 2, h / 2)
        ratio = min(self._display / self._maximum, 1.0)
        if ratio > 0:
            # 语义色保留色相，压低不透明度，避免四条饱和色铺满抢走数字焦点
            fill = QColor(self._color)
            fill.setAlphaF(0.80 if not dark else 0.74)
            p.setBrush(fill)
            path = QPainterPath()
            path.addRoundedRect(label_w, y, max(bar_w * ratio, h), h, h / 2, h / 2)
            p.drawPath(path)
        p.setPen(QPen(dim, 1))
        p.drawText(self.width() - value_w, 0, value_w, self.height(),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   str(int(round(self._display))))


class ProgressDonut(QWidget):
    """圆环进度：已上传/总数。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ratio = 0.0
        self._text = "—"
        self._sub = ""
        _bind_theme(self)
        self.setFixedSize(132, 132)

    def set_value(self, uploaded: int, total: int):
        self._ratio = uploaded / total if total else 0.0
        self._text = f"{uploaded}/{total}" if total else "—"
        self._sub = "已上传" if total else "暂无数据"
        self.update()

    def set_value_animated(self, uploaded: int, total: int, duration: int = 520):
        """页面打开时圆环从零转起；文字直接到位，弧长走补间。"""
        from app.ui.anim import value_animation

        self.set_value(uploaded, total)
        target = self._ratio

        def _step(v):
            self._ratio = target * float(v)
            self.update()

        value_animation(self, 0.0, 1.0, _step, duration=duration)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = isDarkTheme()
        fg = P.fg("fg")
        track = QColor(255, 255, 255, 30) if dark else QColor(0, 0, 0, 15)
        accent = P.fg("brand")

        rect = self.rect().adjusted(10, 10, -10, -10)
        pen = QPen(track, 10)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)
        if self._ratio > 0:
            pen.setColor(accent)
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, -int(360 * 16 * self._ratio))

        # 两行文字作为一个整体在圆环内水平垂直居中
        f = p.font()
        f.setPixelSize(P.FONT["title"])
        f.setBold(True)
        p.setFont(f)
        fm_num = p.fontMetrics()
        sub_font = p.font()
        sub_font.setPixelSize(P.FONT["caption"])
        sub_font.setBold(False)
        fm_sub = QFontMetrics(sub_font)
        gap = 4
        total_h = fm_num.height() + gap + fm_sub.height()
        top = (self.height() - total_h) // 2
        p.setPen(QPen(fg, 1))
        p.drawText(QRect(0, top, self.width(), fm_num.height()),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, self._text)
        p.setFont(sub_font)
        dim = P.fg("fg2")
        p.setPen(QPen(dim, 1))
        p.drawText(QRect(0, top + fm_num.height() + gap, self.width(), fm_sub.height()),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, self._sub)


class SegmentedDonut(QWidget):
    """多段圆环：一个总数按类别切成若干彩色扇段。

    qfluentwidgets 只有单值的 ProgressRing，画不了「同一个环上分色表示几类」，
    所以自绘（与上面 BarRowWidget 同一套做法）。圆心放总数，环上每段按 `segments`
    的占比铺开，段间留缝以便分辨；`missing` 用中性灰占位，表示总数里这部分没有报道。
    """

    GAP_DEGREES = 2.0   # 段间缝：不留缝时纯色相接分界看不清，留太多又会吃掉细段
    MIN_SPAN = 3.5      # 非零段的最小可见角度
    # 260 人基数下 4 人的段只有 5.5°，读者几乎看不出来；给每个非零段一个
    # 可见下限，占比仍然按真实人数算（圆心与图例给准确数字）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments: list[tuple[str, int, QColor]] = []
        self._total = 0
        self._missing = 0
        self._caption = "总团员"
        self._progress = 1.0
        _bind_theme(self)
        self.setFixedSize(150, 150)

    def set_data(self, total: int, segments: list[tuple[str, int, str]],
                 missing: int = 0, caption: str = "总团员"):
        """segments: [(名称, 人数, 颜色)]；missing: 总数里「没有报道」的人数。"""
        self._total = max(int(total), 0)
        self._missing = max(int(missing), 0)
        self._caption = caption
        self._segments = [(name, max(int(value), 0), QColor(color))
                          for name, value, color in segments]
        self.update()

    def set_progress(self, ratio: float):
        """入场动画：0→1 时环上各段一起长出来。"""
        self._progress = max(0.0, min(1.0, ratio))
        self.update()

    def segments_snapshot(self) -> list[tuple[str, int]]:
        """给图例用，顺序与环上一致。"""
        return [(name, value) for name, value, _ in self._segments]

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = isDarkTheme()

        # 环身给厚一些：细环上分段的颜色几乎读不出来
        ring = self.rect().adjusted(8, 8, -8, -8)
        width = 17

        pen = QPen(QColor(255, 255, 255, 26) if dark else QColor(0, 0, 0, 14), width)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.drawArc(ring, 0, 360 * 16)

        # 分母用总数（缺口也占位），扇段长度才能直接读作占比
        denom = max(self._total, 1)
        gap = self.GAP_DEGREES
        start = 90.0                      # 12 点方向起步，顺时针
        for _name, value, color in self._segments:
            if value <= 0:
                continue
            span = 360.0 * value / denom * self._progress
            if span <= 0:
                continue
            # 非零段不低于可见下限；极细的段不缩缝，否则 1 个人会被缝隙吃没
            span = max(span, self.MIN_SPAN * self._progress) if value > 0 else span
            shrink = gap if span > gap * 2 else 0.0
            pen = QPen(color, width)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            p.drawArc(ring, int((start - shrink / 2) * 16), int(-(span - shrink) * 16))
            start -= span

        if self._missing > 0:
            span = 360.0 * self._missing / denom * self._progress
            pen = QPen(QColor(255, 255, 255, 46) if dark else QColor(0, 0, 0, 30), width)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            if span > gap * 2:
                p.drawArc(ring, int((start - gap / 2) * 16), int(-(span - gap) * 16))

        self._draw_center(p)

    def _draw_center(self, p: QPainter):
        num_font = p.font()
        num_font.setPixelSize(P.FONT["metric"])
        num_font.setBold(True)
        cap_font = p.font()
        cap_font.setPixelSize(P.FONT["caption"])

        fm_num = QFontMetrics(num_font)
        fm_cap = QFontMetrics(cap_font)
        gap = 3
        total_h = fm_num.height() + gap + fm_cap.height()
        top = (self.height() - total_h) // 2

        p.setFont(num_font)
        p.setPen(QPen(P.fg("fg"), 1))
        p.drawText(QRect(0, top, self.width(), fm_num.height()),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   str(self._total) if self._total else "—")
        p.setFont(cap_font)
        p.setPen(QPen(P.fg("fg2"), 1))
        p.drawText(QRect(0, top + fm_num.height() + gap, self.width(), fm_cap.height()),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   self._caption)
