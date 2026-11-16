"""启动画面：在导入 qfluentwidgets、构造主窗口之前先把画面放出来。

双击图标到主窗口出现之间有一段什么都看不到的空档：解释器启动、PySide6 与
qfluentwidgets 导入、六个页面逐个构造。macOS 上量到 1.4 秒，Windows 冷启动
还要叠上杀毒扫描与运行时解压，三四秒里屏幕上没有任何反馈，看起来就像没打开。

这个模块只依赖 PySide6（不碰 qfluentwidgets、不碰 app.ui.page），所以能在
上面那些慢动作之前就被画出来。画面尺寸与主窗口完全一致，主窗口 show 之后它
淡出，替换的是同一块底色的启动界面，看不到接力痕迹。

窗口标志用 SplashScreen：不占任务栏、无边框、置顶，点不到也拖不动。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

CORNER_RADIUS = 10
LOGO = 80


def main_window_rect() -> QRect:
    """主窗口的初始几何：85% 可用区域（上限 1180×760），居中。

    启动画面与主窗口都用这一份，两边各算一次迟早会差几个像素，
    接力时就会看到画面跳一下。"""
    screen = QGuiApplication.primaryScreen().availableGeometry()
    w = min(1180, int(screen.width() * 0.85))
    h = min(760, int(screen.height() * 0.85))
    geo = QRect(0, 0, w, h)
    geo.moveCenter(screen.center())
    return geo


def _is_dark() -> bool:
    """主题还没应用时按配置与系统外观判断，与 setup_app 之后的结果一致。"""
    from app.services import store

    mode = store.load().get("themeMode", "auto")
    if mode == "dark":
        return True
    if mode == "light":
        return False
    hints = QGuiApplication.styleHints()
    return hints.colorScheme() == Qt.ColorScheme.Dark


def page_bg() -> QColor:
    """与 home_page.page_bg_color 同一个公式：窗口底色叠一层半透明白。"""
    dark = _is_dark()
    base = QColor(32, 32, 32) if dark else QColor(240, 244, 249)
    alpha = 8 if dark else 128
    return QColor(
        base.red() + (255 - base.red()) * alpha // 255,
        base.green() + (255 - base.green()) * alpha // 255,
        base.blue() + (255 - base.blue()) * alpha // 255,
    )


def _ui_font(pixel: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(["PingFang SC", "Microsoft YaHei UI", "Segoe UI"])
    f.setPixelSize(pixel)
    f.setWeight(weight)
    return f


class BootSplash(QWidget):
    """一张自绘的启动卡：圆角底 + 图标 + 标题 + 一行说明 + 转圈。"""

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(main_window_rect())

        self._dark = _is_dark()
        self._bg = page_bg()
        self._ink = QColor(226, 232, 240) if self._dark else QColor(18, 33, 46)
        self._sub = QColor(150, 162, 178) if self._dark else QColor(51, 73, 94)
        self._logo = QPixmap()
        self._logo_done = False
        self._angle = 0
        self._finished = False

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    # ---------- 生命周期 ----------

    def start(self) -> None:
        """show 之后调用：只有真正画出第一帧之后才值得转圈。"""
        self._load_logo()
        self._timer.start()

    def finish(self) -> None:
        """主窗口已经画出第一帧，淡出并销毁。"""
        if self._finished:
            return
        self._finished = True
        self._timer.stop()
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation

        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(180)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self.close)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    # ---------- 绘制 ----------

    def _load_logo(self) -> None:
        from app.ui.brand import app_pixmap

        self._logo = app_pixmap(LOGO)
        self._logo_done = True
        self.update()

    def _tick(self) -> None:
        self._angle = (self._angle + 9) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._bg)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), CORNER_RADIUS, CORNER_RADIUS)
        p.drawPath(path)

        cx, cy = self.width() / 2, self.height() / 2
        top = cy - 96

        # 图标：先占位再贴图，图还没解出来时也不会让文字上下跳
        logo_rect = QRectF(cx - LOGO / 2, top, LOGO, LOGO)
        if self._logo_done and not self._logo.isNull():
            p.drawPixmap(logo_rect.toRect(), self._logo)
        top += LOGO + 20

        p.setPen(self._ink)
        p.setFont(_ui_font(20, QFont.Weight.DemiBold))
        p.drawText(QRectF(0, top, self.width(), 30), Qt.AlignmentFlag.AlignCenter, "智慧团委")
        top += 30

        p.setPen(self._sub)
        p.setFont(_ui_font(13))
        p.drawText(QRectF(0, top, self.width(), 22), Qt.AlignmentFlag.AlignCenter,
                   "正在联络各个支部的同志")
        top += 22 + 20

        # 转圈：底环很淡，弧段跟着角度转，颜色与应用主题色一致
        ring = QRectF(cx - 16, top, 32, 32)
        p.setBrush(Qt.BrushStyle.NoBrush)
        track = QPen(QColor(255, 255, 255, 26) if self._dark else QColor(0, 0, 0, 20))
        track.setWidth(3)
        p.setPen(track)
        p.drawEllipse(ring)
        arc = QPen(QColor("#0078d4"))
        arc.setWidth(3)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(arc)
        p.drawArc(ring, int((90 - self._angle) * 16), int(100 * 16))
        p.end()
