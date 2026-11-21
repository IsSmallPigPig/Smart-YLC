"""上传页的自包含控件：拖放空态、拖放遮罩、来源目录卡片。

从 upload_page.py 拆出，因为这些控件只依赖 Qt / palette / note_label，
不碰 UploadPage 的 self 状态；留在页面文件里只是把它撑到 1400 多行。
类名带下划线，仅在本页内部使用。
"""

from __future__ import annotations

from PyQt5.QtCore import QRectF, Qt, pyqtSignal as Signal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    CardWidget,
    FluentIcon as FIF,
    IconWidget,
    StrongBodyLabel,
    ToolButton,
    isDarkTheme,
)

from app.ui import palette as P
from app.ui.page import note_label, page_bg_color, set_tip
from app.ui.widgets import PillLabel


class _DropZone(QWidget):
    """空态拖放区：虚线框 + 两行说明，点一下等同「添加文件夹」。"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(112)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(4)
        title = note_label("拖入文件夹")
        f = title.font()
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.DemiBold)
        title.setFont(f)
        # 这是空态的主文案，不能跟说明一样灰：毛玻璃上会整行消失。
        # 颜色取 palette 的前景 token，不在页面里另写一套 hex
        title.setTextColor(*P.text_colors("fg"))
        lay.addWidget(title, 0, Qt.AlignmentFlag.AlignHCenter)
        hint = note_label("支持多选，自动扫描子文件夹")
        hint.setWordWrap(False)   # 居中单行，折行会把这个框撑成两截
        lay.addWidget(hint, 0, Qt.AlignmentFlag.AlignHCenter)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(P.MUTED))
        pen.setWidth(1)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setColor(QColor(154, 160, 172) if not isDarkTheme() else QColor(96, 102, 112))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class _DropOverlay(QWidget):
    """拖动文件夹经过窗口时盖在内容区的提示层：虚线框 + 一句话说明松手会发生什么。

    文字直接画在 paintEvent 里（不用子控件）：这一层只活几百毫秒，
    交给布局反而会因为它自己的尺寸变化把文案裁掉半行。"""

    TITLE = "松开添加这些文件夹"
    HINT = "子文件夹自动扫描，重复目录跳过"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        box = self.rect().adjusted(10, 10, -10, -10)
        p.setPen(Qt.PenStyle.NoPen)
        # 不透明页面底色：底下那层空态提示的字会从半透明里透出来，两层字叠在一起
        p.setBrush(page_bg_color())
        p.drawRoundedRect(box, 14, 14)

        pen = QPen(QColor(P.BRAND))
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box.adjusted(2, 2, -2, -2), 14, 14)

        ink = P.fg("fg")
        dim = P.fg("fg2")

        title_font = QFont(self.font())
        title_font.setPixelSize(15)
        title_font.setWeight(QFont.Weight.DemiBold)
        hint_font = QFont(self.font())
        hint_font.setPixelSize(12)

        # 标题底边、说明顶边各自贴住中线留 4px：两行才不会挤到一起去
        title_rect = QRectF(box.left(), box.top(), box.width(), box.height() / 2 - 4)
        p.setFont(title_font)
        p.setPen(ink)
        p.drawText(title_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                   self.TITLE)

        hint_rect = QRectF(box.left(), box.center().y() + 4, box.width(), 40)
        p.setFont(hint_font)
        p.setPen(dim)
        p.drawText(hint_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   self.HINT)


class _FolderCard(CardWidget):
    """来源目录里的一张普通卡片：文件夹名、完整路径、份数与移除。

    折叠只发生在「来源目录」这一层；具体文件夹不再各自展开一份文件清单——
    文件已经在下面的支部分组里，再列一遍只会让页面更长。
    """

    def __init__(self, name: str, path: str, tags: list[tuple[str, str]],
                 on_remove=None, parent=None):
        super().__init__(parent)
        self.setBorderRadius(P.RADIUS["control"])
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 10, 10)
        row.setSpacing(12)

        icon = IconWidget(FIF.FOLDER)
        icon.setFixedSize(20, 20)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.title_label = StrongBodyLabel(name or "未命名目录")
        f = self.title_label.font()
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.DemiBold)
        self.title_label.setFont(f)
        text.addWidget(self.title_label)
        self.desc_label = note_label(path)
        text.addWidget(self.desc_label)
        row.addLayout(text, 1)

        self._tags = []
        for text_value, tone in tags:
            pill = PillLabel(str(text_value), tone)
            row.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
            self._tags.append(pill)

        if on_remove is not None:
            remove = ToolButton(FIF.DELETE)
            set_tip(remove, "移除这个来源目录（磁盘文件不动）")
            remove.clicked.connect(lambda _=False: on_remove())
            row.addWidget(remove, 0, Qt.AlignmentFlag.AlignVCenter)
