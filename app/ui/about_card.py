"""系统配置的「关于」分组：作者主页、反馈、检查更新、下载加速。

检查结果用右下角浮动信息条反馈：检查中常驻，已是最新自动收起，发现新版时
信息条上直接给「立即下载」，不再往分组里塞第二个下载按钮。
"""

from __future__ import annotations

import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QWidget

from qfluentwidgets import (
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PushButton,
)

from app import __version__
from app.services import update_check, updater
from app.ui.page import CollapsibleCard, SettingRow


def _controls(*widgets, spacing: int = 8) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for w in widgets:
        lay.addWidget(w)
    return box


def _fill(widget) -> QWidget:
    """把一个控件铺满标准控件列，各行左边缘才对得齐。"""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget, 1)
    return box


def _button(icon, text: str) -> PushButton:
    """关于分组里每个动作都用同一种按钮：同形、同图标位置，宽度交给控件列。"""
    return PushButton(icon, text)


class AboutCard(CollapsibleCard):
    CREDIT = "本项目由广州天省实验学校 12407 欧阳文俊 @IsSmallPigPig 开发。"
    FEEDBACK_DESC = "遇到问题或有建议，欢迎告诉我们"

    def __init__(self, parent=None):
        super().__init__("关于项目", expanded=True, parent=parent)
        self._info: updater.UpdateInfo | None = None
        self._check_worker = None
        self._download_worker = None
        self._bar = None

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
            "开源许可", icon=FIF.CERTIFICATE, control=_fill(license_btn)))

        feedback_btn = _button(FIF.FEEDBACK, "提交反馈")
        feedback_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(updater.ISSUES_URL)))
        self.body_lay.addWidget(SettingRow(
            "提供反馈", desc=self.FEEDBACK_DESC, icon=FIF.FEEDBACK,
            control=_fill(feedback_btn)))

        self.check_btn = _button(FIF.UPDATE, "检查更新")
        self.check_btn.clicked.connect(self._check)
        self.update_row = SettingRow(
            "检查更新", icon=FIF.UPDATE, control=_fill(self.check_btn))
        self.body_lay.addWidget(self.update_row)
        self._refresh_status()

        self.proxy_combo = ComboBox()
        self.proxy_combo.addItems(updater.PROXY_LABELS)
        self.proxy_combo.setCurrentIndex(0 if update_check.proxy_mode() == "ghproxy" else 1)
        self.proxy_combo.currentIndexChanged.connect(self._on_proxy_changed)
        self.body_lay.addWidget(SettingRow(
            "下载加速", icon=FIF.CLOUD, control=_fill(self.proxy_combo),
            separator=False))

    # ------------------------------------------------------------ 状态小字

    def _refresh_status(self):
        try:
            status = update_check.last_check_text()
        except Exception as e:
            status = f"更新记录读取失败：{e}"
        self.update_row.set_desc(
            f"当前版本 {__version__}，来自 {updater.REPO}，{status}")

    # ------------------------------------------------------------ 浮动信息条

    def _close_bar(self):
        bar, self._bar = self._bar, None
        if bar is None:
            return
        try:
            bar.close()
        except RuntimeError:
            pass

    def _show_bar(self, level: str, title: str, content: str, *,
                  duration: int = 4000, widget=None, sticky: bool = False):
        """右下角信息条；sticky 的条常驻，供检查中与发现新版本使用。"""
        if sticky:
            self._close_bar()
        bar = getattr(InfoBar, level)(
            title, content, duration=-1 if sticky else duration,
            position=InfoBarPosition.BOTTOM_RIGHT, parent=self.window())
        if widget is not None:
            bar.addWidget(widget)
        if sticky:
            self._bar = bar
        return bar

    def _set_bar_content(self, text: str):
        if self._bar is None:
            return
        try:
            self._bar.contentLabel.setText(text)
        except RuntimeError:
            pass

    def _notice(self, level: str, title: str, content: str, duration: int = 7000):
        self._show_bar(level, title, content, duration=duration)

    # ------------------------------------------------------------ 检查更新

    def _on_proxy_changed(self, index: int):
        update_check.set_proxy_mode("ghproxy" if index == 0 else "direct")

    def _check(self):
        if self._check_worker is not None and self._check_worker.isRunning():
            return
        self.check_btn.setEnabled(False)
        self.check_btn.setText("正在检查")
        self._show_bar("info", "检查更新", "正在检查更新…", sticky=True)
        worker = update_check.CheckWorker()
        self._check_worker = worker
        worker.found.connect(self._on_checked)
        worker.failed.connect(self._on_check_failed)
        worker.start()

    def _finish_check(self):
        self.check_btn.setEnabled(True)
        self.check_btn.setText("检查更新")
        self._refresh_status()

    def _on_checked(self, info):
        self._finish_check()
        update_check.record("update" if info else "latest", info.version if info else "")
        self._info = info
        if info is None:
            self._close_bar()
            self._notice("success", "已是最新版本", f"当前版本 {__version__}", 3500)
            return
        dl = _button(FIF.DOWNLOAD, "立即下载")
        dl.clicked.connect(self._download)
        self._download_action = dl
        self._show_bar("success", "检测到新版本",
                       f"最新版本: {info.version}", widget=dl, sticky=True)

    def _on_check_failed(self, message: str):
        self._finish_check()
        update_check.record("error", message[:180])
        self._notice("error", "检查更新失败", message, 8000)

    # ------------------------------------------------------------ 下载更新

    def _download(self):
        if self._info is None:
            return
        if self._download_worker is not None and self._download_worker.isRunning():
            return
        self._show_bar("info", "下载更新", "正在下载更新包…", sticky=True)
        worker = update_check.DownloadWorker(
            self._info, update_check.proxy_mode() == "ghproxy")
        self._download_worker = worker
        worker.progress.connect(lambda n: self._set_bar_content(f"正在下载更新包 {n}%"))
        worker.fallback.connect(self._on_proxy_fallback)
        worker.done.connect(self._on_downloaded)
        worker.failed.connect(self._on_download_failed)
        worker.start()

    def _on_proxy_fallback(self, reason: str):
        self._notice("warning", "加速下载不可用", f"{reason}，已改用直连下载")

    def _on_downloaded(self, path: str):
        open_btn = _button(FIF.FOLDER, "打开目录")
        open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path))))
        self._show_bar("success", "更新包已下载", os.path.basename(path),
                       widget=open_btn, sticky=True)

    def _on_download_failed(self, message: str):
        self._notice("error", "下载失败", message, 10000)
