"""主窗口：MSFluentWindow（macOS 风格窄侧边栏），返回按钮在标题栏左上角，
支持毛玻璃混合背景（系统配置可设）。文档检查/批量压缩在侧边栏底部与系统配置一组。"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    MSFluentWindow,
    NavigationItemPosition,
    TransparentToolButton,
    qrouter,
)

from app.services import store
from app.ui.check_page import CheckPage
from app.ui.compress_page import CompressPage
from app.ui.home_page import HomePage, page_bg_color
from app.ui.naming_page import NamingPage
from app.ui.org_manage_page import OrgManagePage
from app.ui.org_page import OrgPage
from app.ui.settings_page import SettingsPage
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


class MainWindow(MSFluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("智慧团建管理员辅助")
        self.setMinimumSize(960, 640)

        # 窗口自适应屏幕：85% 可用区域（上限 1180×760），居中
        screen = QApplication.primaryScreen().availableGeometry()
        w, h = min(1180, int(screen.width() * 0.85)), min(760, int(screen.height() * 0.85))
        self.resize(w, h)
        geo = self.frameGeometry()
        geo.moveCenter(screen.center())
        self.move(geo.topLeft())

        # 毛玻璃：窗口底色改为自己绘制（关闭时纯色、开启时露出混合层）
        self._translucent = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("MainWindow{background: transparent}")

        self.settings_page = SettingsPage()
        self.home_page = HomePage(self)
        self.naming_page = NamingPage(self)
        self.compress_page = CompressPage(self)
        self.upload_page = UploadPage(self)
        self.check_page = CheckPage(self)
        self.org_page = OrgPage(self)
        self.org_manage_page = OrgManagePage(self)
        self.settings_page.window_ref = self  # 毛玻璃开关与团支书导入后的页面刷新
        self.settings_page.home_page = self.home_page  # 系统配置里设置头图后即时刷新首页

        self.addSubInterface(self.home_page, FIF.HOME, "工具首页")
        self.addSubInterface(self.upload_page, FIF.CLOUD, "批量上传")
        self.addSubInterface(self.check_page, FIF.CERTIFICATE, "批量检查")
        self.addSubInterface(self.org_page, FIF.PIE_SINGLE, "组织状况")
        self.addSubInterface(self.org_manage_page, FIF.PEOPLE, "组织管理")
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
        if store.load().get("translucent"):
            self.set_translucent(True)

        # 后台静默刷新：智慧团建数据每 4 小时一轮，无用户主动刷新时才跑；
        # 逐账号串行 + 间隔，避免触发风控。启动 1 分钟后先检查一次。
        self._bg_sync_worker: "MemberSyncWorker | None" = None
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(4 * 3600 * 1000)
        self._sync_timer.timeout.connect(self._maybe_background_sync)
        self._sync_timer.start()
        QTimer.singleShot(60 * 1000, self._maybe_background_sync)

    def _maybe_background_sync(self):
        """距上次刷新 ≥4 小时（或从没刷过）才静默刷新；刷新中不重复启动。"""
        from datetime import datetime, timezone

        if self._bg_sync_worker is not None and self._bg_sync_worker.isRunning():
            return
        accounts = self.settings_page.accounts
        if not accounts:
            return
        last = store.load().get("lastSyncAt", "")
        if last:
            try:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            except ValueError:
                elapsed = None
            if elapsed is not None and elapsed < 4 * 3600:
                return
        from app.ui.org_manage_page import MemberSyncWorker, mark_synced

        self._bg_sync_worker = MemberSyncWorker(accounts, sequential=True)
        self._bg_sync_worker.all_done.connect(lambda: mark_synced())
        self._bg_sync_worker.all_done.connect(
            lambda: self.org_manage_page.refresh()
            if self.stackedWidget.currentWidget() is self.org_manage_page else None)
        self._bg_sync_worker.start()

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

    def paintEvent(self, e):
        if not self._translucent:
            painter = QPainter(self)
            painter.fillRect(self.rect(), page_bg_color())
        super().paintEvent(e)

    def set_translucent(self, on: bool):
        """毛玻璃混合背景开关（macOS）；配置持久化，系统配置里可切。"""
        self._translucent = bool(on)
        if sys.platform != "darwin":
            return
        from app.services.mac_effect import disable_acrylic, enable_acrylic

        if on:
            enable_acrylic(self)
        else:
            disable_acrylic(self)
        self.update()
