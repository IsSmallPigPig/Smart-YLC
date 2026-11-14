"""轻量 QPainter 图表组件（深浅色自适应）。"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from qfluentwidgets import isDarkTheme, qconfig


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
        self._maximum = 1
        _bind_theme(self)
        self.setFixedHeight(34)
        self.setMinimumWidth(360)

    def set_value(self, value: int, maximum: int | None = None):
        if maximum is not None and maximum > self._maximum:
            self._maximum = maximum
        self._value = value
        self._maximum = max(self._maximum, value, 1)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = isDarkTheme()
        fg = QColor("#e5e7eb") if dark else QColor("#111827")
        dim = QColor("#9ca3af") if dark else QColor("#4b5563")
        track = QColor(255, 255, 255, 28) if dark else QColor(0, 0, 0, 18)

        label_w, value_w = 76, 40
        y = self.height() / 2 - 5
        bar_w = self.width() - label_w - value_w - 16
        h = 10

        p.setPen(QPen(fg, 1))
        f = p.font()
        f.setPixelSize(12)
        p.setFont(f)
        p.drawText(0, 0, label_w - 8, self.height(), Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(int(label_w), int(y), int(bar_w), h, 5, 5)
        ratio = min(self._value / self._maximum, 1.0)
        if ratio > 0:
            p.setBrush(self._color)
            path = QPainterPath()
            path.addRoundedRect(label_w, y, max(bar_w * ratio, h), h, 5, 5)
            p.drawPath(path)
        p.setPen(QPen(dim, 1))
        p.drawText(self.width() - value_w, 0, value_w, self.height(),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(self._value))


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

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = isDarkTheme()
        fg = QColor("#e5e7eb") if dark else QColor("#111827")
        track = QColor(255, 255, 255, 30) if dark else QColor(0, 0, 0, 15)
        accent = QColor("#0078d4")

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
        f.setPixelSize(20)
        f.setBold(True)
        p.setFont(f)
        fm_num = p.fontMetrics()
        sub_font = p.font()
        sub_font.setPixelSize(11)
        sub_font.setBold(False)
        fm_sub = QFontMetrics(sub_font)
        gap = 4
        total_h = fm_num.height() + gap + fm_sub.height()
        top = (self.height() - total_h) // 2
        p.setPen(QPen(fg, 1))
        p.drawText(QRect(0, top, self.width(), fm_num.height()),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, self._text)
        p.setFont(sub_font)
        dim = QColor("#9ca3af") if dark else QColor("#4b5563")
        p.setPen(QPen(dim, 1))
        p.drawText(QRect(0, top + fm_num.height() + gap, self.width(), fm_sub.height()),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, self._sub)
