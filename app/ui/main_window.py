"""主窗口：MSFluentWindow（macOS 风格窄侧边栏），返回按钮在标题栏左上角，
支持毛玻璃混合背景（系统配置可设）。文档检查/批量压缩在侧边栏底部与系统配置一组。"""

from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    MSFluentWindow,
    NavigationItemPosition,
    SearchLineEdit,
    TransparentToolButton,
    qrouter,
)

from app import __version__ as VERSION
from app.domain.account_health import excluded_summary, split_accounts
from app.services import store
from app.services.notices import NoticeCenter
from app.ui.browser_page import BrowserWindow
from app.ui.home_page import HomePage, page_bg_color
from app.ui.org_manage_page import OrgManagePage
from app.ui.org_page import OrgPage
from app.ui.settings_page import SettingsPage
from app.ui.messages_page import MessagesPage
from app.ui.upload_page import UploadPage

if sys.platform == "darwin" and not os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen"):
    # 离屏平台没有真实 NSWindow，qframelesswindow 的原生调用会段错误；
    # 界面冒烟测试跑在 offscreen 下，这里跳过全部原生钩子。
    # qframelesswindow 的 StandardTitleBar 给标题 QLabel 写死了 'Segoe UI'
    # （macOS 无此字体，每次启动触发 qt.qpa.fonts 别名填充告警，~135ms 开销）。
    # 在任何窗口构造之前拦截：应用后把字体替换为本机中文渲染字体。
    from qframelesswindow.titlebar import StandardTitleBar

    _orig_init = StandardTitleBar.__init__

    def _local_titlebar_font(self, parent):
        _orig_init(self, parent)
        lbl = getattr(self, "titleLabel", None)
        if lbl is not None:
            lbl.setStyleSheet(
                lbl.styleSheet().replace("'Segoe UI'", "'PingFang SC'")
            )

    StandardTitleBar.__init__ = _local_titlebar_font

    # 让 Qt 内容层带 alpha：_initFrameless() 会调 winId() 创建原生 NSWindow，
    # 构造之后再设 WA_TranslucentBackground 实测无效（alphaBufferSize 仍是 -1、
    # NSWindow.isOpaque 仍是 True，QNSView 的不透明底色会盖住混合层）。
    # 这里只处理 Qt 侧的 backing store；毛玻璃效果层本身全部使用官方默认参数，
    # 见 app/services/mac_effect.py。
    from qframelesswindow.mac import MacFramelessWindowBase

    _orig_init_frameless = MacFramelessWindowBase._initFrameless

    def _init_frameless_translucent(self):
        if getattr(type(self), "_translucent_window", False):
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        _orig_init_frameless(self)

    MacFramelessWindowBase._initFrameless = _init_frameless_translucent


class MainWindow(MSFluentWindow):
    _translucent_window = True

    def __init__(self):
        super().__init__()
        from app.ui.brand import app_icon

        self.setWindowIcon(app_icon())
        self.setWindowTitle("智慧团委")
        self.setMinimumSize(960, 640)

        # 窗口自适应屏幕：85% 可用区域（上限 1180×760），居中
        screen = QApplication.primaryScreen().availableGeometry()
        w, h = min(1180, int(screen.width() * 0.85)), min(760, int(screen.height() * 0.85))
        self.resize(w, h)
        geo = self.frameGeometry()
        geo.moveCenter(screen.center())
        self.move(geo.topLeft())

        # 毛玻璃：窗口底色由 paintEvent 与 FluentWidget.backgroundColor 共同控制。
        # 透明属性已在原生窗口创建前设置（见模块顶部钩子）。
        self._translucent = False
        self._material = "none"
        self._material_applied = False
        self.setStyleSheet("MainWindow{background: transparent}")

        # 通知中心：问题清单的唯一持有者，公告面板与浮层提示都读它
        self.notice_center = NoticeCenter(self)
        self.settings_page = SettingsPage()
        self.home_page = HomePage(self)
        self.upload_page = UploadPage(self)
        self.org_page = OrgPage(self)
        self.org_manage_page = OrgManagePage(self)
        self.messages_page = MessagesPage(self)
        self.browser_window: BrowserWindow | None = None
        self.settings_page.window_ref = self  # 毛玻璃开关与团支书导入后的页面刷新
        self.settings_page.home_page = self.home_page  # 系统配置里设置头图后即时刷新首页

        # 右上角浮层提示：前台收到新问题时用它（带处理按钮）；系统通知没有可用的按钮
        from app.ui.notice_toast import NoticeToastHost

        self.notice_toasts = NoticeToastHost(self)
        self._really_quit = False
        self._splash_done = False
        self._tray_hint_shown = False
        self._setup_tray()
        self.notice_center.new_issue.connect(self._on_new_issue)
        # 启动即把已有账号异常收进通知清单（面板可见，但不弹窗——闪屏期间不打扰）
        self.settings_page.refresh_notices()

        self.addSubInterface(self.home_page, FIF.HOME, "工具首页")
        self.addSubInterface(self.upload_page, FIF.CLOUD, "上传档案")
        self.addSubInterface(self.org_page, FIF.PIE_SINGLE, "组织状况")
        self.addSubInterface(self.org_manage_page, FIF.PEOPLE, "组织管理")
        self.addSubInterface(self.messages_page, FIF.MESSAGE, "系统消息")
        # 访问支部放在系统配置上方：点开的是内嵌浏览器窗口，不占侧边栏的页面位
        self.navigationInterface.addItem(
            "visit-branch", FIF.GLOBE, "访问支部",
            onClick=self._on_visit_branch,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )
        self.addSubInterface(
            self.settings_page, FIF.SETTING, "系统配置", position=NavigationItemPosition.BOTTOM
        )
        # 底部那组导航与窗口底边的距离在 _sync_nav_bottom 里对齐到侧边栏左右留白，
        # 那里能读到布局后的真实几何（构造期导航项的 x 还都是 0）。
        # 内置切页动画会把整页从下方 76px 滑上来（PopUpAniStackedWidget 的默认
        # deltaY），刚进入页面时内容整体往上跳；关掉它，过渡交给下面的淡入。
        self.stackedWidget.setAnimationEnabled(False)

        # 标题栏返回按钮：默认无背景，中心位于窗口外框左上角(0,0)与
        # 主页面背景左上角(侧边栏右缘, 标题栏下沿48)连线的中点
        self._setup_back_button()

        QTimer.singleShot(0, lambda: self.switchTo(self.home_page))
        # 启动恢复窗口背景透明材质（兼容旧 translucent 布尔配置）。混合层要等
        # 窗口 show 之后 contentView 尺寸稳定才挂，实际动作在 showEvent 里。
        cfg = store.load()
        from app.services.materials import normalize, win11_mica_available

        self._material = normalize(cfg.get("translucentMaterial")
                                   or ("acrylic" if cfg.get("translucent") else "none"))
        self._translucent = self._material != "none" and (
            sys.platform == "darwin" or win11_mica_available())
        self._apply_window_background()

        # 明暗模式切换（手动或跟随系统变化）：重绘窗口底色 + 重建带烘焙颜色的页面
        from qfluentwidgets import qconfig

        qconfig.themeChanged.connect(lambda *_: self.on_theme_changed())

        # 同步系统数据：成员缓存 → 站内信 → 组织状况统计，工作线程串行执行，
        # 只用已保存会话取数，不登录。刷新登录状态是独立操作，由设置页按各账号
        # 最后刷新时间做 4 小时节流，不参与这里。
        # 节奏：前台可见 30 秒一轮；仅托盘后台 1 小时一轮（_tick_sync 按状态判断）。
        self._refreshing = False
        self._refresh_silent = True
        self._refresh_worker: "QThread | None" = None
        self._last_sync_ts = 0.0
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(30 * 1000)
        self._sync_timer.timeout.connect(self._tick_sync)
        self._sync_timer.start()
        QTimer.singleShot(15 * 1000, self._tick_sync)

        # 启动闪屏：盖住整个窗口（侧边栏、标题栏都在下面）。首屏快照回来后
        # 由 home_page.stats_ready 收起；5 秒兜底，快照失败也不会卡住界面。
        from app.ui.splash import SplashScreen

        self._splash_done = False
        self.splash = SplashScreen(self)
        self.splash.setGeometry(self.rect())
        self.splash.show()
        self.splash.raise_()
        self.splash.start()
        self.home_page._startup_loading = True
        self.home_page.stats_ready.connect(self._dismiss_splash)
        QTimer.singleShot(5000, self._dismiss_splash)

    def _dismiss_splash(self):
        if self._splash_done:
            return
        self._splash_done = True
        self.home_page._startup_loading = False
        self.splash.finish()

    def _is_foreground(self) -> bool:
        """前台 = 窗口可见且没最小化；仅托盘驻留时算后台。"""
        return self.isVisible() and not self.isMinimized()

    def _tick_sync(self):
        """按前后台决定静默刷新节奏：前台 30 秒、仅后台 1 小时。

        定时器每 30 秒轻量打点；未到间隔、上一轮未结束、没有账号都直接返回。
        刷新只取网页数据（成员/站内信/组织状况），不重新登录。"""
        if not self.settings_page.accounts:
            return
        interval = 30.0 if self._is_foreground() else 3600.0
        if time.monotonic() - self._last_sync_ts < interval - 0.5:
            return
        if self._refreshing:
            return
        self._last_sync_ts = time.monotonic()
        self.refresh_all(silent=True, fast=True)

    def _on_new_issue(self, notice):
        """新问题：前台用带按钮的应用内浮层，后台用系统通知。"""
        if not getattr(self, "_splash_done", False):
            return  # 启动闪屏还没收，先不弹；公告面板会显示
        if self._is_foreground():
            self.notice_toasts.show(notice, self.handle_notice_action)
        else:
            from app.services.notify import send

            send(notice.title, notice.detail or notice.title)

    # ---------- 通知处理入口 ----------

    def handle_notice_action(self, action: str):
        """把通知的 action 解释成「切页 + 定位」：accounts:<账号> / org:<支部> / ..."""
        if not action:
            return
        head, _, rest = action.partition(":")
        if head == "accounts":
            self.switchTo(self.settings_page)
            QTimer.singleShot(0, lambda: self.settings_page.focus_accounts(rest))
        elif head == "org":
            self.switchTo(self.org_manage_page)
            if rest:
                QTimer.singleShot(0, lambda: self.org_manage_page.reveal_branch(rest))
        elif head == "messages":
            self.switchTo(self.messages_page)
        elif head == "upload":
            self.switchTo(self.upload_page)

    # ---------- 托盘驻留 ----------

    def _setup_tray(self):
        from PySide6.QtWidgets import QSystemTrayIcon

        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QMenu

        from app.ui.brand import app_icon

        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("智慧团委")
        menu = QMenu()
        act_show = QAction("显示主界面", self)
        act_show.triggered.connect(self._restore_from_tray)
        act_sync = QAction("立即同步", self)
        act_sync.triggered.connect(self._sync_now)
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self._quit_app)
        menu.addAction(act_show)
        menu.addAction(act_sync)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        from PySide6.QtWidgets import QSystemTrayIcon

        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _sync_now(self):
        self._last_sync_ts = time.monotonic()
        self.refresh_all(silent=True, fast=True)

    def _quit_app(self):
        self._really_quit = True
        if getattr(self, "tray", None) is not None:
            self.tray.hide()
        QApplication.quit()

    def refresh_all(self, silent: bool = True, fast: bool = False) -> bool:
        """同步系统数据：成员缓存、站内信、组织状况统计，每轮都取数。

        全程工作线程（网络不进 GUI 线程）；进行中再次调用直接返回 False。
        fast=False（手动）：组织状况在线重查（OrgWorker），并收回网页会话占用。
        fast=True（30 秒定时）：只用已保存会话取数，不登录，避免顶掉网页端
        会话；站内信拉到新消息弹系统通知；有账号同步到新成员时组织状况从
        缓存离线重算，否则保留现有展示。
        刷新登录状态不在这里：设置页按每个账号的最后刷新时间独立节流。
        「系统配置」的按钮走 silent=False（有开始/完成提示），定时同步走静默。"""
        from qfluentwidgets import InfoBar

        from app.services import login_gate
        from app.ui.messages_page import MessageFetchWorker
        from app.ui.org_manage_page import MemberSyncWorker, mark_synced

        if not silent and login_gate.active_browser_sessions():
            login_gate.clear_browser_sessions()

        usable, excluded = split_accounts(self.settings_page.accounts)
        accounts = usable
        if not accounts:
            if not silent:
                hint = (f"{len(excluded)} 个账号均被标记为异常，"
                        "请先处理（测活或打开网页端重新登录）") if excluded \
                    else "先在系统配置添加团支部账号"
                InfoBar.warning("没有可用账号", hint, duration=6000, parent=self)
            return False
        if excluded and not silent:
            InfoBar.warning("跳过异常账号",
                f"{excluded_summary(excluded)}，本轮不参与刷新；测活恢复正常后自动纳入",
                duration=6000, parent=self)
        if self._refreshing:
            if not silent:
                InfoBar.info("刷新进行中", "上一轮还没跑完，稍候", duration=3000, parent=self)
            return False

        self._refreshing = True
        self._refresh_silent = silent
        if not silent:
            InfoBar.info("开始刷新", f"正在刷新 {len(accounts)} 个账号", duration=4000, parent=self)

        synced_any: list[str] = []

        def _on_sync_one(user, branch, count, status, message):
            if status == "ok":
                synced_any.append(user)

        def _after_sync():
            self.org_manage_page.refresh()
            mark_synced()
            msg_w = MessageFetchWorker(accounts, force=fast)
            self._refresh_worker = msg_w
            msg_w.new_ready.connect(self._notify_new_messages)
            msg_w.all_done.connect(_after_messages)
            msg_w.start()

        def _after_messages():
            self.messages_page._load_cache()
            self.home_page.refresh_stats(show_loading=False)
            if fast:
                if synced_any:
                    self.org_page.refresh_offline()  # 成员缓存有更新，离线重算组织状况
                _finish()
            else:
                self.org_page._start()  # 组织状况统计自己开线程，结束回调收尾
                org_w = self.org_page.worker
                if org_w is not None:
                    org_w.all_done.connect(_finish)
                else:
                    _finish()

        def _finish():
            self._refreshing = False
            self._refresh_worker = None
            self.home_page.refresh_stats(show_loading=False)
            if not self._refresh_silent:
                InfoBar.success("刷新完成", "成员、站内信、组织状况均已更新", duration=4000, parent=self)

        sync_w = MemberSyncWorker(accounts, sequential=True)
        self._refresh_worker = sync_w
        sync_w.account_done.connect(_on_sync_one)
        sync_w.all_done.connect(_after_sync)
        sync_w.start()
        return True

    def _notify_new_messages(self, items: list):
        """新站内信：前台用带「查看」按钮的浮层，后台用系统通知。"""
        if not items:
            return
        first = items[0]
        body = f"{first.get('account', '')}：{(first.get('content') or '').strip() or first.get('title', '')}"
        if self._is_foreground() and getattr(self, "_splash_done", False):
            from app.services.notices import Notice

            # 站内信不设标题，直接把内容放一行；动作「查看」在最左
            self.notice_toasts.show(
                Notice(key="messages", title="",
                       detail=body[:120] or f"收到 {len(items)} 条新站内信",
                       action="messages", action_text="查看", tone="info"),
                self.handle_notice_action)
        else:
            from app.services.notify import send

            send(f"收到 {len(items)} 条新站内信", body[:120])

    def _on_visit_branch(self):
        """侧边栏「访问支部」：打开浏览器窗口，没有标签时先问要访问哪个支部。"""
        self._ensure_browser_window().prompt()

    def closeEvent(self, e):
        """关窗：有托盘就收进去继续后台刷新，没有托盘才真正退出。

        真正退出时把账号网页窗口一并收掉（后台驻留时保留，从托盘回来还在）。"""
        if self._really_quit or getattr(self, "tray", None) is None:
            window = getattr(self, "browser_window", None)
            if window is not None:
                window.shutdown()
            super().closeEvent(e)
            return
        e.ignore()
        self.hide()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            from app.services.notify import send

            send("智慧团委", "已缩到托盘继续后台刷新，右键托盘图标可退出")

    def _ensure_browser_window(self) -> BrowserWindow:
        if self.browser_window is None:
            self.browser_window = BrowserWindow()
        return self.browser_window

    def open_browser(self, user: str = ""):
        """打开账号网页窗口：独立窗口，不在侧边栏占一页。"""
        self._ensure_browser_window().open_account(user)

    def sync_browser_accounts(self):
        """账号信息改动后注入已打开的标签：改名跟着改，密码/验证码变过就重登。

        没有浏览器窗口就什么都不做——下次打开读的就是最新配置。
        """
        browser = getattr(self, "browser_window", None)
        if browser is not None:
            browser.refresh_accounts()

    def on_theme_changed(self):
        """明暗模式已经落地：窗口底色重绘，消息卡片的标签各自按新主题重画。

        整窗重算样式是 Qt 一次性完成的，没法拆到多线程；这里不再重建消息卡片，
        把原来「切一次主题重建上百个控件」的开销去掉，只更新按主题取色的文字。
        """
        self.update()
        page = getattr(self, "messages_page", None)
        if page is not None:
            page.apply_theme()

    def apply_theme_async(self, mode: str, done=None):
        """切换明暗模式：画面淡出 → 换样式 → 淡入。

        setTheme 会让整个窗口重算一遍样式表，这段时间界面必然不响应；
        把它放进淡出后的透明帧里执行，用户看到的是颜色平滑过渡，而不是画面定格。
        """
        if getattr(self, "_theme_busy", False):
            return
        from app.setup import apply_theme_mode
        from app.ui.anim import cross_fade

        page = self.stackedWidget.currentWidget()
        content = getattr(page, "scrollWidget", None)
        self._theme_busy = True

        def _apply():
            try:
                apply_theme_mode(mode)
                store.update(lambda cfg: cfg.__setitem__("themeMode", mode))
                self.on_theme_changed()
            finally:
                self._theme_busy = False
            if done:
                done()

        if content is None:
            _apply()
            return
        cross_fade(content, _apply)

    def _setup_back_button(self):
        from app.ui.page import set_tip

        tb = self.titleBar.hBoxLayout
        # 去掉默认的左间距与应用图标占位：标题栏左边要留给侧边栏那一列
        tb.removeWidget(self.titleBar.iconLabel)
        self.titleBar.iconLabel.hide()

        # 返回键放在固定宽度的槽位里：没有返回历史时只隐藏按钮、槽位仍在，
        # 标题文字的左边缘因此不会随页面跳转左右跳。
        # 槽位不要设 WA_TransparentForMouseEvents：那个属性会让 Qt 的命中测试
        # 跳过整个子树，按钮就点不到了（事件落到标题栏上，表现为点了没反应）。
        self.back_slot = QWidget(self.titleBar)
        self.back_slot.setFixedSize(30, 30)
        # 细 chevron「<」：只有尖角、没有中间那根横线（CARE_LEFT_SOLID 是实心三角、
        # LEFT_ARROW 带杆，都不对）。图标是位图旋转来的，颜色在生成时定死，
        # 主题切换后要重新生成一次。
        from app.ui.page import chevron_left_icon
        from qfluentwidgets import qconfig

        self.back_btn = TransparentToolButton(chevron_left_icon(), self.back_slot)
        qconfig.themeChanged.connect(
            lambda *_: self.back_btn.setIcon(chevron_left_icon(enabled=self.back_btn.isEnabled())))
        set_tip(self.back_btn, "返回上一页")
        self.back_btn.setFixedSize(30, 30)
        self.back_btn.move(0, 0)

        # 标题栏放应用图标：带白底板的小方块，深浅主题下都有清晰边界
        from PySide6.QtWidgets import QLabel

        from app.ui.brand import app_tile_pixmap

        self.logo = QLabel(self.titleBar)
        # 裁掉位图留白：方块左缘才能正好压在侧边栏右缘（完整图标自带 20px 的 2px 空边）
        self.logo.setPixmap(app_tile_pixmap(16))
        self.logo.setFixedSize(16, 16)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_tip(self.logo, "智慧团委 Smart-YLC")

        # 撑开返回键与 logo 之间的距离，让 logo 左缘正好落在侧边栏右缘。
        # 宽度由 _sync_logo_gap 按导航栏实际几何算出，侧边栏宽度变化时跟着走。
        self._logo_gap = QWidget(self.titleBar)
        self._logo_gap.setFixedWidth(0)
        self._logo_gap.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # 顺序：返回键槽位 → 侧边栏占位 → 应用图标 → 标题文字。
        # 标题文字的左边缘因此固定，且与内容区左缘对齐。
        while tb.count() and tb.itemAt(0).spacerItem():
            tb.takeAt(0)
        tb.insertWidget(0, self.back_slot, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        at = tb.indexOf(self.titleBar.titleLabel)
        tb.insertWidget(at, self._logo_gap, 0)
        tb.insertWidget(at + 1, self.logo, 0,
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # 图标紧贴标题文字（仅留 4px 呼吸位），两者垂直居中、左缘对齐
        tb.insertSpacing(tb.indexOf(self.titleBar.titleLabel), 4)
        # 标题文字按同一基线居中，否则图标与文字看着不在一行
        self.titleBar.titleLabel.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._sync_logo_gap()

        self.back_btn.clicked.connect(self._on_back_clicked)
        QTimer.singleShot(0, self._place_back_button)

        # 标题栏居中搜索框：只有组织管理与系统消息用，切页时换提示并显隐
        self.search_edit = SearchLineEdit(self.titleBar)
        self.search_edit.setFixedHeight(30)
        self.search_edit.setVisible(False)
        self.search_edit.textChanged.connect(self._on_search_changed)
        self.stackedWidget.currentChanged.connect(self._sync_search)
        self.stackedWidget.currentChanged.connect(self._animate_page)

        qrouter.emptyChanged.connect(self._sync_back_state)
        self.stackedWidget.currentChanged.connect(self._sync_back_state)
        self.org_manage_page.detail_changed.connect(self._sync_back_state)
        self._sync_back_state()
        self._sync_search()
        QTimer.singleShot(0, self._place_search)

    def _on_back_clicked(self):
        """详情页优先退回支部列表，其余情况按导航历史返回上一页。"""
        if (self.stackedWidget.currentWidget() is self.org_manage_page
                and self.org_manage_page.go_back()):
            return
        qrouter.pop()

    def _sync_back_state(self, *_):
        from app.ui.page import chevron_left_icon

        on_org = (self.stackedWidget.currentWidget() is self.org_manage_page
                  and self.org_manage_page.isVisible())
        detail = on_org and self.org_manage_page.in_detail()
        usable = bool(qrouter.history) or detail
        # 返回键一直留在标题栏上：没有可返回的历史时置灰，而不是整块消失，
        # 槽位宽度也不变，标题文字的左边缘因此始终固定。
        self.back_btn.setEnabled(usable)
        # 图标是位图，手动换透明度版本（按钮不会自动套 QIcon.Disabled）
        self.back_btn.setIcon(chevron_left_icon(enabled=usable))

    # ---------- 标题栏搜索 ----------

    SEARCH_PAGES = (("org_manage_page", "搜索团员姓名或手机号"),
                    ("messages_page", "搜索姓名或消息内容"),
                    ("upload_page", "搜索档案文件名或支部"))

    def _sync_search(self, *_):
        """搜索框随页面切换：换提示语、清空关键词，不在用的页面把过滤词复位。"""
        page = self.stackedWidget.currentWidget()
        for attr, _hint in self.SEARCH_PAGES:
            target = getattr(self, attr, None)
            if target is not None and target is not page:
                target.set_search("")
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        hint = next((h for attr, h in self.SEARCH_PAGES
                     if getattr(self, attr, None) is page), None)
        if hint:
            self.search_edit.setPlaceholderText(hint)
            self.search_edit.show()
        else:
            self.search_edit.hide()
        self._place_search()

    def _on_search_changed(self, text: str):
        page = self.stackedWidget.currentWidget()
        for attr, _hint in self.SEARCH_PAGES:
            target = getattr(self, attr, None)
            if target is page:
                target.set_search(text)
                return

    def _animate_page(self, *_):
        """切页时整页淡入。

        只动透明度：对布局里的控件做位移动画会让内容在进入页面时上下跳，
        滚动区域还会顺带重算一次位置。
        """
        page = self.stackedWidget.currentWidget()
        if page is None:
            return
        from app.ui.anim import fade_in

        fade_in(page, duration=180, dy=0)

    def _place_search(self):
        bar = self.titleBar
        if not hasattr(self, "search_edit"):
            return
        width = min(340, max(200, bar.width() - 560))
        self.search_edit.setFixedWidth(width)
        x = max(96, (bar.width() - width) // 2)
        y = (bar.height() - self.search_edit.height()) // 2
        self.search_edit.move(x, y)
        self.search_edit.raise_()  # 绝对定位的兄弟控件要抬到最上层，否则会被标题挡住

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_search()
        self._sync_logo_gap()
        splash = getattr(self, "splash", None)
        if splash is not None and not self._splash_done:
            splash.setGeometry(self.rect())
            splash.raise_()

    def _place_back_button(self):
        tb = self.titleBar.hBoxLayout
        # 撤掉旧占位（重复定位时先清理）
        while tb.count() and tb.itemAt(0).spacerItem():
            tb.takeAt(0)
        idx = tb.indexOf(self.back_slot)
        if idx >= 0:
            tb.takeAt(idx)
        # 参考 Fluent Gallery：返回键、图标、标题文字紧凑排在标题栏最左，
        # 整组左边缘固定在 10px，不随返回历史出现与否移动。
        tb.insertSpacing(0, 10)
        tb.insertWidget(1, self.back_slot, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # 返回箭头与 logo 之间只留 4px，两者之间的大段距离由 _logo_gap 撑到侧边栏右缘
        tb.insertSpacing(2, 4)
        self.titleBar.titleLabel.setText(self.windowTitle())
        self.titleBar.titleLabel.adjustSize()
        self._sync_logo_gap()
        self._place_search()

    def _sync_logo_gap(self):
        """logo 左缘对准侧边栏右缘：占位宽度 = 侧边栏右缘 -（左内边距 + 返回键 + 间距）。"""
        gap = getattr(self, "_logo_gap", None)
        if gap is None:
            return
        nav = self.navigationInterface
        # 侧边栏是 NavigationBar，和 titleBar 之间没有父子关系，直接
        # mapTo(titleBar) 会报 "parent must be in parent hierarchy"；
        # 两边都映射到窗口这个共同祖先再相减。
        edge = (nav.mapTo(self, QPoint(nav.width(), 0)).x()
                - self.titleBar.mapTo(self, QPoint(0, 0)).x())
        gap.setFixedWidth(max(0, edge - (10 + self.back_slot.width() + 4)))
        self._sync_nav_bottom()

    def _sync_nav_bottom(self):
        """底部导航距窗口底边 = 导航项在侧边栏内的左右留白（项宽 64 / 栏宽 72 → 4px）。

        四边等距，选中高亮才不会看着往下沉；NavigationBar 默认留 5px，
        这里按实测几何改写，布局尚未生效时（x 为 0）跳过。
        """
        nav = self.navigationInterface
        lay = nav.layout()
        item = nav.widget("page-settings")
        if lay is None or item is None or item.x() <= 0:
            return
        side = item.x()
        m = lay.contentsMargins()
        if m.bottom() != side:
            lay.setContentsMargins(m.left(), m.top(), m.right(), side)

    def showEvent(self, e):
        super().showEvent(e)
        splash = getattr(self, "splash", None)
        if splash is not None and not self._splash_done:
            splash.setGeometry(self.rect())
            splash.raise_()
        if not self._material_applied:
            # 等窗口布局稳定再挂：showEvent 当帧 window.width() 还是初始几何，
            # 官方 setAcrylicEffect 用它建 frame 会错位。效果层也要窗口稳定
            # 几帧后才完成合成，过早截图/关窗会看不到毛玻璃。
            def _apply(retry: bool = True):
                self.set_material(self._material)
                if not self._material_applied and retry:
                    QTimer.singleShot(200, lambda: _apply(False))

            QTimer.singleShot(150, _apply)

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self._translucent:
            painter = QPainter(self)
            painter.fillRect(self.rect(), page_bg_color())

    def _apply_window_background(self):
        # 深色保持全透明，保留原来的背景呈现与毛玻璃质感；首页头图渐隐到
        # 透明后直接融进这层真实底色。浅色要铺一层稳定浅底：Fluent 的
        # CardWidget 在浅色下只有 67% 白，全透明窗底会让桌面颜色透进卡片，
        # 深色文字压在深色条纹上就读不清。light/dark 随主题切换自动生效。
        # Windows 的 Mica 自带桌面取色，窗口底全透明即可，不再叠半透明层。
        if self._translucent:
            if sys.platform == "win32":
                self.setCustomBackgroundColor(QColor(0, 0, 0, 0), QColor(0, 0, 0, 0))
            else:
                self.setCustomBackgroundColor(QColor(249, 249, 251, 200), QColor(0, 0, 0, 0))
        else:
            self.setCustomBackgroundColor(QColor(240, 244, 249), QColor(32, 32, 32))

    def apply_material(self, material: str):
        """切换毛玻璃材质：主窗口和已打开的支部浏览器窗口一起换。

        材质是全局偏好，只改主窗口会让开着的浏览器窗口继续挂在旧材质上，
        看起来就是"设置改了但那个窗口没反应"。浏览器窗口是独立窗口，
        有自己的混合层，得单独通知一次。
        """
        self.set_material(material)
        browser = getattr(self, "browser_window", None)
        if browser is not None:
            browser.set_material(material)

    def set_material(self, material: str):
        """窗口背景透明材质：none 关闭（纯色底），acrylic/mica 为混合层。

        配置持久化 translucentMaterial，系统配置里可切；兼容旧 translucent 布尔
        与 sidebar/hud 旧值（自动归一为 acrylic）。"""
        from app.services.materials import apply_windows_effect, normalize

        material = normalize(material)
        self._material = material
        if sys.platform == "darwin":
            # 混合层只在 macOS / Win11 实现；其他平台保持纯色底（否则背景没人画，窗口发黑）
            self._translucent = material != "none"
        elif sys.platform == "win32":
            from app.services.materials import win11_mica_available

            self._translucent = material != "none" and win11_mica_available()
        else:
            self._translucent = False
        self._apply_window_background()
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
                # Win10 等不支持时回退纯色底，不让窗口裸透明发黑
                self._translucent = False
                self._apply_window_background()
        else:
            self._material_applied = True
        self.update()
