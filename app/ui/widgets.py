"""自绘小件：状态徽章、文件状态列表、统计磁贴。

替代表格呈现检查结果：一行一个圆角卡片，状态用配色圆点与柔和标签表达，
不画网格线，深浅主题各自取色。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QRadialGradient
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CaptionLabel,
    FluentIcon,
    IconWidget,
    StrongBodyLabel,
    TitleLabel,
    isDarkTheme,
    qconfig,
)

from app.ui.page import note_label, theme_font

from app.ui import palette as P

TONE_COLOR = {
    "ok": P.OK,
    "warn": P.WARNING,
    "error": P.DANGER,
    "info": P.BRAND,
    "muted": P.MUTED,
}


def tint(color: str, alpha: int = 26) -> str:
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


class PillLabel(QWidget):
    """自绘胶囊标签：底色与文字色在 paintEvent 里按当前主题现算。

    样式表会随主题整体重算，是切换深浅色时最贵的一环；标签数量又多，
    所以这里不用 setStyleSheet，主题变化只需 update() 重画一次。
    """

    TONES = {
        # 基础语气
        "blue": (P.BRAND, "#ffffff"),
        "gray": ("#4b5563", "#ffffff"),
        "darkgray": (P.MUTED, "#ffffff"),
        "orange": (P.WARNING, "#ffffff"),
        "green": (P.OK, "#ffffff"),
        "red": (P.DANGER, "#ffffff"),
        # 结果语气（与 TONE_COLOR 同键，供上传状态行复用）
        "ok": (P.OK, "#ffffff"),
        "warn": (P.WARNING, "#ffffff"),
        "error": (P.DANGER, "#ffffff"),
        "muted": (P.MUTED, "#ffffff"),
        "info": (P.BRAND, "#ffffff"),
        # 异常分类（与组织管理成员标签同色，保证跨页一致）
        "identity": (P.KIND["identity"], "#ffffff"),
        "report": (P.KIND["report"], "#ffffff"),
        "archive": (P.KIND["archive"], "#ffffff"),
        "fees": (P.KIND["fees"], "#ffffff"),
    }

    def __init__(self, text: str = "", tone: str = "gray", parent=None):
        super().__init__(parent)
        self._text = text
        self._tone = tone
        self._dim = False
        self._fixed_w: int | None = None
        font = self.font()
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Medium)
        self.setFont(font)
        self._sync_size()
        qconfig.themeChanged.connect(self._on_theme)

    def set_tone(self, tone: str):
        self._tone = tone
        self._sync_size()
        self.update()

    def text(self) -> str:
        return self._text

    def set_text(self, text: str):
        self._text = text
        self._sync_size()
        self.update()

    def set_fixed_width(self, width: int | None):
        """固定宽度：多行排成一列时用，文字放不下就省略号收尾。"""
        self._fixed_w = width
        self._sync_size()

    def _sync_size(self):
        fm = self.fontMetrics()
        if self._fixed_w:
            self.setFixedSize(self._fixed_w, 21)
        else:
            self.setFixedSize(fm.horizontalAdvance(self._text) + 20, 21)

    def _on_theme(self, *_):
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg, fg = self.TONES.get(self._tone, self.TONES["gray"])
        # 深色下灰底提亮一档，避免灰块在深底上过重；彩色标签保持原色
        color = QColor(bg)
        if self._tone in ("gray", "darkgray"):
            color = color.lighter(125) if isDarkTheme() else QColor(bg)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        # 4px 圆角小标签，与组织管理成员标签（border-radius:4px）一致，不用满圆药丸
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 4, 4)
        p.setPen(QColor(fg))
        text = self._text
        if self._fixed_w:
            text = self.fontMetrics().elidedText(
                self._text, Qt.TextElideMode.ElideRight, self._fixed_w - 12)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)


def muted(text: str) -> CaptionLabel:
    lbl = CaptionLabel(text)
    lbl.setTextColor(QColor("#3f4751"), QColor("#7e8896"))
    return lbl


class StatusRow(CardWidget):
    """一行状态：标题/说明 + 右侧若干指标 + 状态标签。

    状态标签用与组织管理成员标签同款的实心小标签（PillLabel：纯色底白字、4px 圆角），
    不再用左侧圆点 + 浅底软标签的混搭，跨页视觉统一。

    item 结构：{title, note, tone, status, meta: [str], }
    """

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.setBorderRadius(6)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 9, 14, 9)
        lay.setSpacing(10)
        self._lay = lay

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(theme_font(StrongBodyLabel(str(item.get("title") or "")),
                                  13, int(QFont.Weight.DemiBold)))
        note = item.get("note")
        self.note_lbl = note_label(str(note)) if note else None
        if self.note_lbl is not None:
            text.addWidget(self.note_lbl)
        lay.addLayout(text, 1)

        for meta in item.get("meta") or []:
            lay.addWidget(muted(str(meta)), 0, Qt.AlignmentFlag.AlignVCenter)

        status = item.get("status")
        self.pill = PillLabel(str(status), item.get("tone", "muted")) if status else None
        if self.pill is not None:
            lay.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_state(self, status: str, tone: str, note: str | None = None):
        if note is not None and self.note_lbl is not None:
            self.note_lbl.setText(note)
        if self.pill is None:
            self.pill = PillLabel(status, tone)
            self._lay.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            self.pill.set_state(status, tone)


class StatusList(QWidget):
    """卡片式状态列表：加一行就多一张小卡片，空列表显示一行灰字。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(6)
        self._rows: list[StatusRow] = []
        self.setMinimumHeight(0)

    def set_items(self, items: list[dict], empty_text: str = "暂无文件"):
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._rows = []
        for data in items:
            row = StatusRow(data)
            self._rows.append(row)
            self._lay.addWidget(row)
        if not items:
            self._lay.addWidget(note_label(empty_text))
        self._lay.addStretch(1)

    def set_row_state(self, index: int, status: str, tone: str, note: str | None = None):
        if 0 <= index < len(self._rows):
            self._rows[index].set_state(status, tone, note)

    def count(self) -> int:
        return len(self._rows)


class BadgeIcon(QWidget):
    """彩色实心图标：直接画 filled 实体图标，不用描边线条。

    素材是 Fluent UI System Icons 的 filled 变体（MIT），存在
    app/assets/icons/ 下，配色由调用方指定。"""

    def __init__(self, name: str = "", color: str = "#0067f4", size: int = 28,
                 parent=None):
        super().__init__(parent)
        self._name = name
        self._color = QColor(color)
        self.setFixedSize(size, size)

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, e):
        if not self._name:
            return
        from app.ui.icons import colored_pixmap

        pm = colored_pixmap(self._name, self._color.name(),
                             max(self.width(), self.height()))
        if pm.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(0, 0, pm)
        p.end()


class StatTile(CardWidget):
    """统计磁贴：fluent 风格的大图标 + 大数字 + 一行说明。

    卡片底子铺一层按色调渐隐的斜向光，图标后面再压一团柔光，磁贴之间
    靠颜色与光照区分，而不是靠边框。
    """

    clicked = Signal()

    def __init__(self, title: str, value: str = "0", note: str = "",
                 icon: str = "", tone: str = "info", parent=None,
                 icon_color: str = ""):
        super().__init__(parent)
        self.setBorderRadius(8)
        self.setClickEnabled(True)
        self._tone = tone
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.icon_box = None
        if icon:
            color = icon_color or TONE_COLOR.get(tone, TONE_COLOR["info"])
            self.icon_box = BadgeIcon(icon, color)
            head.addWidget(self.icon_box, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(theme_font(BodyLabel(title), 14, int(QFont.Weight.DemiBold)))
        head.addStretch(1)
        lay.addLayout(head)

        self.value_lbl = theme_font(TitleLabel(value), 28, int(QFont.Weight.Bold))
        lay.addWidget(self.value_lbl)
        self.note_lbl = note_label(note)
        lay.addWidget(self.note_lbl)

    def _tone_color(self) -> QColor:
        return QColor(TONE_COLOR.get(self._tone, TONE_COLOR.get("muted", "#4b5563")))

    def paintEvent(self, e):
        """卡片底子：斜向淡光 + 图标后的柔光。"""
        super().paintEvent(e)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        tone = self._tone_color()
        grad = QLinearGradient(0, 0, self.width(), self.height())
        head = QColor(tone)
        head.setAlpha(38)
        grad.setColorAt(0, head)
        tail = QColor(tone)
        tail.setAlpha(0)
        grad.setColorAt(1, tail)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)

        if self.icon_box is not None:
            center = self.icon_box.geometry().center()
            glow = QRadialGradient(center, 34)
            color = QColor(tone)
            color.setAlpha(60)
            glow.setColorAt(0, color)
            color.setAlpha(0)
            glow.setColorAt(1, color)
            painter.setBrush(glow)
            painter.drawEllipse(center, 34, 34)
        painter.end()

    def set_value(self, value, note: str | None = None, animate: bool = True):
        text = str(value)
        if note is not None:
            self.note_lbl.setText(note)
        if animate and self.value_lbl.isVisible() and text.isdigit():
            from app.ui.anim import value_animation

            start = self.value_lbl.text()
            try:
                start = float(start)
            except ValueError:
                start = 0.0
            value_animation(self, start, float(text),
                            lambda v: self.value_lbl.setText(str(int(round(float(v))))),
                            duration=420)
        else:
            self.value_lbl.setText(text)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        self.clicked.emit()
