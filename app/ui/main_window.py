"""主窗口：MSFluentWindow（macOS 风格窄侧边栏），返回按钮在标题栏左上角，
支持毛玻璃混合背景（系统配置可设）。文档检查/批量压缩在侧边栏底部与系统配置一组。"""

from __future__ import annotations

import os
import sys
import time

from PyQt5.QtCore import QEvent, QPoint, QThread, Qt, QTimer
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QApplication, QWidget
from app.ui.main_window_tray_mixin import MainWindowTrayMixin
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    MSFluentWindow,
    NavigationItemPosition,
    SearchLineEdit,
    TransparentToolButton,
    qrouter,
)

from app.domain.account_health import excluded_summary, split_accounts
from app.services import store
from app.services.notices import NoticeCenter
from app.ui.browser_page import BrowserWindow
from app.ui.home_page import HomePage
from app.ui.org_manage_page import OrgManagePage
from app.ui.page import page_bg_color
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

    # 让 Qt 内容层带 alpha：首次取 winId() 会创建原生 NSWindow，之后再设
    # WA_TranslucentBackground 实测无效（alphaBufferSize 仍是 -1、NSWindow.isOpaque
    # 仍是 True，QNSView 的不透明底色会盖住混合层）。
    # 这里只处理 Qt 侧的 backing store；毛玻璃效果层本身全部使用官方默认参数，
    # 见 app/services/mac_effect.py。
    #
    # 两版包的入口名不同：PySide6 版是 MacFramelessWindowBase._initFrameless()，
    # PyQt5 版（PyQt5-Frameless-Window 0.8）是 MacFramelessWindow.updateFrameless()。
    # 语义一样（建标题栏 + 建 NSWindow），按存在性挂到对应的方法上。
    from qframelesswindow import mac as _mac

    _base = getattr(_mac, "MacFramelessWindowBase", None) or _mac.MacFramelessWindow
    _hook_name = "_initFrameless" if hasattr(_base, "_initFrameless") else "updateFrameless"
    _orig_native_init = getattr(_base, _hook_name)

    def _init_frameless_translucent(self, *args, **kwargs):
        if getattr(type(self), "_translucent_window", False):
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        _orig_native_init(self, *args, **kwargs)

    setattr(_base, _hook_name, _init_frameless_translucent)


def _pump():
    """构造页面期间把这一拍交还事件循环。

    启动画面是独立窗口，主线程闷头建六个页面时它就冻在那儿；每建完一页让一拍，
    转圈能继续动，系统也不会把这个刚起来的进程标成「未响应」。
    只放行绘制与定时器，不吃用户输入、不碰套接字。"""
    from PyQt5.QtCore import QEventLoop
    from PyQt5.QtWidgets import QApplication

    QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                               | QEventLoop.ProcessEventsFlag.ExcludeSocketNotifiers)


class MainWindow(MainWindowTrayMixin, MSFluentWindow):
    _translucent_window = True

    def __init__(self):
        super().__init__()
        from app.ui.brand import app_icon

        self.setWindowIcon(app_icon())
        self.setWindowTitle("智慧团委")
        self.setMinimumSize(960, 640)

        # 窗口自适应屏幕：85% 可用区域（上限 1180×760），居中。
        # 几何与启动画面共用 boot.main_window_rect()：两边各算一次迟早差几个像素，
        # 交棒时画面会跳一下。
        from app.ui.boot import main_window_rect

        rect = main_window_rect()
        self.resize(rect.size())
        geo = self.frameGeometry()
        geo.moveCenter(rect.center())
        self.move(geo.topLeft())

        # 毛玻璃：窗口底色由 paintEvent 与 FluentWidget.backgroundColor 共同控制。
        # 透明属性已在原生窗口创建前设置（见模块顶部钩子）。
        self._translucent = False
        self._material = "none"
        self._material_applied = False
        self.setStyleSheet("MainWindow{background: transparent}")

        # 通知中心：问题清单的唯一持有者，公告面板与浮层提示都读它
        # 六页逐个构造，每建完一页让一拍：合计一秒多，闷头建完画面就是定住的
        self.notice_center = NoticeCenter(self)
        self.settings_page = SettingsPage()
        _pump()
        # 退出前收束设置页的后台线程（测活/工作区）：PyQt5 析构运行中的
        # QThread 会直接 abort，托盘退出、关窗退出、崩溃兜底都走 aboutToQuit
        QApplication.instance().aboutToQuit.connect(self.settings_page.shutdown)
        self.home_page = HomePage(self)
        _pump()
        self.upload_page = UploadPage(self)
        _pump()
        self.org_page = OrgPage(self)
        self.org_manage_page = OrgManagePage(self)
        _pump()
        self.messages_page = MessagesPage(self)
        self.browser_window: BrowserWindow | None = None
        self.settings_page.window_ref = self  # 毛玻璃开关与团支书导入后的页面刷新
        self.settings_page.home_page = self.home_page  # 系统配置里设置头图后即时刷新首页

        self._really_quit = False
        self._splash_done = False
        self._tray_hint_shown = False
        self._setup_tray()
        from app.ui import bg_toast

        bg_toast.set_default_action(self._restore_from_tray)
        self.notice_center.new_issue.connect(self._on_new_issue)
        # 启动即把已有账号异常收进通知清单（面板可见，但不弹窗——闪屏期间不打扰）。
        # 这一趟要过一遍全部账号，放到首帧之后做，别压着启动画面。
        QTimer.singleShot(300, self.settings_page.refresh_notices)

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
        #
        # 切页动画：开，但不搬用库默认的 76px 竖直滑入——那是给弹窗式页面设计的，
        # 整页内容从下方冲上来，观感像页面在跳。这里改成 12px 的轻微上浮，
        # 配合透明度淡入（见 _fade_in_page），是 Fluent 的「进入」节奏：
        # 位移小到只提供方向感，视觉重心仍然稳在原地。
        # 动画容器是 stackedWidget.view（PopUpAniStackedWidget），
        # 外层 StackedWidget 只是壳。
        for info in self.stackedWidget.view.aniInfos:
            info.deltaY = 12
            info.deltaX = 0
        self.stackedWidget.setAnimationEnabled(True)
        self.stackedWidget.currentChanged.connect(self._fade_in_page)

        # 标题栏返回按钮：默认无背景，中心位于窗口外框左上角(0,0)与
        # 主页面背景左上角(侧边栏右缘, 标题栏下沿48)连线的中点
        self._setup_back_button()

        QTimer.singleShot(0, lambda: self.switchTo(self.home_page))
        # 启动恢复窗口背景透明材质（兼容旧 translucent 布尔配置）。混合层要等
        # 窗口 show 之后 contentView 尺寸稳定才挂，实际动作在 showEvent 里。
        cfg = store.load()
        from app.services.materials import preferred, windows_material_available

        self._material = preferred(cfg)
        self._translucent = self._material != "none" and (
            sys.platform == "darwin" or windows_material_available(self._material))
        self._apply_window_background()

        # 明暗模式切换（手动或跟随系统变化）：重绘窗口底色 + 重建带烘焙颜色的页面
        from qfluentwidgets import qconfig

        qconfig.themeChanged.connect(self.on_theme_changed)

        # 同步系统数据：成员缓存 → 站内信 → 组织状况统计，工作线程串行执行，
        # 只用已保存会话取数，不登录。刷新登录状态是独立操作，由设置页按各账号
        # 最后刷新时间做 4 小时节流，不参与这里。
        # 节奏：前台可见 30 秒一轮；仅托盘后台 1 小时一轮（_tick_sync 按状态判断）。
        self._refreshing = False
        self._refresh_silent = True
        self._refresh_worker: "QThread | None" = None
        self._mark_worker: "QThread | None" = None
        self._last_sync_ts = 0.0
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(30 * 1000)
        self._sync_timer.timeout.connect(self._tick_sync)
        self._sync_timer.start()
        # 首轮同步等启动画面收完：23 个账号的取数线程一起起来，赶在启动那一拍上
        # 会把 CPU 顶满，画面看着更卡。
        QTimer.singleShot(20 * 1000, self._tick_sync)

        # 新问题先攒 250ms 再弹：一轮检查里十几个账号同时异常时合并成一条，
        # 否则右上角会一次堆满十几条消息条
        self._pending_bars: list = []
        self._bar_timer = QTimer(self)
        self._bar_timer.setSingleShot(True)
        self._bar_timer.setInterval(250)
        self._bar_timer.timeout.connect(self._flush_bars)

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
        # 界面已经露面，趁空闲把内嵌浏览器的引擎拉起来：第一次建默认 profile 要
        # 几百毫秒，以前这笔开销落在「打开账号网页」那一拍上，窗口就定住一下。
        QTimer.singleShot(3000, self.warm_web_engine)
        # 使用引导：首次启动、或进入新学年（9 月 1 日起）弹一次
        QTimer.singleShot(1200, self.show_guide_if_due)

    def show_guide_if_due(self):
        """该弹引导就弹：从没弹过，或已经进了比上次更晚的学年。"""
        from datetime import datetime

        from app.ui.guide_dialog import GuideDialog, mark_guide_shown, should_show_guide

        now = datetime.now()
        if not should_show_guide(store.load(), now):
            return
        dlg = GuideDialog(self)
        dlg.finished.connect(lambda _code: mark_guide_shown(now))
        self._guide_dialog = dlg
        dlg.show()

    def warm_web_engine(self):
        """预热 Chromium（只建默认 profile，不碰各账号的存储分区）。

        幂等：Qt 里默认 profile 只建一次，之后再调就是瞬间返回。
        资源不齐时 QtWebEngine 在 Windows 上触发 int3 断点（0x80000003），进程
        当场消失——所以先问 webengine 能不能用，不能就一个字节都不碰引擎。"""
        if getattr(self, "_engine_warm", False):
            return
        from app.services import webengine
        from app.services.app_log import warning

        if not webengine.available():
            warning(f"WebEngine 不可用，跳过预热：{webengine.reason()}")
            return
        self._engine_warm = True
        try:
            from PyQt5.QtWebEngineWidgets import QWebEngineProfile

            QWebEngineProfile.defaultProfile()
        except Exception:
            # 引擎起不来不该影响主界面：真要打开网页时还会再试一次
            self._engine_warm = False

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
        """新问题：前台攒成一条消息条，后台用系统通知（都只发短文案）。"""
        if not getattr(self, "_splash_done", False):
            return  # 启动闪屏还没收，先不弹；公告面板会显示
        if self._is_foreground():
            self._pending_bars.append(notice)
            self._bar_timer.start()
        else:
            self._alert(notice.brief or notice.title, notice.detail or "",
                        notice.action, notice.tone)

    def _flush_bars(self):
        """把这一批新问题合成最多几条消息条：同类账号异常只报个数。"""
        pending, self._pending_bars = self._pending_bars, []
        if not pending:
            return
        accounts = [n for n in pending if n.action.startswith("accounts:")]
        others = [n for n in pending if not n.action.startswith("accounts:")]

        if accounts:
            self._show_account_notice(accounts)

        for notice in others[:3]:  # 其余问题逐条，最多 3 条；全部在公告面板里
            self._show_notice_bar(notice)

    def _show_account_notice(self, notices):
        """账号异常只留一条消息条，新的替换旧的。

        以前每来一批新问题就单独弹一条，一轮测活能叠出三块「N 个账号异常」。
        单个账号保持「哪个班异常」的原话；多个账号按原因归并成一句。
        """
        if len(notices) == 1:
            bar = self._show_notice_bar(notices[0])
        else:
            from app.domain.account_health import HEALTH_LABEL
            from app.services.notices import Notice

            buckets: dict[str, int] = {}
            for acc in self.settings_page.accounts:
                status = (acc.get("health") or {}).get("status") or ""
                if status in ("ok", "unknown", ""):
                    continue
                label = HEALTH_LABEL.get(status, "状态异常").split("（")[0]
                buckets[label] = buckets.get(label, 0) + 1
            bad = sum(buckets.values()) or len(notices)
            # 不用中点分隔：不同系统字体下中点基线不一致，改用逗号 + 空格
            detail = "，".join(f"{k} {v}" for k, v in
                              sorted(buckets.items(), key=lambda kv: -kv[1]))
            content = f"{bad} 个账号异常" + (f"：{detail}" if detail else "")
            bar = self._show_notice_bar(Notice(
                key="accounts", title="", detail=content, brief=content,
                action=notices[0].action, action_text="去设置", tone="error"))
        old = getattr(self, "_account_bar", None)
        if old is not None and old is not bar:
            old.close()
        self._account_bar = bar

    def _show_notice_bar(self, notice):
        """用 qfluentwidgets 的 InfoBar 呈现一条通知。

        配色由 InfoBarIcon 决定（success 绿 / warning 橙 / error 红 / info 蓝），
        右上角自动堆叠、内容只显示首行；有处理入口时按钮贴最右。"""
        from PyQt5.QtCore import Qt
        from qfluentwidgets import (
            InfoBar,
            InfoBarIcon,
            InfoBarPosition,
            TransparentPushButton,
        )

        icon = {
            "success": InfoBarIcon.SUCCESS,
            "warning": InfoBarIcon.WARNING,
            "error": InfoBarIcon.ERROR,
        }.get(notice.tone, InfoBarIcon.INFORMATION)
        # 有短文案就只显示短文案：消息条是用来提醒的，具体原因留在公告面板
        if notice.brief:
            title, content = "", notice.brief
        else:
            title, content = notice.title, (notice.detail or notice.title)
        bar = InfoBar(
            icon=icon,
            title=title,
            content=content,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=8000 if (notice.action and notice.action_text) else 5000,
            parent=self,
        )
        if notice.action and notice.action_text:
            btn = TransparentPushButton(text=notice.action_text, parent=bar)
            btn.clicked.connect(lambda: (self.handle_notice_action(notice.action), bar.close()))
            bar.addWidget(btn)   # 文字右侧：动作贴在消息条最右
        bar.show()
        return bar

    # ---------- 更新就绪（等待用户确认重启） ----------

    def show_update_ready(self, installer: str, version: str = ""):
        """下载完成后的常驻提示：新版本已就绪，重启生效。

        不自动安装、不自动退出——由用户点「重启」确认。消息条 duration<0
        表示不自动消失，只有用户主动叉掉才收（关掉不影响已下载的更新包，
        下次启动仍会提示）。
        """
        from PyQt5.QtCore import Qt
        from qfluentwidgets import (
            InfoBar,
            InfoBarIcon,
            InfoBarPosition,
            TransparentPushButton,
        )

        old = getattr(self, "_update_bar", None)
        if old is not None and old is not self:
            try:
                old.close()
            except RuntimeError:
                pass
        bar = InfoBar(
            icon=InfoBarIcon.INFORMATION,
            title="",
            content="新版本已就绪，重启生效",
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=-1,        # 常驻：除非用户主动关闭
            parent=self,
        )
        btn = TransparentPushButton("重启", parent=bar)
        btn.clicked.connect(
            lambda: (bar.close(), self._restart_for_update(installer, version)))
        bar.addWidget(btn)
        bar.show()
        self._update_bar = bar
        return bar

    def _restart_for_update(self, installer: str, version: str = ""):
        """用户点「重启」：拉起静默安装并退出，装好后自动回到新版本。"""
        from qfluentwidgets import InfoBar

        from app.services import silent_update

        try:
            silent_update.save_pending_installer(installer, version)
            if version:
                silent_update.mark_pending(version)
            silent_update.start(installer)
        except Exception as e:  # noqa: BLE001 - 安装器缺失/被占用都要给出下一步
            InfoBar.error("重启失败", f"{e}。可到「系统配置 - 关于项目」重试",
                          duration=8000, parent=self)
            return
        self._quit_app()
        # 兜底：后台同步/上传线程在跑时进程可能不退出，安装器会撞「文件被占用」
        # 以退出码 5 中止。给一段硬退窗口，保证安装能接管。
        QTimer.singleShot(2500, lambda: os._exit(0))

    def notify_update_ready_if_pending(self) -> bool:
        """启动时若上次已下载好更新包，重新给出常驻重启提示。"""
        from app.services import silent_update

        item = silent_update.pending_installer()
        if not item:
            return False
        self.show_update_ready(item["path"], item.get("version", ""))
        return True

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
            # 记录刷新时间是整份 config.json 的读写；放界面线程会在每轮同步收尾
            # 压住输入（拖动窗口正好卡一下）。交给后台线程，写完不影响后续链。
            from app.ui.page import CallbackWorker

            self._mark_worker = CallbackWorker(mark_synced, self)
            self._mark_worker.start()
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
        """新站内信：前台用带「查看」按钮的消息条，后台用系统通知。"""
        if not items:
            return
        first = items[0]
        account = str(first.get("account", "")).strip()
        text = (first.get("content") or "").strip() or str(first.get("title", "")).strip()
        # 消息条只放一行：账号 + 半句内容，长文进系统消息页看
        if len(items) > 1:
            brief = f"{len(items)} 条新站内信"
        elif text:
            brief = f"{account}：{text[:24]}" if account else text[:28]
        else:
            brief = f"{account}：收到新站内信" if account else "收到新站内信"

        if self._is_foreground() and getattr(self, "_splash_done", False):
            from app.services.notices import Notice

            # 站内信不设标题、只显示内容；用 InfoBar 呈现（标题为空会自动隐藏）
            self._show_notice_bar(
                Notice(key="messages", title="", detail=brief, brief=brief,
                       action="messages", action_text="查看", tone="info"))
        else:
            self._alert(f"收到 {len(items)} 条新站内信", brief, "messages", "info")

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
            self._alert("智慧团委", "已缩到托盘继续后台刷新，右键托盘图标可退出")

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
        # 衬纸色按主题取（浅色近白 / 深色近黑）。不重设的话，切到浅色后窗口
        # 还压着深色那层半透明底，整屏发灰、文字发虚——用户报的「可读性差」。
        self._apply_window_background()
        page = getattr(self, "messages_page", None)
        if page is not None:
            page.apply_theme()
        # 换主题要重挂：Windows 的亚克力衬纸烘焙在 ACCENT_POLICY 里；macOS 上
        # 材质本身跟着深浅开关（浅色关、深色开）。浏览器窗口是独立窗口，一起换。
        if self._material != "none":
            self.apply_material(self._material)
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
        qconfig.themeChanged.connect(self._restyle_back_icon)
        set_tip(self.back_btn, "返回上一页")
        self.back_btn.setFixedSize(30, 30)
        self.back_btn.move(0, 0)

        # 标题栏放应用图标：带白底板的小方块，深浅主题下都有清晰边界
        from PyQt5.QtWidgets import QLabel

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

        # 标题栏居中搜索框：只有组织管理、系统消息、上传档案用。
        # 其他页面不隐藏它，只置灰禁用（见 _sync_search）——藏起来会让标题栏
        # 中间的视觉重心随切页左右跳
        self.search_edit = SearchLineEdit(self.titleBar)
        self.search_edit.setFixedHeight(30)
        self.search_edit.textChanged.connect(self._on_search_changed)
        self.stackedWidget.currentChanged.connect(self._sync_search)

        qrouter.emptyChanged.connect(self._sync_back_state)
        self.stackedWidget.currentChanged.connect(self._sync_back_state)
        self.org_manage_page.detail_changed.connect(self._sync_back_state)
        # 组织状况的「异常团员名单」同样是详情视图，返回键要能退回来
        self.org_page.detail_changed.connect(self._sync_back_state)
        self._sync_back_state()
        self._sync_search()
        QTimer.singleShot(0, self._place_search)

    def _fade_in_page(self, index: int):
        """切页时新页面淡入。

        库里那套动画只做位移（pos），没有透明度过渡，页首那一帧是硬出现的；
        这里给内容区补一段淡入，位移与透明度同时进行，观感才连贯。

        只淡入内容滚动区，不淡入整页：整页带 QGraphicsEffect 会让整块离屏渲染，
        Qt 里那等于每帧重新合成整个窗口，很贵。内容区本来就小得多。
        上一页的动效还没跑完就再次切页时，旧动画会被 fade_in 内部 stop 掉。
        """
        from app.ui.anim import fade_in

        page = self.stackedWidget.widget(index)
        content = getattr(page, "scrollWidget", None)
        if content is None:
            return
        fade_in(content, duration=180, dy=0)

    def _restyle_back_icon(self, *_):
        """主题切换后重烘焙返回键图标：图标是位图旋转来的，颜色生成时定死。

        槽必须是绑定方法（随窗口析构自动断开）；lambda 挂在 qconfig 全局
        信号上，窗口销毁后打进悬空对象，PyQt5 里直接 abort。"""
        from app.ui.page import chevron_left_icon

        self.back_btn.setIcon(chevron_left_icon(enabled=self.back_btn.isEnabled()))

    def _on_back_clicked(self):
        """详情视图优先退回它自己的列表，其余情况按导航历史返回上一页。"""
        page = self.stackedWidget.currentWidget()
        if page is self.org_manage_page and self.org_manage_page.go_back():
            return
        if page is self.org_page and self.org_page.back_to_overview():
            return
        qrouter.pop()

    def _sync_back_state(self, *_):
        from app.ui.page import chevron_left_icon

        page = self.stackedWidget.currentWidget()
        on_org = page is self.org_manage_page and self.org_manage_page.isVisible()
        on_stats = page is self.org_page and self.org_page.isVisible()
        detail = ((on_org and self.org_manage_page.in_detail())
                  or (on_stats and self.org_page.in_detail()))
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
    # 当前页没有搜索功能时搜索框显示的灰色提示：说明是这页不支持，
    # 不是坏了
    SEARCH_DISABLED_HINT = "当前页面没有可搜索的内容"

    def _sync_search(self, *_):
        """搜索框随页面切换：换提示语、清空关键词，没有搜索的页面置灰禁用。

        没有搜索的页面不隐藏搜索框：整块消失会让标题栏中间的视觉重心左右跳，
        用户也看不出「这页不能搜」和「搜索框坏了」的区别。禁用保留原位，
        提示语换成一句说明，一眼能看出是当前页不支持。
        """
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
        self.search_edit.setEnabled(hint is not None)
        self.search_edit.setPlaceholderText(hint or self.SEARCH_DISABLED_HINT)
        self._place_search()

    def _on_search_changed(self, text: str):
        page = self.stackedWidget.currentWidget()
        for attr, _hint in self.SEARCH_PAGES:
            target = getattr(self, attr, None)
            if target is page:
                target.set_search(text)
                return

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
        # 底部再让出窗口圆角：只留 side(4px) 时，最后一个项的高亮圆角会跟
        # 窗口的圆角弧线打架，看着像被窗边裁掉一块。让到圆角直径以内，
        # 两个圆角同心。
        bottom = max(item.x(), 12)
        m = lay.contentsMargins()
        if m.bottom() != bottom:
            lay.setContentsMargins(m.left(), m.top(), m.right(), bottom)

    def showEvent(self, e):
        super().showEvent(e)
        # 无边框窗口不会自动获得系统圆角（Windows 11 靠 DWM）。显示时窗口
        # 几何已稳定，可以顺手把 Win11 的投影补上（投影会改 frame，只能在
        # 这种「几何已定」的时机做，不能进 resizeEvent）。
        if sys.platform != "darwin":
            from app.services.materials import apply_rounded_corners
            from app.ui import palette as P

            apply_rounded_corners(self, P.RADIUS["window"])
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

    def changeEvent(self, e):
        super().changeEvent(e)
        # 最大化 / 还原 / 从最小化回来：DWM 会重算窗口 frame，亚克力与系统背板
        # 一起被复位成不透明底——用户看到的就是「一最大化就变纯黑（或浅色下变白）」。
        # 等这一帧几何定下来再重挂；不能同步做，重挂里有 DwmExtendFrameIntoClientArea，
        # 在窗口状态变化的中途改 frame 会被 DWM 再打回一次。
        if (e.type() == QEvent.Type.WindowStateChange
                and sys.platform == "win32" and self._material_applied):
            QTimer.singleShot(0, self._reapply_material)

    def _reapply_material(self):
        """按当前材质重挂一次混合层：主题切换与窗口状态变化的共用出口。"""
        if self._material and self._material != "none":
            self.set_material(self._material)

    def _normalBackgroundColor(self):
        """材质挂上后窗口底色给系统混合层让路，Qt 不再铺一层不透明底。

        基类按 isMicaEffectEnabled() 决定透不透明，而亚克力那条路不经过这个开关
        （Win10 上它从来没被打开过），照基类判断的结果就是把模糊整个盖掉。这里
        按自己的 _translucent 判断，两条路都一致。macOS 仍走基类：那边要在混合层
        上压一层六成不透明的衬纸，不能全透明。
        """
        if sys.platform == "win32" and getattr(self, "_translucent", False):
            return QColor(0, 0, 0, 0)
        return super()._normalBackgroundColor()

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self._translucent:
            painter = QPainter(self)
            painter.fillRect(self.rect(), page_bg_color())

    def _apply_window_background(self):
        # 底色由 page.window_background 统一给：账号网页窗口用的是同一套，
        # 两个窗口的透明度不会各调一份。深色全透明，亮度交给混合层；浅色铺
        # 近白防止桌面颜色透进卡片（浅色 CardWidget 只有 67% 白）。
        from app.ui.page import window_background

        self.setCustomBackgroundColor(*window_background(self._translucent))

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
        from app.services.materials import (
            apply_rounded_corners,
            apply_windows_effect,
            clear_corners,
            normalize,
            windows_material_available,
        )
        from app.ui import palette as P

        material = normalize(material)
        self._material = material
        if sys.platform == "darwin":
            # macOS 的浅色亚克力透的是桌面壁纸，叠出来是一层灰雾，正文压不住
            # （用户实测可读性差）；浅色一律铺不透明底，深色才走混合层。
            from qfluentwidgets import isDarkTheme as _dark

            self._translucent = material != "none" and _dark()
        elif sys.platform == "win32":
            # 深浅都挂材质。早期「深色刷白」是 ACCENT_ENABLE_ACRYLICBLURBEHIND
            # 那条旧通路的毛病；现在 acrylic 走 DWMWA_SYSTEMBACKDROP_TYPE、
            # mica 走系统的 WCA_USEDARKMODECOLORS，暗色 tint 由 DWM 自己上。
            # 旧结论套在新实现上，等于把一个本来能用的能力关掉了
            # （2026-11-21 Win11 24H2 实测：深色窗口拿到的是 #202020 兜底色）。
            self._translucent = windows_material_available(material)
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
        elif sys.platform == "win32":
            if self._translucent:
                # 圆角与投影先设：addShadowEffect 会重设 frame 扩展
                # （DwmExtendFrameIntoClientArea），放在材质之后会把 Mica 自己的
                # 客户区 margins 顶掉，效果就没了。
                apply_rounded_corners(self, P.RADIUS["window"])
                self._material_applied = apply_windows_effect(self, material)
                if not self._material_applied:
                    # 更老的系统挂不上，回退纯色底，不让窗口裸透明发黑
                    self._translucent = False
                    self._apply_window_background()
            else:
                # 切回「关闭」或这台机器挂不上材质：把还能生效的背板撤干净，
                # 否则旧材质会在下次启动时透出来
                from app.services.materials import remove_windows_effect

                remove_windows_effect(self)
                self._material_applied = True
        else:
            self._material_applied = True
        # 关掉材质后清掉圆角缓存，下次开材质时重新按当前窗口状态设置
        if sys.platform == "win32" and not self._translucent:
            clear_corners(self)
        self.update()
