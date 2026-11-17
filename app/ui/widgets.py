"""自绘小件：状态徽章、文件状态列表、统计磁贴。

替代表格呈现检查结果：一行一个圆角卡片，状态用配色圆点与柔和标签表达，
不画网格线，深浅主题各自取色。
"""

from __future__ import annotations

from PyQt5.QtCore import QRectF, Qt, QTimer, pyqtSignal as Signal
from PyQt5.QtGui import (QColor, QFont, QLinearGradient, QPainter, QPainterPath,
                           QRadialGradient)
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

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
        # 组织状况概览的分类语气：黄=信息错误，红=欠缴团费，灰=没有报道/档案，
        # 与那里的多段圆环同一套读法
        "ring-identity": (P.RING["identity"], "#1f2937"),
        "ring-fees": (P.RING["fees"], "#ffffff"),
        "ring-report": (P.RING["report"], "#ffffff"),
        "ring-archive": (P.RING["archive"], "#1f2937"),
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
    lbl.setTextColor(*P.text_colors("fg2"))
    return lbl


class StatusRow(CardWidget):
    """一行状态：标题/说明 + 右侧若干指标 + 状态标签。

    状态标签用与组织管理成员标签同款的实心小标签（PillLabel：纯色底白字、4px 圆角），
    不再用左侧圆点 + 浅底软标签的混搭，跨页视觉统一。

    item 结构：{title, note, tone, status, meta: [str], }
    """

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        # 嵌在卡片里的小控件：半径按同心圆从卡片半径推出来，
        # 不是随手减 2——卡片内边距一变，这里会自动跟上
        self.setBorderRadius(P.inner_radius(P.RADIUS["card"], 8))
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
    """官方彩色图标：直接渲染 Microsoft Fluent color SVG 的原始渐变配色。

    素材见 app/ui/icons.py。这里不画底板、不给图标染色，深浅主题都用同一份
    官方配色。"""

    def __init__(self, name: str = "", size: int = 32, parent=None):
        super().__init__(parent)
        self._name = name
        self.setFixedSize(size, size)

    def paintEvent(self, e):
        if not self._name:
            return
        from app.ui.icons import native_pixmap

        pm = native_pixmap(self._name, max(self.width(), self.height()))
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
                 icon: str = "", tone: str = "info", parent=None):
        super().__init__(parent)
        self.setBorderRadius(P.RADIUS["card"])
        self.setClickEnabled(True)
        self._tone = tone
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.icon_box = None
        if icon:
            self.icon_box = BadgeIcon(icon)
            head.addWidget(self.icon_box, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(theme_font(BodyLabel(title), P.FONT["body"], int(QFont.Weight.DemiBold)))
        head.addStretch(1)
        lay.addLayout(head)

        self.value_lbl = theme_font(TitleLabel(value), P.FONT["metric"], int(QFont.Weight.Bold))
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
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)

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


class ProgressToast(QWidget):
    """右下角浮动进度条：跑批量任务时贴在窗口右下角，不挡页面也不锁交互。

    形态参考 Ghost Downloader 的下载提示：一行「标题 + 进度百分比 + 关闭」，
    底部一条细进度线。任务结束调用 finish()：补满 100% 后短暂停留再自行关闭。
    """

    closed = Signal()

    MARGIN = 16
    CARD_W, CARD_H = 320, 52
    PAD = 10          # 四周留给投影的空白：靠阴影和页面分开，不画描边

    def __init__(self, title: str, parent=None, on_close=None):
        super().__init__(parent)
        self._title = title
        self._done = 0
        self._total = 0
        self._indeterminate = False
        self._phase = 0.0
        self._on_close = on_close
        self.setFixedSize(self.CARD_W + self.PAD * 2, self.CARD_H + self.PAD * 2)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        from PyQt5.QtWidgets import QGraphicsDropShadowEffect

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 90 if isDarkTheme() else 55))
        self.setGraphicsEffect(shadow)
        self._shadow = shadow

        lay = QHBoxLayout(self)
        # 文字这一行让开底部的进度线，垂直重心略偏上
        lay.setContentsMargins(self.PAD + 12, self.PAD + 4,
                               self.PAD + 8, self.PAD + 16)
        lay.setSpacing(8)
        # 进度文本会高频跳数（42/260（16%）），等宽字族避免宽度抖动
        self._label = tabular_label(BodyLabel(title, self))
        lay.addWidget(self._label, 1)
        from qfluentwidgets import TransparentToolButton

        self._close_btn = TransparentToolButton(FluentIcon.CLOSE, self)
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.clicked.connect(self._request_close)
        lay.addWidget(self._close_btn, 0)

        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self.close)

        # 总量未知时进度线改成来回扫的一段（检查更新这种没有百分比的活儿）
        self._marquee = QTimer(self)
        self._marquee.setInterval(16)
        self._marquee.timeout.connect(self._advance_marquee)

        # 绑定方法而非 lambda：浮条销毁后 qconfig 仍会发信号，
        # lambda 会让它打进悬空对象（PyQt5 里直接 abort）
        qconfig.themeChanged.connect(self.update)
        if parent is not None:
            parent.installEventFilter(self)
        self._reposition()

    # ---------- 进度 ----------

    def set_progress(self, done: int, total: int, text: str | None = None):
        """确定总量：done/total 之外可以给一句自定文案（如「正在下载 42%」）。"""
        self._indeterminate = False
        self._marquee.stop()
        self._done, self._total = done, max(0, total)
        pct = int(min(done, self._total) / self._total * 100) if self._total else 0
        if text is not None:
            self._label.setText(text)
        else:
            self._label.setText(f"{self._title} {done}/{self._total}（{pct}%）" if self._total
                                else self._title)
        self.update()

    def set_indeterminate(self, text: str | None = None):
        """总量未知：进度线画成一段来回扫的光条，表示还在跑。"""
        self._indeterminate = True
        self._total = self._done = 0
        self._phase = 0.0
        self._label.setText(text or self._title)
        self._marquee.start()
        self.update()

    def _advance_marquee(self):
        self._phase = (self._phase + 0.008) % 1.0
        self.update()

    def finish(self, hold_ms: int = 800, text: str | None = None):
        """补满并停留一小会儿再关，让「已完成」有个被看见的瞬间。"""
        self._marquee.stop()
        self._indeterminate = False
        if text:
            self._label.setText(text)
        elif self._total:
            self._label.setText(f"{self._title}（100%）")
        self._done = self._total
        self.update()
        self._close_timer.start(hold_ms)

    # ---------- 位置与外观 ----------

    def eventFilter(self, obj, e):
        from PyQt5.QtCore import QEvent

        if obj is self.parentWidget() and e.type() == QEvent.Type.Resize:
            self._reposition()
        return super().eventFilter(obj, e)

    def _reposition(self):
        """贴住右下角：按卡片本身（不含投影留白）算边距，跟页面留 16px。"""
        parent = self.parentWidget()
        if parent is None:
            return
        self.move(parent.width() - self.width() + self.PAD - self.MARGIN,
                  parent.height() - self.height() + self.PAD - self.MARGIN)
        self.raise_()

    def show(self):
        super().show()
        self._reposition()
        self.raise_()

    def _request_close(self):
        self._close_timer.stop()
        if self._on_close is not None:
            self._on_close()
        self.close()

    def closeEvent(self, e):
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
        self._marquee.stop()
        self.closed.emit()
        super().closeEvent(e)

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = isDarkTheme()
        card = QRectF(self.rect()).adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)

        # 卡片不描边：深色主题上一圈 1px 浅色描边会显成「白边」，
        # 边界交给投影（QGraphicsDropShadowEffect）来交代
        path = QPainterPath()
        path.addRoundedRect(card, 10, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(45, 45, 48) if dark else QColor(252, 252, 253))
        painter.drawPath(path)

        # 进度轨道收进卡片内侧：贴边画会跟卡片底缘混成一条多出来的线
        track = QRectF(card.left() + 12, card.bottom() - 9, card.width() - 24, 3)
        painter.setBrush(QColor(255, 255, 255, 30) if dark else QColor(0, 0, 0, 24))
        painter.drawRoundedRect(track, 1.5, 1.5)

        if self._indeterminate:
            span = track.width() * 0.34
            x = track.left() - span + (track.width() + span) * self._phase
            painter.save()
            painter.setClipRect(track)
            painter.setBrush(QColor(P.BRAND))
            painter.drawRoundedRect(QRectF(x, track.top(), span, track.height()), 1.5, 1.5)
            painter.restore()
        elif self._total:
            ratio = min(1.0, self._done / self._total)
            if ratio > 0:
                fill = QRectF(track)
                fill.setWidth(max(6.0, track.width() * ratio))
                painter.setBrush(QColor(P.BRAND))
                painter.drawRoundedRect(fill, 1.5, 1.5)


_MONO_FAMILY: str | None = None


def mono_family() -> str:
    """数字等宽所需的字族名。

    内置字体 `MiSans` 的数字本来就是等宽字形（打包时把 cmap 的 0–9 指到 `.tf`），
    中文与字母仍是比例字形，所以界面正常运行时不需要换字族。这个函数只用于
    兜底：字体文件缺失时界面会落回系统比例字体，数字又会变宽变窄。
    """
    try:
        from app.setup import register_fonts

        family = register_fonts()
        if family:
            return family
    except Exception:
        pass
    return "Courier New"


def tabular_label(label):
    """给会跳数的标签钉住数字等宽。

    规范要求动态数值用 tabular-nums。PyQt5 5.15 没有 `QFont.setFeature`，开不了
    OpenType 的 `tnum`，所以换成打包时就烧进 cmap 的等宽数字字形——效果等同于
    开 tnum，且不依赖运行时特性开关。正常运行时标签已经是内置字体，这里不做改动；
    只有字体没注册上（文件缺失）才换系统等宽族，避免数字照旧抖。
    """
    f = label.font()
    family = mono_family()
    if family and f.family() != family:
        f.setFamily(family)
        label.setFont(f)
    return label
