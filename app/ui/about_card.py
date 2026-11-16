"""系统配置的「关于」分组：作者主页、反馈、检查更新、下载加速。

检查与下载用右下角的浮动进度条反馈（和「同步数据」同一种），下载时按百分比
推进。下载完成后拉起独立进程静默安装，不弹安装窗口；装完发系统通知提醒重启。
失败时按钮变成「打开目录」，可以手动安装。
"""

from __future__ import annotations

import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QWidget

from qfluentwidgets import (
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PushButton,
)

from app import __version__
from app.services import silent_update, update_check, updater
from app.ui.combo import ComboBox
from app.ui.page import CONTROL_WIDTH, CollapsibleCard, SettingRow


def _controls(*widgets, spacing: int = 8) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for w in widgets:
        lay.addWidget(w)
    return box


def _fill(widget) -> QWidget:
    """把控件对齐到设置页统一的控件宽度，各行左边缘才在同一条线上。"""
    widget.setFixedWidth(CONTROL_WIDTH)
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget, 0)
    return box


def _button(icon, text: str) -> PushButton:
    """关于分组里每个动作都用同一种按钮：同形、同图标位置，宽度交给控件列。"""
    return PushButton(icon, text)


class AboutCard(CollapsibleCard):
    CREDIT = "本项目由广州天省实验学校 12407 欧阳文俊 @IsSmallPigPig 开发。"
    FEEDBACK_DESC = "遇到问题或有建议，欢迎告诉我们"

    def __init__(self, parent=None):
        super().__init__("关于项目", expanded=True, desc="版本、反馈与自动更新", parent=parent)
        self._info: updater.UpdateInfo | None = None
        self._check_worker = None
        self._download_worker = None
        self._toast = None
        self._downloaded = ""      # 已下载的更新包路径；有值时按钮变成「打开目录」
        self._fallback = ""        # 加速失败改直连时的原因，收尾文案里带上

        author_btn = _button(FIF.GITHUB, "作者主页")
        author_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(updater.AUTHOR_URL)))
        self.body_lay.addWidget(SettingRow(
            "了解作者", desc=self.CREDIT, icon=FIF.PEOPLE,
            control=_fill(author_btn)))

        license_btn = _button(FIF.CERTIFICATE, "GPL-3.0")
        license_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(updater.REPO_PAGE + "/blob/main/LICENSE")))
        self.body_lay.addWidget(SettingRow(
            "开源许可", desc="GPL-3.0，可自由分发与修改",
            icon=FIF.CERTIFICATE, control=_fill(license_btn)))

        feedback_btn = _button(FIF.FEEDBACK, "提交反馈")
        feedback_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(updater.ISSUES_URL)))
        self.body_lay.addWidget(SettingRow(
            "提供反馈", desc=self.FEEDBACK_DESC, icon=FIF.FEEDBACK,
            control=_fill(feedback_btn)))

        self.check_btn = _button(FIF.UPDATE, "检查更新")
        self.check_btn.clicked.connect(self._on_action)
        self.update_row = SettingRow(
            "检查更新", icon=FIF.UPDATE, control=_fill(self.check_btn))
        self.body_lay.addWidget(self.update_row)
        self._refresh_status()

        self.proxy_combo = ComboBox()
        self.proxy_combo.addItems(updater.gateway_labels())
        keys = list(updater.gateway_keys())
        try:
            self.proxy_combo.setCurrentIndex(keys.index(update_check.gateway_mode()))
        except ValueError:
            self.proxy_combo.setCurrentIndex(0)
        self.proxy_combo.currentIndexChanged.connect(self._on_proxy_changed)
        self.body_lay.addWidget(SettingRow(
            "下载加速", desc="更新弹窗里每个通道都能临时选；这里定默认", icon=FIF.CLOUD,
            control=_fill(self.proxy_combo), separator=False))

    # ------------------------------------------------------------ 状态小字

    def _refresh_status(self):
        try:
            status = update_check.last_check_text()
        except Exception as e:
            status = f"更新记录读取失败：{e}"
        self.update_row.set_desc(
            f"当前版本 {__version__}，来自 {updater.REPO}，{status}")

    # ------------------------------------------------------------ 浮动进度

    def _open_toast(self, title: str):
        """右下角浮动进度条：与「同步数据」同一个组件，不挡页面。"""
        from app.ui.widgets import ProgressToast

        self._close_toast()
        toast = ProgressToast(title, self.window())
        self._toast = toast
        toast.show()
        return toast

    def _close_toast(self, text: str = "", hold_ms: int = 800):
        """收掉浮层。text 非空时先补满并停留一下，把结果留在屏幕上。"""
        toast, self._toast = self._toast, None
        if toast is None:
            return
        try:
            if text:
                toast.finish(hold_ms=hold_ms, text=text)
            else:
                toast.close()
        except RuntimeError:
            pass  # 用户已点掉浮层

    def _notice(self, level: str, title: str, content: str, duration: int = 7000):
        getattr(InfoBar, level)(
            title, content, duration=duration,
            position=InfoBarPosition.BOTTOM_RIGHT, parent=self.window())

    # ------------------------------------------------------------ 检查更新

    def _on_proxy_changed(self, index: int):
        keys = list(updater.gateway_keys())
        if 0 <= index < len(keys):
            update_check.set_gateway_mode(keys[index])

    def _on_action(self):
        """一个按钮依次承担：检查更新 → 下载新版本 → 打开下载目录。"""
        if self._downloaded:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self._downloaded)))
            # 打开过目录就回到「检查更新」，别把按钮卡成只能开文件夹
            self._downloaded = ""
            self._info = None
            self.check_btn.setText("检查更新")
            self._refresh_status()
        elif self._info is not None:
            self._download()
        else:
            self._check()

    def _check(self):
        if self._check_worker is not None and self._check_worker.isRunning():
            return
        self._downloaded = ""
        self.check_btn.setEnabled(False)
        self.check_btn.setText("正在检查")
        self._open_toast("检查更新").set_indeterminate("正在检查更新…")
        worker = update_check.CheckWorker()
        self._check_worker = worker
        worker.found.connect(self._on_checked)
        worker.failed.connect(self._on_check_failed)
        worker.start()

    def _on_checked(self, info):
        update_check.record("update" if info else "latest", info.version if info else "")
        self._info = info
        self.check_btn.setEnabled(True)
        self._refresh_status()
        if info is None:
            self.check_btn.setText("检查更新")
            self._close_toast(text=f"已是最新版本 {__version__}")
            return
        self._close_toast()
        self.check_btn.setText(f"下载 {info.version}")
        self.update_row.set_desc(f"发现新版本 {info.version}，点右侧按钮下载（当前 {__version__}）")

    def _on_check_failed(self, message: str):
        update_check.record("error", message[:180])
        self._info = None
        self.check_btn.setEnabled(True)
        self.check_btn.setText("检查更新")
        self._refresh_status()
        self._close_toast()
        self._notice("error", "检查更新失败", message, 8000)

    # ------------------------------------------------------------ 下载更新

    def _download(self):
        if self._info is None:
            return
        if self._download_worker is not None and self._download_worker.isRunning():
            return
        # 每个通道一行下载按钮，点哪个走哪个；选完记住为默认
        from app.ui.gateway_picker import pick_gateway

        def _start(gw):
            update_check.set_gateway_mode(gw["key"])
            self._download_via(gw)

        pick_gateway(self.window(), _start, self._info.version)

    def _download_via(self, gw: dict):
        if self._download_worker is not None and self._download_worker.isRunning():
            return
        self._downloaded = ""
        self._fallback = ""
        self.check_btn.setEnabled(False)
        self.check_btn.setText("正在下载")
        self._open_toast("下载更新").set_progress(0, 100, "正在下载更新包 0%")
        worker = update_check.DownloadWorker(self._info, gw["key"])
        self._download_worker = worker
        worker.progress.connect(self._on_download_progress)
        worker.fallback.connect(self._on_proxy_fallback)
        worker.done.connect(self._on_downloaded)
        worker.failed.connect(self._on_download_failed)
        worker.start()

    def _on_download_progress(self, percent: int):
        toast = self._toast
        if toast is None:
            return
        try:
            toast.set_progress(percent, 100, f"正在下载更新包 {percent}%")
        except RuntimeError:
            self._toast = None

    def _on_proxy_fallback(self, reason: str):
        # 下载中再弹一条信息条会压在进度浮层上；回退这件事记下来，收尾时说
        self._fallback = reason

    def _on_downloaded(self, path: str):
        self._downloaded = path
        self.check_btn.setEnabled(False)
        self.check_btn.setText("正在安装")
        self.update_row.set_desc("更新包已下载，正在后台静默安装…")
        self._pending_install = path

        # 上传到一半退出会留下半截提交；等它结束再让出文件
        worker = getattr(getattr(self.window(), "upload_page", None), "worker", None)
        if worker is not None and getattr(worker, "isRunning", lambda: False)():
            self._notice("info", "上传进行中",
                         "上传完成后会自动安装更新，并通知你重启生效", 7000)
            worker.finished.connect(self._begin_silent_install)
            if not worker.isRunning():
                self._begin_silent_install()
            return
        from PySide6.QtCore import QTimer

        QTimer.singleShot(400, self._begin_silent_install)

    def _begin_silent_install(self):
        """拉起独立安装进程，然后退出主程序；完成通知由那个进程发出。"""
        path = getattr(self, "_pending_install", "")
        if not path:
            return
        self._pending_install = ""
        try:
            silent_update.start(path)
        except Exception as e:
            self._close_toast()
            self.check_btn.setEnabled(True)
            self.check_btn.setText("打开目录")
            self.update_row.set_desc(f"更新包已下载：{os.path.basename(path)}")
            self._notice("error", "无法静默安装",
                         f"{e}。可以点「打开目录」手动安装。", 10000)
            return

        text = "正在后台静默安装，完成后会通知你重启应用"
        if self._fallback:
            text = "所选通道不可用，已自动换通道下载；" + text
        self._close_toast(text=text, hold_ms=1200)
        self._quit_for_install()

    def _quit_for_install(self):
        """安装要替换正在运行的程序：先让主程序退出，再由安装助手接管。"""
        from PySide6.QtCore import QTimer

        quit_app = getattr(self.window(), "_quit_app", None)
        if quit_app is not None:
            QTimer.singleShot(1200, quit_app)

    def _on_download_failed(self, message: str):
        self.check_btn.setEnabled(True)
        self.check_btn.setText(f"重新下载 {self._info.version}" if self._info else "检查更新")
        # 失败也要收掉「正在下载」，否则浮层会一直停在下载中
        self._close_toast()
        self._notice("error", "下载失败", message, 10000)
