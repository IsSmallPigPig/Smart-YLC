"""主窗口：MSFluentWindow（macOS 风格窄侧边栏），返回按钮在标题栏左上角，
支持毛玻璃混合背景（系统配置可设）。文档检查/批量压缩在侧边栏底部与系统配置一组。"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    MSFluentWindow,
    NavigationItemPosition,
    TransparentToolButton,
    qrouter,
)

from app.domain.account_health import excluded_note, split_accounts
from app.services import store
from app.ui.check_page import CheckPage
from app.ui.compress_page import CompressPage
from app.ui.home_page import HomePage, page_bg_color
from app.ui.naming_page import NamingPage
from app.ui.org_manage_page import OrgManagePage
from app.ui.org_page import OrgPage
from app.ui.settings_page import SettingsPage
from app.ui.messages_page import MessagesPage
from app.ui.upload_page import UploadPage

VERSION = "0.2.0-qt"

if sys.platform == "darwin":
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

    # Qt 必须在原生 NSWindow 创建之前知道窗口要透明：qframelesswindow 的
    # _initFrameless() 里会调 winId() 创建 NSWindow，之后再设
    # WA_TranslucentBackground 只改 Qt 属性，backing store 仍没有 alpha
    # （alphaBufferSize=-1）、NSWindow 仍是 opaque，behind-window 混合层
    # 不会采样窗口背后的桌面内容。运行期再调 setOpaque_(False) 会被
    # QNSWindow 立刻覆盖回来，只能在这里提前声明。
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

        self.settings_page = SettingsPage()
        self.home_page = HomePage(self)
        self.naming_page = NamingPage(self)
        self.compress_page = CompressPage(self)
        self.upload_page = UploadPage(self)
        self.check_page = CheckPage(self)
        self.org_page = OrgPage(self)
        self.org_manage_page = OrgManagePage(self)
        self.messages_page = MessagesPage(self)
        self.settings_page.window_ref = self  # 毛玻璃开关与团支书导入后的页面刷新
        self.settings_page.home_page = self.home_page  # 系统配置里设置头图后即时刷新首页

        self.addSubInterface(self.home_page, FIF.HOME, "工具首页")
        self.addSubInterface(self.upload_page, FIF.CLOUD, "批量上传")
        self.addSubInterface(self.check_page, FIF.CERTIFICATE, "批量检查")
        self.addSubInterface(self.org_page, FIF.PIE_SINGLE, "组织状况")
        self.addSubInterface(self.org_manage_page, FIF.PEOPLE, "组织管理")
        self.addSubInterface(self.messages_page, FIF.MESSAGE, "系统消息")
        # 文档类工具与系统配置放在一起，置于侧边栏底部
        self.addSubInterface(self.naming_page, FIF.EDIT, "文档检查", position=NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.compress_page, FIF.ZOOM, "批量压缩", position=NavigationItemPosition.BOTTOM)
        self.addSubInterface(
            self.settings_page, FIF.SETTING, "系统配置", position=NavigationItemPosition.BOTTOM
        )

        # 标题栏返回按钮：默认无背景，中心位于窗口外框左上角(0,0)与
        # 主页面背景左上角(侧边栏右缘, 标题栏下沿48)连线的中点
        self._setup_back_button()

        QTimer.singleShot(0, lambda: self.switchTo(self.home_page))
        # 启动恢复窗口背景透明材质（兼容旧 translucent 布尔配置）。混合层要等
        # 窗口 show 之后 contentView 尺寸稳定才挂，实际动作在 showEvent 里。
        cfg = store.load()
        self._material = cfg.get("translucentMaterial") or ("acrylic" if cfg.get("translucent") else "none")
        self._translucent = self._material != "none"
        self._apply_window_background()

        # 明暗模式切换（手动或跟随系统变化）：重绘窗口底色 + 重建带烘焙颜色的页面
        from qfluentwidgets import qconfig

        qconfig.themeChanged.connect(lambda *_: self.on_theme_changed())

        # 统一数据刷新：成员缓存 → 站内信 → 组织状况统计，三段都在工作线程串行执行。
        # 30 秒快刷（后台静默）：成员缓存按 4 小时新鲜度跳过，站内信每轮强制拉取
        # 检测新消息（有新消息弹系统通知），组织状况从成员缓存离线重算；
        # 手动入口在「系统配置」（在线重查全部数据）。
        self._refreshing = False
        self._refresh_silent = True
        self._refresh_worker: "QThread | None" = None
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(30 * 1000)
        self._sync_timer.timeout.connect(self._maybe_background_sync)
        self._sync_timer.start()
        QTimer.singleShot(15 * 1000, self._maybe_background_sync)

    def _maybe_background_sync(self):
        """30 秒一次的静默快刷；刷新中不重复启动，没有账号就跳过。"""
        if not self.settings_page.accounts:
            return
        self.refresh_all(silent=True, fast=True)

    def refresh_all(self, silent: bool = True, fast: bool = False) -> bool:
        """刷新所有数据：成员缓存 → 站内信 → 组织状况统计。

        全程工作线程（网络不进 GUI 线程）；进行中再次调用直接返回 False。
        fast=False（手动/慢刷）：组织状况在线重查（OrgWorker）。
        fast=True（30 秒快刷）：站内信绕开新鲜跳过强制拉取，拉到新消息弹系统
        通知；成员缓存仍按 4 小时新鲜度跳过（不重复请求）；有账号真的同步到
        新成员时组织状况从缓存离线重算，否则保留现有展示。
        「系统配置」的按钮走 silent=False（有开始/完成提示），定时快刷走静默。"""
        from qfluentwidgets import InfoBar

        from app.ui.messages_page import MessageFetchWorker
        from app.ui.org_manage_page import MemberSyncWorker, mark_synced

        usable, excluded = split_accounts(self.settings_page.accounts)
        accounts = usable
        if not accounts:
            if not silent:
                hint = (f"其余账号均被标记为异常（{excluded_note(excluded)}），"
                        "请先处理（测活或打开网页端重新登录）") if excluded \
                    else "先在「系统配置」添加团支部账号"
                InfoBar.warning("没有可用账号", hint, duration=6000, parent=self)
            return False
        if excluded and not silent:
            InfoBar.warning("跳过异常账号",
                f"{excluded_note(excluded)} 不参与本轮刷新；测活恢复正常后自动纳入",
                duration=6000, parent=self)
        if self._refreshing:
            if not silent:
                InfoBar.info("刷新进行中", "上一轮还没跑完，稍候", duration=3000, parent=self)
            return False

        self._refreshing = True
        self._refresh_silent = silent
        if not silent:
            InfoBar.info("开始刷新", f"成员缓存 → 站内信 → 组织状况，共 {len(accounts)} 个账号", duration=4000, parent=self)

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
            if not self._refresh_silent:
                InfoBar.success("刷新完成", "成员、站内信、组织状况均已更新", duration=4000, parent=self)

        sync_w = MemberSyncWorker(accounts, sequential=True)
        self._refresh_worker = sync_w
        sync_w.account_done.connect(_on_sync_one)
        sync_w.all_done.connect(_after_sync)
        sync_w.start()
        return True

    def _notify_new_messages(self, items: list):
        """线上来了新站内信 → 系统通知（macOS osascript）。"""
        if not items:
            return
        from app.services.notify import send

        first = items[0]
        body = f"{first.get('account', '')}：{(first.get('content') or '').strip() or first.get('title', '')}"
        send(f"收到 {len(items)} 条新站内信", body[:120])

    def on_theme_changed(self):
        """明暗模式变化：窗口底色重绘；消息卡片右侧小标签的底色是按当前
        主题烘焙的，需要重建列表。其余页面样式由 fluent 主题联动自动更新。"""
        self.update()
        if hasattr(self, "messages_page") and self.messages_page._views:
            self.messages_page._rebuild()

    def _setup_back_button(self):
        self.back_btn = TransparentToolButton(FIF.RETURN, self.titleBar)
        self.back_btn.setToolTip("返回上一页")
        self.back_btn.setFixedSize(34, 34)

        tb = self.titleBar.hBoxLayout
        # 去掉默认的左间距与应用图标占位，让按钮按对角线定位
        tb.removeWidget(self.titleBar.iconLabel)
        self.titleBar.iconLabel.hide()
        while tb.count() and tb.itemAt(0).spacerItem():
            tb.takeAt(0)
        tb.insertWidget(0, self.back_btn, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.back_btn.clicked.connect(lambda: qrouter.pop())
        QTimer.singleShot(0, self._place_back_button)

        def _enable_back(empty: bool):
            self.back_btn.setEnabled(not empty)

        qrouter.emptyChanged.connect(_enable_back)
        _enable_back(not qrouter.history)

    def _place_back_button(self):
        tb = self.titleBar.hBoxLayout
        # 撤掉旧占位（重复定位时先清理）
        while tb.count() and tb.itemAt(0).spacerItem():
            tb.takeAt(0)
        idx = tb.indexOf(self.back_btn)
        if idx >= 0:
            tb.takeAt(idx)
        nav_w = self.navigationInterface.width() or 66
        pad = max(8, int(nav_w / 2 - self.back_btn.width() / 2))
        tb.insertSpacing(0, pad)
        tb.insertWidget(1, self.back_btn, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.titleBar.titleLabel.setText(self.windowTitle())
        self.titleBar.titleLabel.adjustSize()

    def showEvent(self, e):
        super().showEvent(e)
        if not self._material_applied:
            self.set_material(self._material)
            if not self._material_applied:
                QTimer.singleShot(0, lambda: self.set_material(self._material))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if sys.platform == "darwin" and self._translucent:
            from app.services.mac_effect import sync_frame

            sync_frame(self)

    def paintEvent(self, e):
        # FluentWidget.paintEvent 会画一层 backgroundColor；开启材质时它由
        # _apply_window_background 设为全透明，混合层才不会被盖住。
        super().paintEvent(e)
        if not self._translucent:
            painter = QPainter(self)
            painter.fillRect(self.rect(), page_bg_color())

    def _apply_window_background(self):
        if self._translucent:
            self.setCustomBackgroundColor(QColor(0, 0, 0, 0), QColor(0, 0, 0, 0))
        else:
            self.setCustomBackgroundColor(QColor(240, 244, 249), QColor(32, 32, 32))

    def set_material(self, material: str):
        """窗口背景透明材质：none 关闭（纯色底），acrylic/sidebar/hud 为混合层。

        配置持久化 translucentMaterial，系统配置里可切；兼容旧 translucent 布尔。"""
        material = material if material in ("none", "acrylic", "sidebar", "hud") else "none"
        self._material = material
        # 混合层只在 macOS 实现；其他平台保持纯色底（否则背景没人画，窗口发黑）
        self._translucent = material != "none" and sys.platform == "darwin"
        self._apply_window_background()
        if sys.platform == "darwin":
            from app.services.mac_effect import disable_acrylic, enable_acrylic

            if self._translucent:
                self._material_applied = enable_acrylic(self, material)
            else:
                disable_acrylic(self)
                self._material_applied = True
        self.update()
