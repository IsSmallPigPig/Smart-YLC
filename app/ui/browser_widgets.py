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
        from app.ui.page import page_bg_color

        painter = QPainter(self)
        painter.fillRect(self.rect(), page_bg_color())
