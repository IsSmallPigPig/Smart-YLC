"""主窗口：FluentWindow 侧边栏六页导航。"""

from __future__ import annotations

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow, NavigationItemPosition

from app.ui.check_page import CheckPage
from app.ui.compress_page import CompressPage
from app.ui.home_page import HomePage
from app.ui.naming_page import NamingPage
from app.ui.settings_page import SettingsPage
from app.ui.upload_page import UploadPage

VERSION = "0.2.0-qt"


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("智慧团建管理员辅助")
        self.setMinimumSize(960, 640)

        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        # 窗口大小自适应屏幕：小屏缩到 85%，大屏用默认 1180×760
        screen = QApplication.primaryScreen().availableGeometry()
        w, h = min(1180, int(screen.width() * 0.85)), min(760, int(screen.height() * 0.85))
        self.resize(w, h)

        self.settings_page = SettingsPage()
        self.home_page = HomePage(self)
        self.naming_page = NamingPage(self)
        self.compress_page = CompressPage(self)
        self.upload_page = UploadPage(self)
        self.check_page = CheckPage(self)

        self.addSubInterface(self.home_page, FIF.HOME, "工具首页")
        self.addSubInterface(self.naming_page, FIF.EDIT, "命名检查")
        self.addSubInterface(self.compress_page, FIF.ZOOM, "批量压缩")
        self.addSubInterface(self.upload_page, FIF.CLOUD, "批量上传")
        self.addSubInterface(self.check_page, FIF.CERTIFICATE, "批量检查")
        self.addSubInterface(
            self.settings_page, FIF.SETTING, "系统配置", NavigationItemPosition.BOTTOM
        )

        QTimer.singleShot(0, lambda: self.switchTo(self.home_page))
