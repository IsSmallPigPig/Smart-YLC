"""系统配置的「关于」分组：作者主页、反馈、检查更新（含镜像源）。

检查与下载用右下角的浮动进度条反馈（和「同步数据」同一种），下载时按百分比
推进。下载完成后拉起独立进程静默安装，不弹安装窗口；装完发系统通知提醒重启。
失败时按钮变成「打开目录」，可以手动安装。
"""

from __future__ import annotations

from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QFileDialog, QHBoxLayout, QWidget

from qfluentwidgets import (
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PushButton,
)

from app import __version__, build_target
from app.services import app_log, silent_update, update_check, updater
from app.ui.mirror_combo import MirrorComboBox
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
    CREDIT = "本项目由广州天省实验学校 1240723 AnkoQAQ @IsSmallPigPig 开发。"
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

        guide_btn = _button(FIF.HELP, "查看引导")
        guide_btn.clicked.connect(self._open_guide)
        self.body_lay.addWidget(SettingRow(
            "使用引导", desc="要配置什么、能做什么，每学年 9 月 1 日后会自动再提示一次",
            icon=FIF.HELP, control=_fill(guide_btn)))

        license_btn = _button(FIF.CERTIFICATE, "GPL-3.0")
        license_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(updater.REPO_PAGE + "/blob/main/LICENSE")))
        self.body_lay.addWidget(SettingRow(
            "开源许可", desc="GPL-3.0，可自由分发与修改",
            icon=FIF.CERTIFICATE, control=_fill(license_btn)))

        export_btn = _button(FIF.DOCUMENT, "导出日志")
        export_btn.setFixedWidth(CONTROL_WIDTH)
        export_btn.clicked.connect(self._export_logs)
        feedback_btn = _button(FIF.FEEDBACK, "提交反馈")
        feedback_btn.setFixedWidth(CONTROL_WIDTH)
        feedback_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(updater.ISSUES_URL)))
        self.body_lay.addWidget(SettingRow(
            "提供反馈", desc=self.FEEDBACK_DESC, icon=FIF.FEEDBACK,
            control=_controls(export_btn, feedback_btn)))

        # 镜像源与检查按钮同一行：左侧选走哪个站，右侧执行检查/下载
        self.mirror_combo = MirrorComboBox()
        self.check_btn = _button(FIF.UPDATE, "检查更新")
        self.check_btn.setFixedWidth(CONTROL_WIDTH)
        self.check_btn.clicked.connect(self._on_action)
        self.update_row = SettingRow(
            "检查更新", icon=FIF.UPDATE,
            control=_controls(self.mirror_combo, self.check_btn))
        self.body_lay.addWidget(self.update_row)
        self._refresh_status()

    # ------------------------------------------------------------ 运行日志

    def _open_guide(self):
        """再读一遍使用引导（不写回展示时间：不影响下一学年的自动提示）。"""
        from app.ui.guide_dialog import GuideDialog

        dlg = GuideDialog(self.window())
        dlg.show()
        self._guide = dlg      # 留着引用，避免被回收

    def _export_logs(self):
        """导出全部滚动日志到用户指定位置。日志可能含路径等信息，但不包含配置里的密码。"""
        from app.services.export_data import default_name

        path, _ = QFileDialog.getSaveFileName(
            self.window(), "导出运行日志", default_name("运行日志", ".txt"),
            "文本文件 (*.txt)")
        if not path:
            return
        if not path.lower().endswith(".txt"):
            path += ".txt"
        try:
            count = app_log.export_logs(path)
        except OSError as e:
            self._notice("error", "导出日志失败", str(e), 8000)
            return
        self._notice("success", "日志已导出",
                     f"已写入 {count} 份日志：{path}", 8000)

    # ------------------------------------------------------------ 状态小字

    def _refresh_status(self):
        try:
            status = update_check.last_check_text()
        except Exception as e:
            status = f"更新记录读取失败：{e}"
        variant = build_target()
        label = f"当前版本 {__version__}" + (f"（{variant}）" if variant else "")
        self.update_row.set_desc(f"{label}，来自 {updater.REPO}，{status}")

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

    def _on_action(self):
        """一个按钮依次承担：检查更新 → 立即更新（下载）→ 待重启（重启确认）。"""
        if self._downloaded:
            self._restart_update()
        elif self._info is not None:
            self._download()
        else:
            self._check()

    def shutdown(self):
        """退出前等联网线程收尾：PyQt5 析构运行中的 QThread 会 abort。

        下载没有可中断的 stop()，最多等 2 秒；超时后进程本来就要退出，
        但至少不会在窗口销毁时被 GC 当场析构。由设置页的 shutdown 调用。
        """
        for worker in (self._check_worker, self._download_worker):
            if worker is not None and worker.isRunning():
                worker.wait(2000)

    def _check(self):
        if self._check_worker is not None and self._check_worker.isRunning():
            return
        self._downloaded = ""
        self.check_btn.setEnabled(False)
        self.check_btn.setText("正在检查")
        self._open_toast("检查更新").set_indeterminate("正在检查更新…")
        worker = update_check.CheckWorker(parent=self)
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
        self.check_btn.setText("立即更新")
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
        """按镜像源设置直接开下，不再临时问一遍走哪个站。"""
        if self._info is None:
            return
        if self._download_worker is not None and self._download_worker.isRunning():
            return
        self._download_via(self.mirror_combo.mode())

    def _download_via(self, mode: str):
        if self._download_worker is not None and self._download_worker.isRunning():
            return
        self._downloaded = ""
        self._fallback = ""
        self.check_btn.setEnabled(False)
        self.check_btn.setText("正在下载")
        # 自动模式还没测过速时，下载前会先探一轮路，文案要说清在等什么
        waiting = "正在测速挑选镜像站…" if (
            mode == updater.AUTO and not updater.probe_fresh()) else "正在下载更新包 0%"
        self._open_toast("下载更新").set_progress(0, 100, waiting)
        worker = update_check.DownloadWorker(self._info, mode, parent=self)
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
        """下载完成：不自动安装退出，记下更新包并提示用户重启确认。"""
        self._downloaded = path
        version = getattr(self._info, "version", "") or ""
        silent_update.save_pending_installer(path, version)
        self.check_btn.setEnabled(True)
        self.check_btn.setText("待重启")
        self.update_row.set_desc("更新包已下载，重启后生效")
        self._close_toast(text="更新包已下载")

        win = self.window()
        show = getattr(win, "show_update_ready", None)
        if callable(show):
            show(path, version)

    def _restart_update(self):
        """设置页里的「待重启」：与常驻消息上的「重启」同一动作。"""
        restart = getattr(self.window(), "_restart_for_update", None)
        if callable(restart):
            restart(self._downloaded, getattr(self._info, "version", "") or "")

    def _on_download_failed(self, message: str):
        self.check_btn.setEnabled(True)
        self.check_btn.setText("重新下载" if self._info else "检查更新")
        # 失败也要收掉「正在下载」，否则浮层会一直停在下载中
        self._close_toast()
        self._notice("error", "下载失败", message, 10000)
