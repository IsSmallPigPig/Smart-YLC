"""主窗口：FluentWindow 侧边栏六页导航。"""

from __future__ import annotations

import sys

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow, NavigationItemPosition

from app.ui.check_page import CheckPage
from app.ui.compress_page import CompressPage
from app.ui.home_page import HomePage
from app.ui.naming_page import NamingPage
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


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("智慧团建管理员辅助")
        self.setMinimumSize(960, 640)

        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        # 窗口自适应屏幕：85% 可用区域（上限 1180×760），居中
        screen = QApplication.primaryScreen().availableGeometry()
        w, h = min(1180, int(screen.width() * 0.85)), min(760, int(screen.height() * 0.85))
        self.resize(w, h)
        geo = self.frameGeometry()
        geo.moveCenter(screen.center())
        self.move(geo.topLeft())

        self.settings_page = SettingsPage()
        self.home_page = HomePage(self)
        self.naming_page = NamingPage(self)
        self.compress_page = CompressPage(self)
        self.upload_page = UploadPage(self)
        self.check_page = CheckPage(self)
        self.org_page = OrgPage(self)

        self.addSubInterface(self.home_page, FIF.HOME, "工具首页")
        self.addSubInterface(self.naming_page, FIF.EDIT, "命名检查")
        self.addSubInterface(self.compress_page, FIF.ZOOM, "批量压缩")
        self.addSubInterface(self.upload_page, FIF.CLOUD, "批量上传")
        self.addSubInterface(self.check_page, FIF.CERTIFICATE, "批量检查")
        self.addSubInterface(self.org_page, FIF.PIE_SINGLE, "组织状况")
        self.addSubInterface(
            self.settings_page, FIF.SETTING, "系统配置", NavigationItemPosition.BOTTOM
        )

        QTimer.singleShot(0, lambda: self.switchTo(self.home_page))
