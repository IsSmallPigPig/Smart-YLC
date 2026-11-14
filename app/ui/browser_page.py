"""账号网页：独立的内嵌浏览器窗口，每个团支部一个标签、一份独立存储。

窗口和主窗口一样接管系统标题栏：标签条就在标题栏里，左侧是标签，右侧依次是
后退 / 前进 / 刷新 / 重新登录，再往右留给窗口控制按钮（macOS 是系统原生的红绿灯）。
标签过多时按可用宽度等比缩小，标题文字随之渐隐，不会挤到右侧按钮下面。

站点是单账号单会话：同一个浏览器里多个账号共享 cookie，后登录的会顶掉前一个。
这里为每个账号建独立的 QWebEngineProfile（各自磁盘目录与 cookie 罐），标签之间
互不影响，可以同时保持多个账号在线。

登录在浏览器内完成，不用「接口登录 + 复制 cookie」：
1. 打开登录 GET 接口，由服务端 Set-Cookie 建立会话，正文是结果 JSON
2. 读 JSON 判定结果：成功进首页；要验证码或密码错就落到登录页手动输入
3. 首页载入后读页面正文确认进入系统（还停在密码登录框即判失败）

注意站点根路径 `/` 本身就是登录页，只有 `/bg/index_main.html` 是系统首页——
之前打完接口登录后回根路径，等于一直停在登录页，这是「打开了却没进系统」的原因。
先把该账号存储里的旧 cookie 清掉：旧方案留下的域级 SESSION 与新会话同名，
两条一起到达时服务端读到旧值，页面就弹「登录状态已失效」。
"""

from __future__ import annotations

import json
import os
import sys

from PySide6.QtCore import QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    IconWidget,
    IndeterminateProgressRing,
    PrimaryPushButton,
    TransparentToolButton,
    isDarkTheme,
    qconfig,
)
from qfluentwidgets.window.fluent_window import FluentWindowBase, MSFluentTitleBar

from app.constants import CONFIG_FILE, SITE_BASE_URL
from app.domain.account_health import split_accounts
from app.domain.login_protocol import build_login_url, parse_login_response
from app.domain.upload_inspect import split_branch_label
from app.services import login_gate, store
from app.ui.page import note_label, theme_font, set_tip

PROFILE_ROOT = os.path.join(os.path.dirname(CONFIG_FILE), "webprofiles")
MAX_TABS = 6
SITE_HOME = f"{SITE_BASE_URL}/bg/index_main.html"
TITLE_BAR_HEIGHT = 48
"""macOS 的系统按钮画在标题栏最右侧（宽 75），标题栏内容要提前让开。"""
SYSTEM_BUTTON_INSET = 88 if sys.platform == "darwin" else 8
"""标题栏右侧的导航按钮：后退 / 前进 / 刷新 / 重新登录。"""
NAV_BUTTON = 32

"""页面登录态判定。

站点把失效提示与密码框都放在同一页 DOM 里（改密弹窗也有密码框），只看密码框
会误判；系统首页的 `#name_header` 只有取到当前组织信息后才会有文字，是可靠的
已登录标志。顺序：失效提示 → 组织名 → 密码框 → 路径兜底。"""
LOGIN_CHECK_JS = (
    "(function(){var b=document.body;var t=b?(b.innerText||''):'';"
    "if(t.indexOf('您的登录状态已失效')>=0||t.indexOf('登录状态已失效')>=0)"
    "return 'expired';"
    "var h=document.querySelector('#name_header');"
    "if(h&&(h.innerText||'').trim())return 'ok';"
    "if(document.querySelector('input[type=password]'))return 'login';"
    "return (location.pathname||'').indexOf('index_main')>=0?'ok':'login'})()"
)


def _is_login_page(url: QUrl) -> bool:
    """登录页就是站点根路径（也接受 /bg/index.html）；只有 index_main.html 是系统内。"""
    if "12355.net" not in (url.host() or ""):
        return False
    return (url.path() or "/") in ("/", "/bg/index.html", "/bg/index.htm")


def _is_home_page(url: QUrl) -> bool:
    return "12355.net" in (url.host() or "") and url.path() == "/bg/index_main.html"


class WebTab(QWidget):
    """标题栏里的标签：支部名 + 关闭叉，当前标签有底色。

    宽度由窗口按可用空间分配：变窄时标题逐渐淡出，只留一个可读的最小宽度，
    关闭叉始终在最右侧，不会随标题一起淡掉。"""

    activated = Signal(str)
    closed = Signal(str)

    MIN_WIDTH = 84
    MAX_WIDTH = 216

    def __init__(self, user: str, title: str, parent=None):
        super().__init__(parent)
        self.user = user
        self._title = title or user
        # 标签里只放班名：宽度有限，学段在提示与支部卡片上已有
        self._short = split_branch_label(self._title)[1] or self._title
        self._active = False
        self._hover = False
        font = self.font()
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Medium)
        self.setFont(font)
        self.setFixedHeight(32)
        self.setFixedWidth(self.MAX_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        set_tip(self, f"{self._title}（{user}）")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

    def set_slot_width(self, width: int):
        width = max(self.MIN_WIDTH, min(self.MAX_WIDTH, int(width)))
        if width != self.width():
            self.setFixedWidth(width)
        self.update()

    def set_active(self, value: bool):
        self._active = bool(value)
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if QRect(self.width() - 24, 0, 24, self.height()).contains(e.position().toPoint()):
            self.closed.emit(self.user)
        else:
            self.activated.emit(self.user)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h, dark = self.width(), self.height(), isDarkTheme()
        if self._active:
            bg = QColor(255, 255, 255, 34) if dark else QColor(255, 255, 255)
        elif self._hover:
            bg = QColor(255, 255, 255, 22) if dark else QColor(15, 23, 42, 26)
        else:
            bg = QColor(255, 255, 255, 12) if dark else QColor(15, 23, 42, 14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)
        if self._active:
            # 浅色底上白色标签和窗口底色太接近，加一圈细边才看得出是当前标签
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor(255, 255, 255, 26) if dark else QColor(15, 23, 42, 46))
            p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)
            p.setPen(Qt.PenStyle.NoPen)

        # 标题随宽度渐隐：宽 132 全显示，窄到 84 只剩关闭叉
        fade = max(0.0, min(1.0, (w - self.MIN_WIDTH) / 48))
        if fade > 0:
            fm = self.fontMetrics()
            text_w = max(8, w - 11 - 24)
            p.setOpacity(fade)
            p.setPen(QColor("#f2f3f5") if dark else QColor("#1f2328"))
            p.drawText(QRect(11, 0, text_w, h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       fm.elidedText(self._short, Qt.TextElideMode.ElideRight, text_w))
            p.setOpacity(1.0)

        p.setPen(QColor(205, 208, 214, 220) if dark else QColor(96, 102, 112, 220))
        cx, cy = w - 13, h // 2
        p.drawLine(cx - 3, cy - 3, cx + 3, cy + 3)
        p.drawLine(cx - 3, cy + 3, cx + 3, cy - 3)


class PlusButton(QWidget):
    """标题栏里的新建标签按钮：自绘加号，避开标题栏样式表给按钮加的底色。"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        set_tip(self, "新建标签：选择要访问的支部")
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = isDarkTheme()
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 22) if dark else QColor(15, 23, 42, 26))
            p.drawRoundedRect(self.rect(), 8, 8)
        p.setPen(QColor("#f2f3f5") if dark else QColor("#1f2328"))
        c = self.rect().center()
        p.drawLine(c.x() - 5, c.y(), c.x() + 5, c.y())
        p.drawLine(c.x(), c.y() - 5, c.x(), c.y() + 5)


class ChromeRow(QWidget):
    """标签条空白处可拖动窗口：标题栏被标签占住后仍要能拖。"""

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
                return
        super().mousePressEvent(e)


class LoginView(QWidget):
    """登录过渡页：盖住登录接口返回的那段 JSON，只显示一圈转动的进度环。

    接口页是必须先真正加载一次的（服务端靠这次响应 Set-Cookie 建会话），但它
    的正文就是一段 JSON，露出来很难看，所以整片盖住，等进系统后整层撤掉。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.addStretch(1)
        self.ring = IndeterminateProgressRing(self)
        self.ring.setFixedSize(34, 34)
        lay.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)
        self.label = note_label("正在登录…")
        self.label.setWordWrap(False)  # 换行会把支部名截成两行，单行才能完整显示
        self.label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(12)
        lay.addWidget(self.label, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)

    def set_text(self, text: str):
        self.label.setText(text)

    def paintEvent(self, e):
        """自己铺底色：下面就是那段 JSON 正文，透出来就露馅了。"""
        from app.ui.home_page import page_bg_color

        painter = QPainter(self)
        painter.fillRect(self.rect(), page_bg_color())


class BrowserWindow(FluentWindowBase):
    """账号网页窗口：标题栏里的标签条 + 网页区，只创建一次，后续打开复用。

    关掉窗口不销毁标签，会话与页面都还在，下次打开直接接着用。毛玻璃与主窗口
    同一套开关（translucentMaterial），没有材质时铺纯色底。"""

    _translucent_window = True  # 原生窗口创建前设透明属性（见 main_window 顶部钩子）

    def __init__(self):
        super().__init__()
        self.setWindowTitle("账号网页")
        self.resize(1180, 800)
        self.setMinimumSize(900, 560)
        self.setMicaEffectEnabled(False)
        self.setStyleSheet("BrowserWindow{background: transparent}")
        cfg = store.load()
        from app.services.materials import normalize

        self._material = normalize(cfg.get("translucentMaterial")
                                   or ("acrylic" if cfg.get("translucent") else "none"))
        self._translucent = False
        self._material_applied = False
        self._auto_logins: dict[str, int] = {}
        self._status = ""

        self._views: dict[str, object] = {}
        self._profiles: dict[str, object] = {}
        self._tabs: dict[str, WebTab] = {}
        self._labels: dict[str, str] = {}
        self._state: dict[str, str] = {}  # login | home | checking | ready
        self._loadings: dict[str, LoginView] = {}
        self._current = ""

        # FluentWindowBase 自带一个没进布局的 stackedWidget，它就停在窗口左上角
        # 画一块不透明的白底（截图里左上角那块）。浏览区用的是自建的 stack，
        # 这个一律藏起来。
        self.stackedWidget.hide()
        self.stackedWidget.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._build_title_bar()
        self._build_body()
        self._apply_background()
        qconfig.themeChanged.connect(lambda *_: self.update())
        self._sync_nav()

        # 站内会话超时会弹「登录状态已失效」，每隔一会儿看一眼当前标签，过期就自动重登
        self._poll = QTimer(self)
        self._poll.setInterval(25_000)
        self._poll.timeout.connect(self._poll_session)
        self._poll.start()

    # ---------- 标题栏 ----------

    def _build_title_bar(self):
        self.setTitleBar(MSFluentTitleBar(self))
        self.titleBar.iconLabel.hide()
        self.titleBar.titleLabel.hide()
        lay = self.titleBar.hBoxLayout
        # 标题栏自带的弹簧不再平分宽度：全部富余空间给标签条，窗口按钮才靠最右
        for i in range(lay.count()):
            if lay.itemAt(i).spacerItem() is not None:
                lay.setStretch(i, 0)

        self.tab_row = ChromeRow(self.titleBar)
        row = QHBoxLayout(self.tab_row)
        row.setContentsMargins(10, 0, SYSTEM_BUTTON_INSET, 0)
        row.setSpacing(4)

        # 标签自己排一行；宽度由 _relayout_tabs 按剩余空间算，不参与布局伸缩
        self.tab_lay = QHBoxLayout()
        self.tab_lay.setContentsMargins(0, 0, 0, 0)
        self.tab_lay.setSpacing(4)
        row.addLayout(self.tab_lay, 0)
        self.new_tab_btn = PlusButton(self.tab_row)
        self.new_tab_btn.clicked.connect(self.pick_new_tab)
        row.addWidget(self.new_tab_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)

        # 导航与重新登录贴在窗口按钮左侧：标签再多也不会盖住它们
        self.back_btn = TransparentToolButton(FIF.LEFT_ARROW, self.tab_row)
        set_tip(self.back_btn, "后退")
        self.fwd_btn = TransparentToolButton(FIF.RIGHT_ARROW, self.tab_row)
        set_tip(self.fwd_btn, "前进")
        self.reload_btn = TransparentToolButton(FIF.SYNC, self.tab_row)
        set_tip(self.reload_btn, "重新加载当前页")
        self.login_btn = TransparentToolButton(FIF.GLOBE, self.tab_row)
        set_tip(self.login_btn, "重新登录该支部")
        for btn in (self.back_btn, self.fwd_btn, self.reload_btn, self.login_btn):
            btn.setFixedSize(NAV_BUTTON, NAV_BUTTON)
            row.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.back_btn.clicked.connect(lambda: self._nav("back"))
        self.fwd_btn.clicked.connect(lambda: self._nav("forward"))
        self.reload_btn.clicked.connect(lambda: self._nav("reload"))
        self.login_btn.clicked.connect(self._relogin)

        # 插在图标之后、窗口按钮之前：标签靠左，窗口按钮始终在最右侧
        lay.insertWidget(1, self.tab_row, 1)

    # ---------- 内容 ----------

    def _build_body(self):
        self.hBoxLayout.setContentsMargins(0, TITLE_BAR_HEIGHT, 0, 0)
        body = QWidget(self)
        self.hBoxLayout.addWidget(body, 1)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.progress = QProgressBar(body)
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.setStyleSheet(
            "QProgressBar{border:none;background:transparent;}"
            "QProgressBar::chunk{background:#0067f4;border-radius:1px;}")
        lay.addWidget(self.progress, 0)

        # 浏览区改成手工叠放：标签页之间只换层级，不 hide/show。QStackedWidget 每次
        # 切换都要把网页视图重新显示一次，Chromium 会先出一帧白底，看起来就是闪一下。
        self.area = QWidget(body)
        lay.addWidget(self.area, 1)
        self.area.installEventFilter(self)
        self.empty = self._build_welcome()
        self._place_pages()

    def _build_welcome(self) -> QWidget:
        """欢迎页：打开窗口先看到它，选支部的动作交给按钮或标题栏的 ＋。"""
        box = QWidget(self.area)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(10)
        lay.addStretch(1)
        from PySide6.QtWidgets import QLabel

        from app.ui.brand import app_pixmap

        icon = QLabel(box)
        icon.setPixmap(app_pixmap(56))
        icon.setFixedSize(56, 56)
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        title = theme_font(BodyLabel("还没有打开支部", box), 20, int(QFont.Weight.DemiBold))
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(title, 0, Qt.AlignmentFlag.AlignHCenter)
        self.welcome_hint = note_label("让我们拜访一下支部的同志们")
        self.welcome_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.welcome_hint, 0, Qt.AlignmentFlag.AlignHCenter)
        self.start_btn = PrimaryPushButton(FIF.GLOBE, "开始访问", box)
        self.start_btn.setFixedSize(140, 34)
        self.start_btn.clicked.connect(self.pick_new_tab)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.start_btn, 0)
        row.addStretch(1)
        lay.addSpacing(6)
        lay.addLayout(row)
        lay.addStretch(1)
        return box

    # ---------- 叠放 ----------

    def _pages(self):
        yield self.empty
        for view in self._views.values():
            yield view
        for view in self._loadings.values():
            yield view

    def _place_pages(self):
        rect = self.area.rect()
        for page in self._pages():
            page.setGeometry(rect)

    def _raise_page(self, user: str):
        """把该标签当前该显示的那一层抬到最上面：登录中盖等待页，进系统后露网页。"""
        view = self._views.get(user)
        if view is None:
            self.empty.show()
            self.empty.raise_()
            return
        loading = self._loadings.get(user)
        state = self._state.get(user)
        top = loading if (loading is not None and state in ("login", "home", "checking")) else view
        for page in self._pages():
            if page is top:
                continue
            if page not in self._views.values():
                page.hide()
        top.show()
        top.raise_()

    # ---------- 外部入口 ----------

    def tab_count(self) -> int:
        return len(self._views)

    def prompt(self):
        """侧边栏「访问支部」：打开窗口停在欢迎页，选支部由欢迎页按钮发起。"""
        self.show_window()

    def open_account(self, user: str = ""):
        """打开某个账号的标签；不给账号时沿用当前标签，没有标签就停在欢迎页。"""
        self.show_window()
        if user:
            self._open(user)
        elif self._current:
            self._activate(self._current)

    def shutdown(self):
        """主窗口关闭时调用：关掉全部标签并隐藏窗口，不留一个孤立的浏览器窗口。

        标签页是 Chromium 的独立进程视图，留着最占内存；配置目录里的 Cookie 仍在，
        下次打开重新走登录接口即可。"""
        try:
            self._poll.stop()
        except AttributeError:
            pass
        for user in list(self._views):
            self._close(user)
        self.hide()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        # showNormal 会让 Qt 重设原生窗口样式，这一步必须再补一次（见 _apply_native_chrome）
        QTimer.singleShot(0, self._apply_native_chrome)

    # ---------- 窗口背景 ----------

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._apply_native_chrome)

    def _apply_native_chrome(self):
        """接管标题栏 + 挂毛玻璃。每次显示后都要重来一次。

        `showNormal()`、切标签创建 QWebEngineView 等动作会让 Qt 按自己的窗口
        标志重设一次原生 styleMask，`NSFullSizeContentViewWindowMask` 与透明标题栏
        会被丢掉：结果就是又长出一条系统标题栏，窗口也不再和混合层合成——深色
        模式下窗口底色本身是全透明的，没画东西的地方就变成一块纯黑。
        """
        if sys.platform == "win32":
            # Windows：showNormal 同样会把 Fluent 的 Mica 效果打掉，显示后重挂
            from app.services.materials import apply_windows_effect

            self._material_applied = apply_windows_effect(self, self._material)
            self._translucent = self._material_applied
            self._apply_background()
            self.update()
            return
        if sys.platform != "darwin":
            self._material_applied = True
            return
        try:
            self.updateFrameless()
        except Exception:
            pass
        # 窗口被重建时混合层跟着旧 contentView 一起没了，而 _material_applied 还是
        # True——不校验就会留下一个"透明但没有材质"的窗口，深色下整块发黑。
        if self._material_applied and not self._acrylic_alive():
            self._material_applied = False
            self._acrylic_view = None
            self._acrylic_container = None
        if not self._material_applied:
            # 材质层要等原生窗口稳定后再挂，否则使用初始几何建层会错位
            def _apply(retry: bool = True):
                self.set_material(self._material)
                if not self._material_applied and retry:
                    QTimer.singleShot(200, lambda: _apply(False))

            QTimer.singleShot(150, _apply)


    def _acrylic_alive(self) -> bool:
        """混合层是否还挂在当前窗口上（窗口重建后旧视图会脱离层级）。"""
        view = getattr(self, "_acrylic_view", None)
        if view is None:
            return False
        try:
            return view.superview() is not None
        except Exception:
            return False

    def paintEvent(self, e):
        """没有材质时自己铺底色：Fluent 的透明窗口没人画底就会露出黑色。"""
        super().paintEvent(e)
        if not self._translucent or not self._material_applied:
            from app.ui.home_page import page_bg_color

            painter = QPainter(self)
            painter.fillRect(self.rect(), page_bg_color())

    def set_material(self, material: str):
        """与主窗口同一套毛玻璃开关：none 铺纯色底，其余挂混合层。"""
        from app.services.materials import apply_windows_effect, normalize

        material = normalize(material)
        self._material = material
        if sys.platform == "darwin":
            self._translucent = material != "none"
        elif sys.platform == "win32":
            from app.services.materials import win11_mica_available

            self._translucent = material != "none" and win11_mica_available()
        else:
            self._translucent = False
        self._apply_background()
        if sys.platform == "darwin":
            from app.services.mac_effect import disable_acrylic, enable_acrylic

            if self._translucent:
                self._material_applied = enable_acrylic(self, material)
            else:
                disable_acrylic(self)
                self._material_applied = True
        elif sys.platform == "win32" and self._translucent:
            self._material_applied = apply_windows_effect(self, material)
            if not self._material_applied:
                self._translucent = False
                self._apply_background()
        else:
            self._material_applied = True
        self.update()

    def _apply_background(self):
        if self._translucent:
            # 混合层只负责"透"，亮度由这层底色定：不铺就是桌面颜色，深色桌面下
            # 整窗发黑。浅色铺近白，深色铺中深灰，两层叠加才是毛玻璃的效果。
            # Windows 的 Mica 自带桌面取色，窗口底全透明即可，不再叠半透明层。
            if sys.platform == "win32":
                self.setCustomBackgroundColor(QColor(0, 0, 0, 0), QColor(0, 0, 0, 0))
            else:
                self.setCustomBackgroundColor(QColor(249, 249, 251, 200), QColor(44, 46, 50, 190))
        else:
            self.setCustomBackgroundColor(QColor(240, 244, 249), QColor(32, 32, 32))

    # ---------- 账号 ----------

    def _accounts(self) -> list[dict]:
        return list(store.load().get("accounts") or [])

    def _account(self, user: str) -> dict:
        return next((a for a in self._accounts() if a.get("userName") == user), {}) or {}

    def _label_of(self, user: str) -> str:
        return str(self._account(user).get("remark") or user)

    # ---------- 标签 ----------

    def pick_new_tab(self):
        """弹窗问要访问哪个支部，确认后开它的标签页；只列状态正常的账号。"""
        from app.ui.branch_picker import pick_branch

        if self.tab_count() >= MAX_TABS:
            return
        usable, excluded = split_accounts(self._accounts())
        if not usable:
            # 窗口里不放漂浮提示条：说明就写在欢迎页的说明行上
            self.welcome_hint.setText(
                "没有可用的支部账号" if not excluded
                else "支部账号状态都不正常，先到测活恢复，或在系统配置重新登录")
            self._raise_page("")
            return
        self.welcome_hint.setText("让我们拜访一下支部的同志们")
        pick_branch(usable, self, self._open, self._current)

    def _open(self, user: str):
        if user in self._views:  # 已经开着，直接切过去
            self._activate(user)
            return
        if self.tab_count() >= MAX_TABS:
            return
        acc = self._account(user)
        if not acc or not acc.get("password"):
            return
        self._ensure_view(user)
        self._activate(user)
        self._login(user)

    def _activate(self, user: str):
        view = self._views.get(user)
        if view is None:
            return
        self._current = user
        for name, tab in self._tabs.items():
            tab.set_active(name == user)
        self._raise_page(user)
        self._sync_nav()

    def _close(self, user: str):
        view = self._views.pop(user, None)
        tab = self._tabs.pop(user, None)
        profile = self._profiles.pop(user, None)
        self._labels.pop(user, None)
        self._state.pop(user, None)
        if tab is not None:
            self.tab_lay.removeWidget(tab)
            tab.setParent(None)
            tab.deleteLater()
        loading = self._loadings.pop(user, None)
        if loading is not None:
            loading.setParent(None)
            loading.deleteLater()
        if view is not None:
            view.setParent(None)
            view.deleteLater()
        if profile is not None:
            profile.deleteLater()
        if self._views:
            self._activate(next(reversed(self._views)))
        else:
            self._current = ""
            self._status = ""
            self._raise_page("")
        self._sync_nav()
        self._relayout_tabs()

    def _short_name(self, user: str) -> str:
        """标签与提示统一用班名：学段在 tooltip 与本页标题里已有。"""
        tab = self._tabs.get(user)
        return getattr(tab, "_short", "") or self._labels.get(user, user)

    def _relayout_tabs(self):
        """按可用宽度分配标签宽度：挤不下就等比缩小，最小 84px，右侧按钮永不被盖。"""
        n = len(self._tabs)
        if not n:
            return
        margins = self.tab_row.layout().contentsMargins()
        spacing = self.tab_row.layout().spacing()
        reserved = (self.new_tab_btn.width() + 4 * NAV_BUTTON
                    + spacing * (5 + n))
        space = self.tab_row.width() - margins.left() - margins.right() - reserved
        slot = max(WebTab.MIN_WIDTH, min(WebTab.MAX_WIDTH, space // n))
        for tab in self._tabs.values():
            tab.set_slot_width(slot)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._relayout_tabs)

    def _sync_nav(self):
        view = self._views.get(self._current)
        history = view.history() if view is not None else None
        self.back_btn.setEnabled(bool(history and history.canGoBack()))
        self.fwd_btn.setEnabled(bool(history and history.canGoForward()))
        self.reload_btn.setEnabled(view is not None)
        self.login_btn.setEnabled(view is not None)

    def _nav(self, action: str):
        view = self._views.get(self._current)
        if view is None:
            return
        history = view.history()
        if action == "back" and history.canGoBack():
            history.back()
        elif action == "forward" and history.canGoForward():
            history.forward()
        elif action == "reload":
            view.reload()

    # ---------- 网页视图 ----------

    def _ensure_view(self, user: str):
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView

        storage = os.path.join(PROFILE_ROOT, user)
        os.makedirs(storage, exist_ok=True)
        profile = QWebEngineProfile(f"gd-{user}", self)
        profile.setPersistentStoragePath(storage)
        profile.setCachePath(os.path.join(storage, "cache"))
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)

        view = QWebEngineView(self.area)
        view.setPage(QWebEnginePage(profile, view))
        view.loadProgress.connect(self._on_progress)
        view.loadFinished.connect(lambda ok, u=user: self._on_loaded(u, ok))
        view.urlChanged.connect(lambda url, u=user: self._on_url(u, url))
        view.installEventFilter(self)
        view.setGeometry(self.area.rect())
        self._views[user] = view
        self._profiles[user] = profile

        label = self._label_of(user)
        self._labels[user] = label
        tab = WebTab(user, label, self.tab_row)
        tab.activated.connect(self._activate)
        tab.closed.connect(self._close)
        self._tabs[user] = tab
        self.tab_lay.addWidget(tab, 0, Qt.AlignmentFlag.AlignVCenter)
        QTimer.singleShot(0, self._relayout_tabs)
        # 网页视图是原生图层，创建后会重设一次窗口样式，标题栏要重新接管
        QTimer.singleShot(0, self._apply_native_chrome)
        return view

    def eventFilter(self, obj, event):
        """浏览区尺寸变化时把每一层跟上去。"""
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.Type.Resize and obj is self.area:
            self._place_pages()
        return super().eventFilter(obj, event)

    def _on_progress(self, value: int):
        if value >= 100:
            self.progress.hide()
            return
        self.progress.setValue(value)
        self.progress.show()

    def _on_url(self, user: str, url: QUrl):
        if user == self._current:
            self._sync_nav()
        state = self._state.get(user)
        if state in ("home", "checking") and _is_login_page(url):
            # 站点把会话失效的页面直接跳回登录页，不必等正文检查
            self._after_home(user, "login")
        elif state == "ready" and _is_home_page(url):
            self._set_status(f"{self._labels.get(user, user)}：已进入系统")
            login_gate.mark_browser_session(user, "内嵌浏览器")

    # ---------- 登录 ----------

    def _relogin(self):
        if self._current:
            self._auto_logins[self._current] = 0
            self._login(self._current)

    def _login(self, user: str):
        acc = self._account(user)
        if not acc or not acc.get("password"):
            return
        view = self._views.get(user)
        if view is None:
            return
        self._state[user] = "login"
        self._set_status(f"{self._labels.get(user, user)}：正在登录…")
        self.progress.setValue(0)
        self.progress.show()
        # 登录接口的正文是 JSON，整片盖住换成进度环；接口仍要在网页视图里真加载
        loading = self._loadings.get(user)
        if loading is None:
            loading = LoginView(self.area)
            loading.setGeometry(self.area.rect())
            self._loadings[user] = loading
        loading.set_text(f"正在登录 {self._short_name(user)}…")
        loading.show()
        loading.raise_()
        if user == self._current:
            self._raise_page(user)
        # 旧会话先清掉：域级与主机级两条同名 SESSION 同时到达会被判「登录状态已失效」
        view.page().profile().cookieStore().deleteAllCookies()
        url = build_login_url(user, str(acc.get("password") or ""),
                             str(acc.get("loginValidCode") or ""))
        QTimer.singleShot(300, lambda: self._load_login(user, url))

    def _load_login(self, user: str, url: str):
        """等 Cookie 清完再导航；期间标签可能被关掉，所以要重新取一次 view。"""
        view = self._views.get(user)
        if view is not None:
            view.load(QUrl(url))

    def _on_loaded(self, user: str, ok: bool):
        view = self._views.get(user)
        if view is None:
            return
        state = self._state.get(user)
        if state == "login":
            if not ok:
                self._fail(user, "登录接口没有加载成功")
                return
            view.page().runJavaScript(
                "(document.body&&document.body.innerText)||''",
                lambda text: self._after_login(user, text))
        elif state == "home":
            self._state[user] = "checking"
            QTimer.singleShot(1400, lambda: self._check_home(user))

    def _after_login(self, user: str, text: str):
        """登录接口的正文就是结果 JSON：OK 进首页，其余把原因报出来。"""
        if user not in self._views:
            return
        try:
            verdict = parse_login_response(json.loads(text or ""))
        except (ValueError, TypeError):
            verdict = None
        if verdict is None or verdict.kind != "ok":
            message = verdict.message if verdict is not None else "登录接口没有返回能识别的内容"
            self._fail(user, message)
            self._views[user].load(QUrl(SITE_BASE_URL))  # 落到登录页，可手动输入
            return
        login_gate.mark_browser_session(user, "内嵌浏览器")
        self._state[user] = "home"
        self._set_status(f"{self._labels.get(user, user)}：登录成功，正在进入系统…")
        self._views[user].load(QUrl(SITE_HOME))

    def _check_home(self, user: str):
        view = self._views.get(user)
        if view is None or self._state.get(user) != "checking":
            return
        view.page().runJavaScript(LOGIN_CHECK_JS,
                                  lambda state: self._after_home(user, state))

    def _after_home(self, user: str, state: str):
        label = self._labels.get(user, user)
        if state == "ok":
            self._state[user] = "ready"
            self._auto_logins[user] = 0
            self._set_status(f"{label}：已进入系统")
            login_gate.mark_browser_session(user, "内嵌浏览器")
            if user == self._current:
                self._raise_page(user)  # 进系统了，把等待页收掉露出网页
            return
        reason = "登录状态已失效" if state == "expired" else "还停在密码登录页"
        if self._auto_relogin(user, reason):
            return
        self._state[user] = "ready"
        self._set_status(f"{label}：{reason}，可在页面里手动登录")
        if user == self._current:
            self._raise_page(user)  # 没登进去就露出登录页，让人工输入

    def _auto_relogin(self, user: str, reason: str) -> bool:
        """会话过期就自己重登；连续两次仍不成功才交回给用户（大多是要验证码）。"""
        if self._auto_logins.get(user, 0) >= 2:
            return False
        self._auto_logins[user] = self._auto_logins.get(user, 0) + 1
        self._set_status(f"{self._labels.get(user, user)}：{reason}，正在自动重新登录…")
        self._login(user)
        return True

    def _poll_session(self):
        """定时看一眼当前标签：站点弹「登录状态已失效」时不等用户操作，直接重登。"""
        user = self._current
        view = self._views.get(user) if user else None
        if view is None or not self.isVisible() or self._state.get(user) != "ready":
            return
        view.page().runJavaScript(
            LOGIN_CHECK_JS, lambda state, u=user: self._on_polled(u, state))

    def _on_polled(self, user: str, state: str):
        if self._state.get(user) != "ready" or state == "ok":
            return
        reason = "登录状态已失效" if state == "expired" else "已退出登录"
        self._auto_relogin(user, reason)

    def _fail(self, user: str, message: str):
        self._state[user] = "ready"
        self.progress.hide()
        if user == self._current:
            self._raise_page(user)
        self._set_status(f"{self._labels.get(user, user)}：登录失败，{message}")

    def _set_status(self, text: str):
        """状态只挂在标签的悬停提示上：窗口里不占一行，也不再弹提示条。"""
        self._status = text
        user = self._current
        tab = self._tabs.get(user) if user else None
        if tab is not None:
            set_tip(tab, f"{text}\n{user}" if text else f"{self._labels.get(user, user)}（{user}）")
