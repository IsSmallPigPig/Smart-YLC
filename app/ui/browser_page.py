"""账号网页：独立的内嵌浏览器窗口，每个团支部一个标签、一份独立存储。

窗口和主窗口一样接管系统标题栏：标签条就在标题栏里，用的是 qfluentwidgets
的 TabBar 控件（自带 ＋ 按钮、关闭叉与挤压收缩），右侧依次是后退 / 前进 /
刷新 / 重新登录，再往右留给窗口控制按钮（macOS 是系统原生的红绿灯）。

网页视图用 qframelesswindow 的 FramelessWebEngineView（官方「背景失效解决
办法」的做法）：裸 QWebEngineView 的原生图层会把无边框窗口的背景与材质穿透。

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
import re
import sys
from datetime import datetime

from PyQt5.QtCore import QEvent, QPoint, Qt, QTimer, QUrl, pyqtSignal as Signal
from PyQt5.QtGui import QColor, QDesktopServices, QFont, QPainter
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    InfoBar,
    InfoBarIcon,
    InfoBarPosition,
    IndeterminateProgressRing,
    PrimaryPushButton,
    TabBar,
    TransparentPushButton,
    TransparentToolButton,
    qconfig,
    setFont,
)
from qfluentwidgets.window.fluent_window import FluentWindowBase, MSFluentTitleBar

from app.constants import CONFIG_FILE, SITE_BASE_URL
from app.domain.login_protocol import build_login_url, parse_login_response
from app.domain.upload_inspect import split_branch_label
from app.services import login_gate, store
from app.ui.page import note_label, set_tip, theme_font, window_background

PROFILE_ROOT = os.path.join(os.path.dirname(CONFIG_FILE), "webprofiles")
SHOT_DIR = os.path.join(os.path.dirname(CONFIG_FILE), "screenshots")
MAX_TABS = 6
"""登录等待页最多盖这么久。超时就把网页露出来，别让人对着转圈干等。"""
LOGIN_TIMEOUT_MS = 25_000
SITE_HOME = f"{SITE_BASE_URL}/bg/index_main.html"
TAB_HEIGHT = 36
"""标签与标题栏按钮行的高度。

TabItem 控件默认就是 36 高（qfluentwidgets 给页面内标签的档位），标题栏沿用它
正好不用再压扁；比旧自绘的 32 高一档，文字也更有呼吸感。
"""
TAB_TOP = 5 if sys.platform == "darwin" else 3
"""标签行上沿离窗口顶边的距离。

macOS 的红绿灯由 AppKit 摆在窗口顶部往下约 21px 处，不是标题栏中点；标签行
32 高、上沿留在 5px，它和导航按钮的中线才正好落在系统按钮那条线上。
Windows 的自绘窗口按钮本身就是 32px，上下各留 3px 即居中。
实际数值在 showEvent 里按系统按钮的真实位置再校一次。
"""
# 标签行上下留白相等：标签上沿让出顶边一行，下沿同样留一行。
# 以前只在底部多留几像素（macOS 9px），标签看着偏上、下沿空一块。
TITLE_BAR_HEIGHT = TAB_TOP * 2 + TAB_HEIGHT
"""标题栏总高：macOS 46、Windows 42。"""
# macOS 的系统按钮画在标题栏最右侧（宽 75），标题栏内容要提前让开。
SYSTEM_BUTTON_INSET = 88 if sys.platform == "darwin" else 8
# 标题栏右侧的导航按钮：后退 / 前进 / 刷新 / 重新登录。
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


class BranchTabBar(TabBar):
    """标题栏里的标签条：直接用 qfluentwidgets 的 TabBar，不再自绘。

    routeKey 用账号名；内置的 ＋ 按钮发 `tabAddRequested`、标签上的叉发
    `tabCloseRequested(index)`，这里统一转成账号名再发给窗口。选中底色保持
    与毛玻璃标题栏一致的半透明白——TabBar 默认的浅灰在没有云母的窗口上会
    糊成一块白。

    TabBar 默认整条 46 高、标签字号 12 常规体：12px 中文班名在标题栏里偏小，
    46 的行高又要靠压缩才塞进系统按钮那条中线。这里统一放到 TabItem 的原生
    36 高、字号 13 中黑、圆角 8（与旧自绘标签的观感对齐）。"""

    add_requested = Signal()
    tab_activated = Signal(str)
    tab_closed = Signal(str)

    MIN_WIDTH = 96
    MAX_WIDTH = 230

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(TAB_HEIGHT)
        self.itemLayout.setContentsMargins(5, 0, 5, 0)
        self.setTabMinimumWidth(self.MIN_WIDTH)
        self.setTabMaximumWidth(self.MAX_WIDTH)
        self.setTabsClosable(True)
        self.setTabShadowEnabled(False)
        self.setTabSelectedBackgroundColor(QColor(255, 255, 255, 125),
                                           QColor(255, 255, 255, 50))
        self.tabAddRequested.connect(self.add_requested)
        self.tabCloseRequested.connect(self._emit_closed)
        # TabItem 按下时 TabBar 先更新选中项再发 currentChanged，
        # 从这里取 routeKey 就是刚被点的那个账号
        self.currentChanged.connect(
            lambda i: self.tab_activated.emit(self.tab_route(i)))

    def _emit_closed(self, index: int):
        route = self.tab_route(index)
        if route:
            self.tab_closed.emit(route)

    def add_tab(self, user: str, title: str):
        """新标签：显示班名（学段在提示里），提示带完整支部名与账号。"""
        item = self.addTab(routeKey=user,
                           text=split_branch_label(title)[1] or title)
        # TabItem 默认 12px 常规体、圆角 5：中文班名看着挤，方块感也重。
        # 加宽一档字号、提一档字重、圆角对齐窗口其余控件。
        from app.ui import palette as P

        setFont(item, 13, QFont.Weight.Medium)
        item.setBorderRadius(P.RADIUS["control"])
        self.setTabToolTip(self._index_of(user), f"{title}（{user}）")
        return item

    def set_tab_title(self, user: str, title: str):
        """备注改了就把标签名换掉：标签里只放班名，提示里保留学段与账号。"""
        index = self._index_of(user)
        if index < 0:
            return
        self.setTabText(index, split_branch_label(title)[1] or title)
        self.setTabToolTip(index, f"{title}（{user}）")

    def set_tab_tip(self, user: str, text: str):
        index = self._index_of(user)
        if index >= 0:
            self.setTabToolTip(index, text)

    def tab_route(self, index: int) -> str:
        item = self.tabItem(index)
        return item.routeKey() if item is not None else ""

    def current_user(self) -> str:
        item = self.currentTab()
        return item.routeKey() if item is not None else ""

    def user_at(self, order: int) -> str:
        """按打开顺序取第 order 个标签的账号（越界返回空串）。"""
        if 0 <= order < len(self.items):
            return self.items[order].routeKey()
        return ""

    def last_user(self) -> str:
        """最近打开的标签（最后一个）的账号。"""
        return self.user_at(len(self.items) - 1)

    def has_tab(self, user: str) -> bool:
        return user in self.itemMap

    def _index_of(self, user: str) -> int:
        item = self.tab(user)
        return self.items.index(item) if item is not None else -1




def native_button_center(window) -> int | None:
    """系统红绿灯的垂直中心（从窗口顶部起算，Qt 坐标）。

    AppKit 把它们放在窗口顶部往下约 21px 处，跟 32px 高的标题栏中点差 5px；
    按这个位置居中，标签和导航按钮才和圆点在同一条线上。非 macOS 或窗口
    还没建好时返回 None，调用方按常量兜底。
    """
    if sys.platform != "darwin":
        return None
    try:
        import Cocoa
        import objc

        view = objc.objc_object(c_void_p=window.winId().__int__())
        ns_window = view.window()
        button = ns_window.standardWindowButton_(Cocoa.NSWindowCloseButton)
        if button is None:
            return None
        rect = button.convertRect_toView_(button.bounds(), None)
        return round(window.height() - (rect.origin.y + rect.size.height / 2))
    except Exception:
        return None


def _start_system_move(host: QWidget, global_pos: QPoint) -> bool:
    """把这一下拖动交给系统窗口管理器接管。

    用 qframelesswindow 自带的 startSystemMove：它按平台分派到系统接口
    （macOS 走 AppKit 的 performWindowDragWithEvent:，Windows 走
    ReleaseCapture + WM_NCLBUTTONDOWN），跟手由系统负责，Qt 不参与坐标计算，
    多屏不同缩放、窗口吸附与触控板手势都能正常工作。

    必须在鼠标按下事件里、带上按下的全局坐标调用——系统要在那次事件上下文中
    才接受拖动交接，自己另起一次调用会直接被拒。

    macOS 日志偶尔打印 “Window move completed without beginning”，那是这条事件
    没被系统消费的提示，拖动本身仍然连贯；曾经的绕开办法是自己算位移，
    代价是丢掉系统侧行为，所以这里仍然交回系统。
    """
    window = host.window()
    if window is None:
        return False
    try:
        from qframelesswindow.utils import startSystemMove

        startSystemMove(window, global_pos)
        return True
    except Exception:
        return False


class ChromeRow(QWidget):
    """标签条的空白处：按在空白处就拖动窗口。

    加号、导航按钮都是子控件，自己吃掉按下事件，不受影响。
    拖动交给系统接管（见 _start_system_move），不再自己算位移。

    TabBar 是个例外：它的标签项（TabItem）会把按下事件原样转发给父级——事件
    从标签一路冒泡到这里，按在标签上也会发起系统拖动，标签就点不动了。
    官方示例是在标题栏的 canDrag 里排除 `tabRegion()`，这里是同一思路：
    注册进来的控件所占区域不算拖动区（add_no_drag）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._no_drag: list[QWidget] = []

    def add_no_drag(self, tab_bar: BranchTabBar):
        self._no_drag.append(tab_bar)

    def _in_no_drag(self, pos) -> bool:
        for bar in self._no_drag:
            if not bar.isVisible():
                continue
            # tabRegion 是「有标签的那一段」（控件视口坐标）；标签条右侧的空白
            # 仍然算拖动区，跟旧自绘标签时的手感一致
            region = bar.tabRegion()
            region.moveTopLeft(region.topLeft() + bar.viewport().mapTo(self, QPoint(0, 0)))
            if region.contains(pos):
                return True
        return False

    def mousePressEvent(self, e):
        if (e.button() == Qt.MouseButton.LeftButton
                and not self._in_no_drag(e.pos())
                and _start_system_move(self, e.globalPos())):
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
        # 站点是固定宽度的老式布局，窗口窄了内容会被截断；默认给足宽度，
        # 只有屏幕装不下时才收到可用区域（否则窗口会超出屏幕、标题栏被顶到屏幕外）
        avail = self.screen().availableGeometry()
        self.resize(min(1450, avail.width()), min(870, avail.height()))
        self.setMinimumSize(900, 560)
        self.setMicaEffectEnabled(False)
        self.setStyleSheet("BrowserWindow{background: transparent}")
        cfg = store.load()
        from app.services.materials import preferred

        # 跟主窗口用同一个取值函数：没设置过时是 Mica，而不是「配置里没有这个键
        # 就当关闭」。以前这里自己拼了一遍默认值，结果主窗口毛玻璃、浏览器纯色。
        self._material = preferred(cfg)
        self._translucent = False
        self._material_applied = False
        self._auto_logins: dict[str, int] = {}
        self._status = ""

        self._views: dict[str, object] = {}
        self._profiles: dict[str, object] = {}
        self._labels: dict[str, str] = {}
        self._state: dict[str, str] = {}  # login | home | checking | ready
        self._loadings: dict[str, LoginView] = {}
        # 每个标签登记一份「密码 + 登录验证码」，配置里这两项一变就重登
        self._creds: dict[str, tuple[str, str]] = {}
        # 登录看门狗的分代号：重登一次就 +1，旧定时器到点自动作废
        self._login_token: dict[str, int] = {}
        self._current = ""
        self._engine_warm = False
        # 标题栏高度：内容区要从这里往下让出来，所以只算一次、存在属性上，
        # macOS 实测后再改这两份（见 _sync_title_metrics）
        self._title_height = TITLE_BAR_HEIGHT
        self._tab_top = TAB_TOP
        self._metrics_pending = True   # 等窗口几何稳定后按系统按钮中线重算

        # FluentWindowBase 自带一个没进布局的 stackedWidget，它就停在窗口左上角
        # 画一块不透明的底（截图里左上角那块）。浏览区用的是自建的 stack，
        # 这个一律藏起来——光 hide() 不够：窗口重新显示后它的原生视图会再冒出来，
        # 在左上角压出一块 100×30 的实心区域。把它连同原生视图一起收成 0×0。
        self.stackedWidget.hide()
        self.stackedWidget.setFixedSize(0, 0)
        self.stackedWidget.move(-4, -4)
        self.stackedWidget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.stackedWidget.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._build_title_bar()
        self._build_body()
        self._apply_background()
        # 绑定方法而非 lambda：qconfig 是全局单例，lambda 会替它攥着已销毁窗口的
        # 引用，下次切主题打进悬空对象，PyQt5 里直接 abort
        qconfig.themeChanged.connect(self.update)
        self._sync_nav()

        # 站内会话超时会弹「登录状态已失效」，每隔一会儿看一眼当前标签，过期就自动重登
        self._poll = QTimer(self)
        self._poll.setInterval(25_000)
        self._poll.timeout.connect(self._poll_session)
        self._poll.start()

    # ---------- 标题栏 ----------

    def _build_title_bar(self):
        self.setTitleBar(MSFluentTitleBar(self))
        # 标题栏空白处的拖动交给系统接管（与 ChromeRow 同一套做法）
        self.titleBar.installEventFilter(self)
        # 标题栏高度按系统按钮的中线定（macOS 46，Windows 38）；标签行上沿让出
        # TAB_TOP，行内 32px 的按钮中线就跟红绿灯、加号、后退前进落在同一条线上。
        self.titleBar.setFixedHeight(self._title_height)
        self.titleBar.iconLabel.hide()
        self.titleBar.titleLabel.hide()
        lay = self.titleBar.hBoxLayout
        # 标题栏自带的弹簧不再平分宽度：全部富余空间给标签条，窗口按钮才靠最右
        for i in range(lay.count()):
            if lay.itemAt(i).spacerItem() is not None:
                lay.setStretch(i, 0)

        self.tab_row = ChromeRow(self.titleBar)
        # 行高 = 标签高 + 上沿留白：标签自己用剩下的 32px，垂直居中后正落在
        # 系统按钮那条中线上，多出来的高度全部留给标题栏底部的留白
        self.tab_row.setFixedHeight(TAB_HEIGHT + self._tab_top)
        row = QHBoxLayout(self.tab_row)
        row.setContentsMargins(10, self._tab_top, SYSTEM_BUTTON_INSET, 0)
        row.setSpacing(4)

        # 标签靠左，窗口按钮始终在最右侧；垂直位置由 _sync_title_metrics
        # 按系统按钮中线校准（见 showEvent）——标签与红黄绿按钮要在同一条水平线上。
        # TabBar 自带 ＋ 按钮与关闭叉，标签挤压收缩也由控件自己管
        self.tab_bar = BranchTabBar(self.tab_row)
        self.tab_bar.add_requested.connect(self.pick_new_tab)
        self.tab_bar.tab_activated.connect(self._on_tab_activated)
        self.tab_bar.tab_closed.connect(self._close)
        # 标签项会把按下事件转发到父级（TabItem 的行为），不豁免的话按在标签
        # 上也会发起窗口拖动，标签就点不动了
        self.tab_row.add_no_drag(self.tab_bar)
        # 标签条吃满「左边到导航按钮之间」的宽度：只按 sizeHint 给宽度的话，
        # 标签会被压到内容宽、内置的 ＋ 按钮直接被挤出可见区域（标签宽度渲染
        # 不全、加号消失）。标签本身仍靠左（TabBar 自己的 itemLayout 是 AlignLeft），
        # 多出来的空间由 TabBar 内部的弹簧收掉。
        row.addWidget(self.tab_bar, 1)

        # 导航与重新登录贴在窗口按钮左侧：标签再多也不会盖住它们
        self.back_btn = TransparentToolButton(FIF.LEFT_ARROW, self.tab_row)
        set_tip(self.back_btn, "后退")
        self.fwd_btn = TransparentToolButton(FIF.RIGHT_ARROW, self.tab_row)
        set_tip(self.fwd_btn, "前进")
        self.reload_btn = TransparentToolButton(FIF.SYNC, self.tab_row)
        set_tip(self.reload_btn, "重新加载当前页")
        self.login_btn = TransparentToolButton(FIF.GLOBE, self.tab_row)
        set_tip(self.login_btn, "重新登录该支部")
        self.shot_btn = TransparentToolButton(FIF.CAMERA, self.tab_row)
        set_tip(self.shot_btn, "截图当前网页，自动复制到剪贴板")
        for btn in (self.back_btn, self.fwd_btn, self.reload_btn, self.login_btn,
                    self.shot_btn):
            btn.setFixedSize(NAV_BUTTON, NAV_BUTTON)
            row.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.back_btn.clicked.connect(lambda: self._nav("back"))
        self.fwd_btn.clicked.connect(lambda: self._nav("forward"))
        self.reload_btn.clicked.connect(lambda: self._nav("reload"))
        self.login_btn.clicked.connect(self._relogin)
        self.shot_btn.clicked.connect(self._take_screenshot)

        # 插在图标之后、窗口按钮之前：标签靠左，窗口按钮始终在最右侧。
        # 用 AlignTop 而不是 AlignVCenter：标签行的上沿要压在系统按钮中线上，
        # 多出来的高度留给底部留白（居中会让按钮整体下沉几像素）。
        lay.insertWidget(1, self.tab_row, 1, Qt.AlignmentFlag.AlignTop)

    # ---------- 内容 ----------

    def _build_body(self):
        self.hBoxLayout.setContentsMargins(0, self._title_height, 0, 0)
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
        from PyQt5.QtWidgets import QLabel

        from app.ui.brand import app_pixmap

        icon = QLabel(box)
        icon.setPixmap(app_pixmap(56))
        icon.setFixedSize(56, 56)
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        title = theme_font(BodyLabel("还没有打开支部", box), 20, int(QFont.Weight.DemiBold))
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(title, 0, Qt.AlignmentFlag.AlignHCenter)
        # 标题下的一行灰字：给这页一点人情味，不承担功能说明
        self.welcome_sub = note_label("让我们拜访一下支部的同志们")
        self.welcome_sub.setWordWrap(False)
        self.welcome_sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.welcome_sub, 0, Qt.AlignmentFlag.AlignHCenter)
        # 平时不显示任何说明：图标 + 标题 + 按钮已经说明了这一页要做什么，
        # 只有「没有可用账号」这类不影响按钮的差错才写这一行
        self.welcome_hint = note_label("")
        self.welcome_hint.setWordWrap(False)
        self.welcome_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.welcome_hint.setVisible(False)
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
        QTimer.singleShot(0, self._sync_title_metrics)
        QTimer.singleShot(0, self._apply_native_chrome)
        # 引擎预热已经提前到应用启动后的空闲期（main_window.warm_web_engine）：
        # 以前在这里预热，正好压在「窗口刚露出来」那一拍上，看起来就是打开卡一下。
        # 真没预热过（比如预热期间用户已经点了打开）才补一次，也等窗口画完。
        if not self._engine_warm:
            QTimer.singleShot(600, self._warm_engine)

    def changeEvent(self, e):
        super().changeEvent(e)
        # 最大化 / 还原：Qt 会重设一次原生窗口样式，系统背景跟着掉，
        # 深色下窗口就变成一块纯黑。等几何定下来按显示路径再接管一次。
        if (e.type() == QEvent.Type.WindowStateChange
                and sys.platform == "win32" and self._material_applied):
            QTimer.singleShot(0, self._apply_native_chrome)

    def _sync_title_metrics(self):
        """按系统按钮的真实位置定标签行位置与标题栏高度。

        常量只是兜底：红绿灯的纵向位置由 AppKit 决定，macOS 版本之间会差几个
        像素。读一次实测中线，把标签行上沿摆到「中线 − 半个标签高」，按钮就和
        圆点在同一条线上；标题栏高度取「上沿 + 标签行 + 底部留白」。
        """
        if self.height() < 100:      # 几何还没定，保持常量，别拿错值去改
            self._metrics_pending = True
            return
        center = native_button_center(self)
        if center is None:
            self._metrics_pending = True
            return
        top = max(0, center - TAB_HEIGHT // 2)
        # 上下各留 top：标签中心才落在系统按钮中线上，且两侧间距一致
        height = top * 2 + TAB_HEIGHT
        # 已经对齐且没有待处理标记时跳过；resizeEvent 传来的 pending 会强制重算
        if (top == self._tab_top and height == self.titleBar.height()
                and not getattr(self, "_metrics_pending", False)):
            return
        self._tab_top = top
        self._title_height = height
        self._metrics_pending = False
        self.titleBar.setFixedHeight(height)
        self.hBoxLayout.setContentsMargins(0, height, 0, 0)
        self.tab_row.setFixedHeight(TAB_HEIGHT + top)
        self.tab_row.layout().setContentsMargins(
            10, top, SYSTEM_BUTTON_INSET, 0)

    def _warm_engine(self):
        """预热 Chromium：第一次建 QWebEngineProfile 要初始化引擎。

        实测这一下 170~550ms（打包后冷启动更久），以前它落在「选完支部」的点击处理里，
        整个窗口就定住一段时间。窗口显示后先把它做掉，之后建标签只剩几十毫秒。
        只建默认 profile，不碰各账号的存储分区。"""
        if self._engine_warm:
            return
        from app.services import webengine

        if not webengine.available():
            return
        self._engine_warm = True
        from PyQt5.QtWebEngineWidgets import QWebEngineProfile

        QWebEngineProfile.defaultProfile()

    def _apply_native_chrome(self):
        """接管标题栏 + 挂毛玻璃。每次显示后都要重来一次。

        `showNormal()`、切标签创建 QWebEngineView 等动作会让 Qt 按自己的窗口
        标志重设一次原生 styleMask，`NSFullSizeContentViewWindowMask` 与透明标题栏
        会被丢掉：结果就是又长出一条系统标题栏，窗口也不再和混合层合成——深色
        模式下窗口底色本身是全透明的，没画东西的地方就变成一块纯黑。
        """
        if sys.platform == "win32":
            # Windows：showNormal 同样会把系统背景打掉，显示后重挂；
            # 圆角偏好也一起重设（窗口 frame 被 Qt 重设过）。圆角先设，
            # 它内部的 addShadowEffect 会重设 frame 扩展，放在材质后会顶掉 Mica。
            from app.services.materials import apply_rounded_corners, apply_windows_effect
            from app.ui import palette as P

            # 与主窗口同一个圆角档位：两个窗口各写一个数迟早不一致
            apply_rounded_corners(self, P.RADIUS["window"])
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
            # 材质层等这一拍的原生窗口就位后立刻挂：以前推迟 150ms，窗口先露出
            # 一层不透明底色，看起来就是「打开时不带毛玻璃」。挂不上再重试一次。
            def _apply(retry: bool = True):
                self.set_material(self._material)
                if not self._material_applied and retry:
                    QTimer.singleShot(200, lambda: _apply(False))

            QTimer.singleShot(0, _apply)


    def _acrylic_alive(self) -> bool:
        """混合层是否还挂在当前窗口上（窗口重建后旧视图会脱离层级）。"""
        view = getattr(self, "_acrylic_view", None)
        if view is None:
            return False
        try:
            return view.superview() is not None
        except Exception:
            return False

    def _normalBackgroundColor(self):
        """材质挂上后窗口底色交给系统混合层（与主窗口同一套判断）。

        基类只看 isMicaEffectEnabled()，Win10 的亚克力不经过那个开关，照它判断
        会铺一层不透明底，把模糊整个盖住。macOS 仍走基类（那边要留那层衬纸）。
        """
        if sys.platform == "win32" and getattr(self, "_translucent", False):
            return QColor(0, 0, 0, 0)
        return super()._normalBackgroundColor()

    def paintEvent(self, e):
        """没有材质时自己铺底色：Fluent 的透明窗口没人画底就会露出黑色。"""
        super().paintEvent(e)
        if not self._translucent or not self._material_applied:
            from app.ui.home_page import page_bg_color

            painter = QPainter(self)
            painter.fillRect(self.rect(), page_bg_color())

    def set_material(self, material: str):
        """与主窗口同一套毛玻璃开关：none 铺纯色底，其余挂混合层。"""
        from app.services.materials import (
            apply_rounded_corners,
            apply_windows_effect,
            normalize,
            windows_material_available,
        )

        material = normalize(material)
        self._material = material
        if sys.platform == "darwin":
            self._translucent = material != "none"
        elif sys.platform == "win32":
            self._translucent = windows_material_available(material)
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
            from app.ui import palette as P

            apply_rounded_corners(self, P.RADIUS["window"])
            self._material_applied = apply_windows_effect(self, material)
            if not self._material_applied:
                self._translucent = False
                self._apply_background()
        else:
            self._material_applied = True
        self.update()

    def _apply_background(self):
        self.setCustomBackgroundColor(*window_background(self._translucent))

    # ---------- 账号 ----------

    def _accounts(self) -> list[dict]:
        return list(store.load().get("accounts") or [])

    def _account(self, user: str) -> dict:
        return next((a for a in self._accounts() if a.get("userName") == user), {}) or {}

    def _label_of(self, user: str) -> str:
        return str(self._account(user).get("remark") or user)

    # ---------- 标签 ----------

    def _set_welcome_hint(self, text: str):
        self.welcome_hint.setText(text)
        self.welcome_hint.setVisible(bool(text))

    def _explain(self, text: str):
        """说明为什么没打开。

        没有标签时写在欢迎页上；已经有标签就用消息条——欢迎页是透明的，
        抬到 QWebEngineView 上面会透出下面的网页，看起来像「首页糊在浏览器上」。
        """
        if self._views:
            InfoBar.warning("访问支部", text, duration=5000, parent=self)
            return
        self._set_welcome_hint(text)
        self._raise_page("")

    def pick_new_tab(self):
        """弹窗问要访问哪个支部，只列状态正常的账号。

        没有正常账号时明说原因，不静默返回：以前账号全异常时直接抬起欢迎页，
        正好盖在网页上，看着就是「点了新建标签没反应」。
        """
        from app.ui.branch_picker import normal_accounts, pick_branch

        if self.tab_count() >= MAX_TABS:
            self._explain(f"最多同时打开 {MAX_TABS} 个标签，先关掉一个再来")
            return
        accounts = [a for a in self._accounts() if a.get("userName")]
        if not accounts:
            self._explain("还没有支部账号，先到系统配置添加")
            return
        note = ""
        hidden = len(accounts) - len(normal_accounts(accounts))
        if hidden:
            note = f"已隐藏 {hidden} 个状态异常的账号，先到系统配置处理"
        self._set_welcome_hint("")
        if pick_branch(accounts, self, self._open, self._current, note=note) is None:
            self._explain("没有状态正常的支部账号，先到系统配置测活")

    def _open(self, user: str):
        if user in self._views:  # 已经开着，直接切过去
            self._activate(user)
            return
        if self.tab_count() >= MAX_TABS:
            self._explain(f"最多同时打开 {MAX_TABS} 个标签，先关掉一个再来")
            return
        acc = self._account(user)
        # 下面两种都是「点了没反应」的来源，必须说清为什么没开
        if not acc:
            self._explain("这个账号已经不在系统配置里，先刷新账号列表")
            return
        if not acc.get("password"):
            self._explain("这个账号还没有密码，先到系统配置补上")
            return
        self._set_welcome_hint("")
        self._creds[user] = self._cred_sig(acc)
        # 建 Chromium 视图要初始化引擎（引擎没预热时几百毫秒）。先把等待页铺出来，
        # 点击处理立刻返回，视图留到下一轮事件循环再建——否则选完支部整个窗口定住。
        self._show_loading(user, f"正在打开 {acc.get('remark') or user}…")
        self._set_status(f"{self._label_of(user)}：正在打开…")
        QTimer.singleShot(0, lambda u=user: self._mount_tab(u))

    def _mount_tab(self, user: str):
        """真正建标签：上一轮的空闲里窗口已经画过等待页，这里只干活不再等绘制。"""
        if user in self._views:      # 期间已经被别处打开
            self._activate(user)
            return
        if self.tab_count() >= MAX_TABS:   # 连点两下可能同时排进两个
            self._explain(f"最多同时打开 {MAX_TABS} 个标签，先关掉一个再来")
            return
        # 资源不齐时碰 QtWebEngine 是 int3 崩进程，不是抛异常，这里必须先拦住
        from app.services import webengine

        if not webengine.available():
            self._explain("内置浏览器组件不完整，网页打不开。用最新安装包覆盖安装一次即可恢复。")
            return
        self._ensure_view(user)
        self._activate(user)
        self._login(user)

    def _show_loading(self, user: str, text: str):
        """等待页：盖住浏览区，先给个「正在……」的画面，别让人对着空白。"""
        loading = self._loadings.get(user)
        if loading is None:
            loading = LoginView(self.area)
            loading.setGeometry(self.area.rect())
            self._loadings[user] = loading
        loading.set_text(text)
        loading.show()
        loading.raise_()

    def refresh_accounts(self):
        """把系统里最新的账号信息注入已打开的标签。

        备注改了标签名跟着改；密码或登录验证码变过就用新凭据重登一次，否则页面里
        还是旧会话；账号被删掉就关掉它的标签。没开过标签的账号不用管——下次打开
        本来就读最新配置。"""
        accounts = {str(a.get("userName") or ""): a for a in self._accounts()}
        accounts.pop("", None)
        for user in list(self._views):
            acc = accounts.get(user)
            if acc is None:
                self._close(user)
                profile = self._profiles.pop(user, None)
                if profile is not None:      # 账号没了，存储分区也不会再用
                    profile.deleteLater()
                continue
            label = str(acc.get("remark") or user)
            if label != self._labels.get(user):
                self._labels[user] = label
                self.tab_bar.set_tab_title(user, label)
            sig = self._cred_sig(acc)
            if sig == self._creds.get(user):
                continue
            self._creds[user] = sig
            self._auto_logins[user] = 0
            self._login(user)
        self._sync_nav()

    def _cred_sig(self, acc: dict) -> tuple[str, str]:
        return (str(acc.get("password") or ""), str(acc.get("loginValidCode") or ""))

    def _on_tab_activated(self, user: str):
        """标签条里点选：只在和当前不是同一个时切换（程序里 setCurrentTab 也走这里）。"""
        if user and user != self._current:
            self._activate(user)

    def _activate(self, user: str):
        view = self._views.get(user)
        if view is None:
            return
        self._current = user
        self.tab_bar.setCurrentTab(user)
        self._raise_page(user)
        self._sync_nav()

    def _close(self, user: str):
        view = self._views.pop(user, None)
        self._labels.pop(user, None)
        self._state.pop(user, None)
        self._creds.pop(user, None)
        self._login_token.pop(user, None)
        index = self.tab_bar._index_of(user)
        if index >= 0:
            self.tab_bar.removeTab(index)
        loading = self._loadings.pop(user, None)
        if loading is not None:
            loading.setParent(None)
            loading.deleteLater()
        if view is not None:
            view.setParent(None)
            view.deleteLater()
        if self._views:
            self._activate(self.tab_bar.last_user() or next(reversed(self._views)))
        else:
            self._current = ""
            self._status = ""
            self._raise_page("")
        self._sync_nav()

    def _short_name(self, user: str) -> str:
        """标签与提示统一用班名：学段在 tooltip 与本页标题里已有。"""
        label = self._labels.get(user)
        if label:
            return split_branch_label(label)[1] or label
        return user

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 系统按钮的纵向位置要在窗口几何稳定后再读一次：showEvent 那一拍
        # 若 height() 还小于 100，_sync_title_metrics 会提前返回，
        # 标签行就一直停在常量位置，跟红黄绿按钮差几像素、看着没排齐。
        QTimer.singleShot(0, self._sync_title_metrics)

    def _sync_nav(self):
        view = self._views.get(self._current)
        history = view.history() if view is not None else None
        self.back_btn.setEnabled(bool(history and history.canGoBack()))
        self.fwd_btn.setEnabled(bool(history and history.canGoForward()))
        self.reload_btn.setEnabled(view is not None)
        self.login_btn.setEnabled(view is not None)
        self.shot_btn.setEnabled(view is not None)

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

    # ---------- 截图 ----------

    def _take_screenshot(self):
        """截取当前网页：保存到本地、复制到剪贴板，右下角给出查看与打开目录入口。"""
        view = self._views.get(self._current)
        if view is None:
            InfoBar.warning("截图", "先打开一个支部网页", duration=3000, parent=self)
            return

        # QWebEngineView 的原生图层偶尔让 QWidget.grab() 拿到空图，回退到屏幕抓该窗口
        pixmap = view.grab()
        if pixmap.isNull():
            screen = QApplication.primaryScreen()
            wid = int(view.winId())
            if screen is not None and wid:
                pixmap = screen.grabWindow(wid)
        if pixmap.isNull():
            InfoBar.error("截图失败", "没有拿到网页图像，请重试", duration=4000, parent=self)
            return

        os.makedirs(SHOT_DIR, exist_ok=True)
        label = re.sub(r'[\\/:*?"<>|]', "_",
                       self._labels.get(self._current, self._current))
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(SHOT_DIR, f"{label}-{stamp}.png")
        if not pixmap.save(path, "PNG"):
            InfoBar.error("截图失败", "图片没能保存到本地", duration=4000, parent=self)
            return
        QApplication.clipboard().setPixmap(pixmap)
        self._show_shot_bar(path)

    def _show_shot_bar(self, path: str):
        bar = InfoBar(
            icon=InfoBarIcon.SUCCESS,
            title="已截图",
            content=f"已复制到剪贴板：{os.path.basename(path)}",
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.BOTTOM_RIGHT,
            duration=10000,
            parent=self,
        )

        def open_image():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            bar.close()

        def open_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
            bar.close()

        for text, handler in (("查看图像", open_image), ("打开目录", open_dir)):
            btn = TransparentPushButton(text=text, parent=bar)
            btn.clicked.connect(handler)
            bar.addWidget(btn)
        bar.show()

    # ---------- 网页视图 ----------

    def _profile(self, user: str):
        """按账号复用一个 profile：同一个账号反复开关标签都不重建。

        每个 profile 是 Chromium 的一份独立存储分区，建的时候要打开并初始化
        `webprofiles/<账号>` 目录。关掉标签立刻重开时，旧 profile 的页面还在异步
        销毁，新 profile 又要抢同一个目录——这一步卡住就是整个窗口没反应。
        存储分区本来就与页面生命周期无关，留着复用即可。"""
        from PyQt5.QtWebEngineWidgets import QWebEngineProfile

        profile = self._profiles.get(user)
        if profile is not None:
            return profile
        storage = os.path.join(PROFILE_ROOT, user)
        os.makedirs(storage, exist_ok=True)
        # PyQt5 的 QWebEngineProfile 构造只接受 parent（Qt6 才有带存储名的构造），
        # 存储路径靠下面的 setPersistentStoragePath 设置，隔离效果相同。
        profile = QWebEngineProfile(self)
        profile.setPersistentStoragePath(storage)
        profile.setCachePath(os.path.join(storage, "cache"))
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self._profiles[user] = profile
        return profile

    def _ensure_view(self, user: str):
        from PyQt5.QtWebEngineWidgets import QWebEnginePage
        # 官方「背景失效解决办法」要求：窗口里有 QWebEngineView 时，改用
        # qframelesswindow 的 FramelessWebEngineView——它构造时先 setHtml("") 垫一层
        # 底（否则 Chromium 原生图层刚挂上会把窗口背景/材质穿透成白块），并主动
        # 调 window().updateFrameless() 重新接管无边框。本窗口在 Win11/macOS 走自管
        # 材质，视图建好后另有一拍 _apply_native_chrome 重挂（见下），两者配合。
        from qframelesswindow.webengine import FramelessWebEngineView

        profile = self._profile(user)
        view = FramelessWebEngineView(self.area)
        view.setPage(QWebEnginePage(profile, view))
        view.loadProgress.connect(self._on_progress)
        view.loadFinished.connect(lambda ok, u=user: self._on_loaded(u, ok))
        view.urlChanged.connect(lambda url, u=user: self._on_url(u, url))
        view.installEventFilter(self)
        view.setGeometry(self.area.rect())
        self._views[user] = view

        label = self._label_of(user)
        self._labels[user] = label
        self.tab_bar.add_tab(user, label)
        # 网页视图是原生图层，创建后会重设一次窗口样式，标题栏要重新接管
        QTimer.singleShot(0, self._apply_native_chrome)
        return view

    def eventFilter(self, obj, event):
        """浏览区尺寸变化时把每一层跟上去；标题栏空白处按下则交给系统拖动。"""
        from PyQt5.QtCore import QEvent

        if obj is self.titleBar and event.type() == QEvent.Type.MouseButtonPress:
            if (event.button() == Qt.MouseButton.LeftButton
                    and _start_system_move(self.titleBar, event.globalPos())):
                return True
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
        self._creds[user] = self._cred_sig(acc)
        self._set_status(f"{self._labels.get(user, user)}：正在登录…")
        self.progress.setValue(0)
        self.progress.show()
        # 登录接口的正文是 JSON，整片盖住换成进度环；接口仍要在网页视图里真加载
        self._show_loading(user, f"正在登录 {self._short_name(user)}…")
        if user == self._current:
            self._raise_page(user)
        # 旧会话先清掉：域级与主机级两条同名 SESSION 同时到达会被判「登录状态已失效」
        view.page().profile().cookieStore().deleteAllCookies()
        url = build_login_url(user, str(acc.get("password") or ""),
                             str(acc.get("loginValidCode") or ""))
        QTimer.singleShot(300, lambda: self._load_login(user, url))
        token = self._login_token.get(user, 0) + 1
        self._login_token[user] = token
        QTimer.singleShot(LOGIN_TIMEOUT_MS,
                          lambda: self._login_watchdog(user, token))

    def _login_watchdog(self, user: str, token: int):
        """登录迟迟没有结果时把等待页收掉，露出网页让人工接手。

        站点接口、Cookie 清理、页面渲染任一步卡住，`loadFinished` 就不会回来，
        进度环会一直盖在网页上——看着就是整个窗口卡死。这里只收等待页，不关
        标签：页面本身可能已经在加载，露出来就能用。"""
        if self._login_token.get(user) != token:
            return  # 期间重登过，这次定时器作废
        if user not in self._views or self._state.get(user) not in ("login", "home", "checking"):
            return
        self._fail(user, "登录没有回应，可在页面里手动登录")

    def _load_login(self, user: str, url: str):
        """等 Cookie 清完再导航；期间标签可能被关掉，所以要重新取一次 view。"""
        view = self._views.get(user)
        if view is not None:
            view.load(QUrl(url))

    def _on_loaded(self, user: str, ok: bool):
        view = self._views.get(user)
        if view is None:
            return
        if view.url().toString() == "about:blank":
            # FramelessWebEngineView 构造时的 setHtml("") 垫底页：不是登录响应，
            # 直接忽略，否则空正文会被 parse 成失败，把自动登录打断
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
        if not user:
            return
        tip = (f"{text}\n{user}" if text
               else f"{self._labels.get(user, user)}（{user}）")
        self.tab_bar.set_tab_tip(user, tip)
