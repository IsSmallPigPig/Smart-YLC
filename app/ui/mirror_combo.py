"""镜像源下拉：自动 / 各加速站（带实测延迟）/ 直连 / 自定义。

列表按社区整理的公开镜像清单展开，站名后面跟一次实测结果——"(845 ms)"是
本轮测速的耗时，"(超时)"、"(限流)"、"(不可用)"是没拿到文件的站。测速在后台
线程并发跑，界面不等它；设置页一露头就先测一轮，等用户点开下拉时结果基本
已经在列里了。

选择写进配置（updateProxy），下载时按它排序：自动＝按延迟从快到慢、指定＝该站
排第一，无论选哪个，直连 GitHub 都留在序列里兜底。自定义站址写
updateCustomMirror，支持 "https://xxx/" 或不带协议的裸域名。
"""

from __future__ import annotations

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QApplication, QDialog

from qfluentwidgets import InfoBar, InfoBarPosition, LineEdit

from app.services import update_check, updater
from app.ui.combo import ComboBox
from app.ui.dialogs import FormDialog
from app.ui.page import CONTROL_WIDTH

AUTO_TEXT = "自动选择"
CUSTOM_TEXT = "自定义"

_WORKERS: list = []   # 测速线程保活，跑完自己摘掉
_QUIT_HOOKED = False


def _wait_probe_on_quit():
    """退出前收一下测速线程：线程还在跑时对象被回收会打印 QThread 警告甚至崩。"""
    for worker in list(_WORKERS):
        if worker.isRunning():
            worker.wait(2500)


class MirrorComboBox(ComboBox):
    """镜像源下拉：选中即存，测速结果回填到列表文案。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ready = False
        self._worker = None
        self._closed = ""
        self._last_index = 0
        self._probed_once = False
        self.setFixedWidth(CONTROL_WIDTH)
        self.setToolTip("下载更新包走哪个镜像站；自动＝测速后选最快的")

        self.addItem(AUTO_TEXT, userData=updater.AUTO)
        for gw in updater.MIRRORS:
            self.addItem(gw["label"], userData=gw["key"])
        self.addItem(updater.DIRECT["label"], userData=updater.DIRECT["key"])
        self.addItem(CUSTOM_TEXT, userData=updater.CUSTOM)

        index = self._index_of(update_check.mirror_mode())
        self.setCurrentIndex(max(0, index))
        self._last_index = max(0, index)
        self._ready = True
        self.currentIndexChanged.connect(self._on_index_changed)
        self._refresh_display()

    # ------------------------------------------------------------ 展示

    def mode(self) -> str:
        """当前选择：auto / 站 key / custom。"""
        return str(self.currentData() or updater.AUTO)

    def closed_text(self) -> str:
        """收起状态该显示的完整文案，未经窗口宽度省略。

        控件上的 text() 是按 126px 省略过的，跨平台字宽不同，拿它做断言或
        比较都会飘；要判「显示的是什么站」用这个。"""
        return self._closed

    def _index_of(self, mode: str) -> int:
        for i in range(self.count()):
            if self.itemData(i) == mode:
                return i
        return 0

    def _closed_text(self) -> str:
        """收起状态只显示站名/模式，延迟留在列表里——126px 塞不下两样。"""
        mode = self.mode()
        if mode == updater.CUSTOM:
            custom = updater.custom_gateway(update_check.custom_mirror())
            return custom["label"] if custom else CUSTOM_TEXT
        if mode == updater.AUTO:
            return AUTO_TEXT
        gw = updater.gateway_by_key(mode)
        return gw["label"] if gw else AUTO_TEXT

    def _refresh_display(self):
        text = self._closed_text()
        self._closed = text
        self.setToolTip(f"镜像源：{text}（点开可换）")
        self._apply_elide()

    def _apply_elide(self):
        """126px 放不下长域名：尾部省略，完整站名在列表和提示里。"""
        fm = self.fontMetrics()
        avail = max(40, self.width() - 30)
        super().setText(fm.elidedText(self._closed, Qt.TextElideMode.ElideRight, avail))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_elide()

    # ------------------------------------------------------------ 选择

    def _on_index_changed(self, index: int):
        if not self._ready or index < 0:
            return
        previous = self._last_index
        self._last_index = index
        mode = str(self.itemData(index) or updater.AUTO)
        if mode == updater.CUSTOM:
            self._prompt_custom(previous)
            return
        update_check.set_mirror_mode(mode)
        self._refresh_display()

    def _select_silently(self, index: int):
        self._ready = False
        self.setCurrentIndex(max(0, index))
        self._last_index = max(0, index)
        self._ready = True
        self._refresh_display()

    def _prompt_custom(self, previous: int):
        """自定义站址：非模态弹窗，确认后才落库；取消就退回原选择。"""
        edit = LineEdit()
        edit.setPlaceholderText("https://你的镜像站/")
        edit.setText(update_check.custom_mirror())
        edit.selectAll()

        def submit() -> bool:
            text = updater.normalize_prefix(edit.text())
            if not text:
                InfoBar.warning("站址不能为空", "填一个镜像站地址，例如 https://gh-proxy.com/",
                                parent=dlg, duration=4000,
                                position=InfoBarPosition.TOP)
                return False
            update_check.set_custom_mirror(text)
            return True

        dlg = FormDialog("自定义镜像源", self.window(),
                         note="下载更新包时用这个站，站址后直接拼 GitHub 链接；"
                              "填错也不影响，失败会自动换其他镜像站。",
                         on_submit=submit)
        dlg.add_field("站址", edit)

        def _done(code: int):
            if code == QDialog.DialogCode.Accepted:
                update_check.set_mirror_mode(updater.CUSTOM)
                self._refresh_display()
            else:
                self._select_silently(previous)

        dlg.finished.connect(_done)
        dlg.show()

    # ------------------------------------------------------------ 测速

    def showEvent(self, e):
        super().showEvent(e)
        if self._probed_once:
            return
        self._probed_once = True
        # 设置页刚露头时先别抢那几帧，等界面稳定再测
        QTimer.singleShot(800, self._ensure_probe)

    def _showComboMenu(self):
        self._apply_cached_probes()
        self._ensure_probe()
        super()._showComboMenu()

    def _ensure_probe(self):
        if updater.probe_fresh():
            self._apply_cached_probes()
            return
        if self._worker is not None and self._worker.isRunning():
            return
        # 不挂父对象：卡片销毁时若线程还在跑，Qt 删子对象会直接崩；改用列表保活
        worker = update_check.MirrorProbeWorker()
        self._worker = worker
        _WORKERS.append(worker)
        worker.finished.connect(lambda w=worker: _WORKERS.remove(w) if w in _WORKERS else None)
        worker.measured.connect(self._on_measured)
        worker.unavailable.connect(self._on_unavailable)
        self._hook_quit()
        worker.start()

    def _hook_quit(self):
        global _QUIT_HOOKED
        if _QUIT_HOOKED:
            return
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(_wait_probe_on_quit)
            _QUIT_HOOKED = True

    def _apply_cached_probes(self):
        for gw in updater.GATEWAYS:
            ms, reason = updater.probe_result(gw["key"])
            if ms is not None:
                self._set_item_suffix(gw["key"], f"({ms} ms)")
            elif reason:
                self._set_item_suffix(gw["key"], f"({_fail_text(reason)})")

    def _on_measured(self, key: str, ms: int):
        self._set_item_suffix(key, f"({ms} ms)")

    def _on_unavailable(self, key: str, reason: str):
        self._set_item_suffix(key, f"({_fail_text(reason)})")

    def _set_item_suffix(self, key: str, suffix: str):
        index = self._index_of(key)
        gw = updater.gateway_by_key(key)
        if index <= 0 or gw is None:
            return
        self.setItemText(index, f"{gw['label']} {suffix}")
        if index == self.currentIndex():
            # setItemText 会把列表文案同步到按钮上，收起状态要退回不带延迟的站名
            self._refresh_display()


def _fail_text(reason: str) -> str:
    """把失败原因缩成列表里能放的三个字：限流 / 超时 / 不可用。"""
    if updater.is_rate_limited(reason):
        return "限流"
    text = (reason or "").lower()
    if "timed out" in text or "timeout" in text:
        return "超时"
    return "不可用"
