"""系统配置页：多团支部账号管理、健康检查、分学期名单、工作区自动检测。

配置持久化到 ~/.gd12355-helper/config.json：
  {"activeUser", "accounts": [...], "rosters": {"2026秋": path}, "workspaceDir", "notifyTime"}

去重规则：同账号+同密码跳过；同账号+不同密码弹窗询问覆盖/保留。
健康检查：先只读探测会话，失效才登录（避免误增密码错误次数触发验证码）。
账号表：勾选框列支持批量删除，操作列内删除按钮（按行号精确删除）。
"""

from __future__ import annotations

import os
from datetime import datetime

from PyQt5.QtCore import QThread, QTimer, pyqtSignal as Signal, Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
        QStyle,
    QStyleOptionButton,
    QTableWidgetItem,
    QWidget,
)

from concurrent.futures import ThreadPoolExecutor, as_completed

from qfluentwidgets import (
    Action,
    CheckBox,
    DropDownPushButton,
    FluentIcon as FIF,
    InfoBar,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    TableWidget,
)

from app.constants import DEFAULT_EXPECTED_PAGES
from app.ui import dialogs
from app.ui.browser_page import MAX_TABS as MAX_WEB_TABS
from app.ui.combo import ComboBox
from app.ui.page import (CONTROL_MAX_WIDTH, CONTROL_WIDTH, CallbackWorker, CollapsibleCard, FluentPage,
                          set_tip,
                          FullRow, PageBanner, SettingRow, bind_table_context_menu,
                          fit_table_columns, note_label)
from app.ui.rule_dialog import RuleDialog
from app.ui import palette as P
from app.domain.naming import Roster
from app.domain.org_rule import OrgRule, RuleError, describe_code
from app.domain.transfer import is_graduated
from app.services import notify_schedule, store
from app.domain.account_health import HEALTH_LABEL, HEALTH_ROLE

# 表格里用的短标签：完整说明（含处理办法）放 tooltip
HEALTH_SHORT = {"captcha": "需验证码", "cooldown": "登录冷却"}
from app.services.api import SiteClient

THEME_INDEX = {"auto": 0, "light": 1, "dark": 2}
MATERIAL_ORDER = ("none", "acrylic", "mica")   # 下拉框顺序，与 _material_index 一致




class SettingsHealthMixin:
    """系统配置页的健康检查进度、结果与通知汇总行为。"""

    def _on_health_buttons_back(self):
        self.health_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)

    def _show_health_progress(self):
        from app.ui.widgets import ProgressToast

        self._close_health_progress()
        self._health_progress = ProgressToast(
            "正在刷新登录状态", self.window(), on_close=self._stop_health)
        self._health_progress.set_progress(0, self._health_total)
        self._health_progress.show()

    def _stop_health(self):
        """浮条上点关闭：不再往下测，已经拿到的结果照常落盘。

        浮条自己会收起；这里只记下「用户取消了」，结束时不再补一条汇总。
        线程要等手上那批请求返回才会结束，所以别在这里等它。"""
        self._health_cancelled = True
        self._health_progress = None
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()

    def shutdown(self):
        """退出前收束后台线程：PyQt5 析构还在运行的 QThread 会直接 abort。

        stop() 只置取消位，线程等手上那批请求返回；这里最多再等 2 秒，
        没等完的交给事件循环收尾（quit 之后进程本来就要走）。"""
        for worker in (self._worker, self._ws_worker):
            if worker is not None and worker.isRunning():
                if worker is self._worker:
                    worker.stop()
                worker.wait(2000)
        about = getattr(self, "_about_card", None)
        if about is not None:
            about.shutdown()

    def _close_health_progress(self, finished: bool = False):
        toast, self._health_progress = getattr(self, "_health_progress", None), None
        if toast is None:
            return
        if finished:
            toast.finish()      # 补满 100% 后自行淡出，不在这里销毁
        else:
            toast.close()

    def _on_health_finished(self):
        cancelled = getattr(self, "_health_cancelled", False)
        self._health_cancelled = False
        self._close_health_progress(finished=not cancelled)
        self._flush_health_results()
        visible = getattr(self, "_health_visible", False)
        # 手动这几轮已经用一条汇总说清了，公告面板照常收，不再叠消息条
        self.refresh_notices(quiet=visible)
        if visible and not cancelled:
            self._report_health()

    def _report_health(self):
        """一轮测活结束给一条汇总：按原因归并，不再逐个账号弹消息条。"""
        checked = getattr(self, "_health_checked", [])
        buckets: dict[str, int] = {}
        for user in checked:
            acc = next((a for a in self.accounts if a.get("userName") == user), None)
            status = ((acc or {}).get("health") or {}).get("status") or ""
            if status in ("ok", "unknown", ""):
                continue
            label = HEALTH_LABEL.get(status, "状态异常").split("（")[0]
            buckets[label] = buckets.get(label, 0) + 1
        if not buckets:
            InfoBar.success("登录状态正常", f"{len(checked)} 个账号全部正常",
                            duration=4000, parent=self)
            return
        bad = sum(buckets.values())
        detail = "，".join(f"{k} {v}" for k, v in
                          sorted(buckets.items(), key=lambda kv: -kv[1]))
        InfoBar.warning(f"{bad} 个账号异常",
                        f"{detail}；点账号行的健康状态恢复登录",
                        duration=9000, parent=self)

    def _on_account_checked(self, user: str, status: str, message: str):
        acc = next((a for a in self.accounts if a["userName"] == user), None)
        if acc:
            acc["health"] = {
                "status": status, "message": message,
                "checkedAt": datetime.now().isoformat(timespec="seconds"),
            }
        self._health_done = getattr(self, "_health_done", 0) + 1
        if user:
            getattr(self, "_health_checked", []).append(user)
        toast = getattr(self, "_health_progress", None)
        if toast is not None:
            toast.set_progress(self._health_done, self._health_total)
        # 结果逐条回 GUI；50ms 防抖合并为一次「写盘 + 表格刷新」——
        # 逐条 _save_config 是全量 JSON 读写（~15ms × 账号数），52 账号会卡主线程 ~1s
        if getattr(self, "_health_refresh_timer", None) is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(50)
            t.timeout.connect(self._flush_health_results)
            self._health_refresh_timer = t
        self._health_refresh_timer.start()

    def _flush_health_results(self):
        self._save_config()
        self._refresh_table()

    def refresh_notices(self, quiet: bool = False):
        """把账号异常汇总成通知：每个异常账号一条，修好后自动消失。

        quiet=True 只写公告面板、不弹消息条：一轮测活已经用一条汇总说清了，
        逐账号再弹一次就变成截图里那三块叠在一起的红条。
        """
        center = getattr(getattr(self, "window_ref", None), "notice_center", None)
        if center is None:
            return
        from app.services.notices import Notice

        notices = []
        for acc in self.accounts:
            user = str(acc.get("userName") or "")
            if not user:
                continue
            status = (acc.get("health") or {}).get("status") or "unknown"
            if status in ("ok", "unknown", ""):
                continue
            label = HEALTH_LABEL.get(status, "状态异常")
            remark = str(acc.get("remark") or "").strip()
            # 只带具体原因；没有具体信息就不放空泛的操作说明（避免「太抽象」）
            msg = str((acc.get("health") or {}).get("message") or "").strip()
            detail = msg if msg and msg != label else ""
            notices.append(Notice(
                key=f"account:{user}",
                title=f"账号 {remark or user}：{label}",
                detail=detail,
                action=f"accounts:{user}",
                action_text="去设置",
                tone="error",
                # 消息条只报「哪个账号异常」，原因留给公告面板
                brief=f"{remark or user} 账号异常",
                scope="settings",   # 账号问题在系统配置处理，不出现在上传页的公告里
                silent=quiet,
            ))
        center.replace_prefix("account:", notices)

