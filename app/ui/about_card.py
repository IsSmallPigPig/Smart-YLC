"""系统配置的「关于」分组：作者主页、检查更新、开机自动检查、下载加速。

版本与更新包都来自 GitHub 仓库 IsSmallPigPig/Smart-Youth。
检查与下载都在后台线程，结果用 fluent 信息条反馈，网络失败明确写出原因。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QHBoxLayout, QWidget

from qfluentwidgets import (
    ComboBox,
    FluentIcon as FIF,
    HyperlinkButton,
    InfoBar,
    PrimaryPushButton,
    PushButton,
    SwitchButton,
)

from app import __version__
from app.services import update_check, updater
from app.ui.page import CollapsibleCard, FullRow, SettingRow, note_label


def _controls(*widgets, spacing: int = 8) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for w in widgets:
        lay.addWidget(w)
    return box


class AboutCard(CollapsibleCard):
    """关于：一行一个动作，右侧控件对齐，不额外堆说明文字。"""

    CREDIT = "本项目由广州天省实验学校 12407 欧阳文俊 @//issmallpigpig/ 开发。"

    def __init__(self, parent=None):
        super().__init__("关于", expanded=True, parent=parent)
        self._info: updater.UpdateInfo | None = None
        self._check_worker = None
        self._download_worker = None

        author_link = HyperlinkButton(updater.AUTHOR_URL, "打开作者主页")
        author_link.setIcon(FIF.GITHUB)
        self.body_lay.addWidget(SettingRow(
            "了解作者", desc=self.CREDIT, icon=FIF.PEOPLE,
            control=author_link, control_width=240))

        issues_link = HyperlinkButton(updater.ISSUES_URL, "提交反馈")
        issues_link.setIcon(FIF.FEEDBACK)
        self.body_lay.addWidget(SettingRow(
            "提供反馈", icon=FIF.FEEDBACK, control=issues_link, control_width=240))

        self.check_btn = PrimaryPushButton(FIF.UPDATE, "检查更新")
        self.check_btn.clicked.connect(self._check)
        self.download_btn = PushButton(FIF.DOWNLOAD, "下载更新")
        self.download_btn.clicked.connect(self._download)
        self.download_btn.setVisible(False)
        self.body_lay.addWidget(SettingRow(
            "检查更新", desc=f"当前版本 {__version__}，来自 {updater.REPO}",
            icon=FIF.UPDATE, control=_controls(self.check_btn, self.download_btn),
            control_width=260))

        self.status = note_label("")
        status_row = FullRow()
        status_row.lay.setContentsMargins(0, 0, 0, 10)
        status_row.lay.addWidget(self.status)
        self.body_lay.addWidget(status_row)
        self._refresh_status()

        self.auto_check = SwitchButton()
        self.auto_check.setChecked(update_check.auto_check_enabled())
        self.auto_check.checkedChanged.connect(
            lambda *_: update_check.set_auto_check(self.auto_check.isChecked()))
        self.body_lay.addWidget(SettingRow(
            "开机自动检查更新", icon=FIF.POWER_BUTTON,
            control=self.auto_check, control_width=180))

        self.proxy_combo = ComboBox()
        self.proxy_combo.addItems(updater.PROXY_LABELS)
        self.proxy_combo.setCurrentIndex(0 if update_check.proxy_mode() == "ghproxy" else 1)
        self.proxy_combo.setFixedWidth(180)
        self.proxy_combo.currentIndexChanged.connect(self._on_proxy_changed)
        self.body_lay.addWidget(SettingRow(
            "下载加速", icon=FIF.CLOUD, control=self.proxy_combo,
            control_width=180, separator=False))

    # ------------------------------------------------------------ 内部

    def _refresh_status(self):
        try:
            self.status.setText(update_check.last_check_text())
        except Exception as e:
            self.status.setText(f"更新记录读取失败：{e}")

    def _on_proxy_changed(self, index: int):
        update_check.set_proxy_mode("ghproxy" if index == 0 else "direct")

    def _check(self):
        if self._check_worker is not None and self._check_worker.isRunning():
            return
        self.check_btn.setEnabled(False)
        self.check_btn.setText("检查中")
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
        if info is None:
            InfoBar.info("已是最新版本", f"当前版本 {__version__}",
                         duration=3500, parent=self.window())
            self._info = None
            self.download_btn.setVisible(False)
            return
        self._info = info
        self.download_btn.setVisible(True)
        InfoBar.success("发现新版本", f"{info.version} 已发布，可下载更新包",
                        duration=6000, parent=self.window())

    def _on_check_failed(self, message: str):
        self._finish_check()
        update_check.record("error", message[:180])
        InfoBar.error("检查更新失败", message, duration=8000, parent=self.window())

    def _download(self):
        if self._info is None:
            return
        if self._download_worker is not None and self._download_worker.isRunning():
            return
        self.download_btn.setEnabled(False)
        worker = update_check.DownloadWorker(
            self._info, update_check.proxy_mode() == "ghproxy")
        self._download_worker = worker
        worker.progress.connect(lambda n: self.download_btn.setText(f"下载中 {n}%"))
        worker.fallback.connect(self._on_proxy_fallback)
        worker.done.connect(self._on_downloaded)
        worker.failed.connect(self._on_download_failed)
        worker.start()

    def _on_proxy_fallback(self, reason: str):
        InfoBar.warning("加速下载不可用", f"{reason}，已改用直连下载",
                        duration=7000, parent=self.window())

    def _finish_download(self):
        self.download_btn.setEnabled(True)
        self.download_btn.setText("下载更新")

    def _on_downloaded(self, path: str):
        self._finish_download()
        bar = InfoBar.success("更新包已下载", os.path.basename(path),
                              duration=10000, parent=self.window())
        open_btn = PushButton(FIF.FOLDER, "打开所在文件夹")
        open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path))))
        bar.addWidget(open_btn)

    def _on_download_failed(self, message: str):
        self._finish_download()
        InfoBar.error("下载失败", message, duration=10000, parent=self.window())
