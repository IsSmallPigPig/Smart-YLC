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




class SettingsDataMixin:
    """系统配置页的名单、支书、工作区与外观设置行为。"""

    def _refresh_roster(self):
        """合并各份名单为总表（供检查/上传使用），刷新名单管理列表。"""
        merged = Roster("combined")
        for sem in sorted(self.rosters):
            merged.rows.extend(self.rosters[sem].rows)
        self.roster = merged if merged.rows else None
        if getattr(self, "roster_row", None) is not None:
            n = len(merged.rows)
            self.roster_row.set_desc(f"已导入 {n} 名团员" if n else "尚未导入")
        self._refresh_roster_table()
        self.roster_changed.emit(self.roster)

    def _refresh_roster_table(self):
        """名单列表：一行一个学期（含空学期），便于管理多个表格。"""
        self.roster_table.blockSignals(True)
        self.roster_table.setRowCount(len(self.rosters))
        for i, sem in enumerate(sorted(self.rosters)):
            path = self._roster_paths.get(sem, "")
            for col, text in (
                (0, sem),
                (1, str(len(self.rosters[sem].rows))),
                (2, path or "（未导入文件）"),
            ):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.roster_table.setItem(i, col, item)
        self.roster_table.blockSignals(False)
        # 高度随行数收缩，不留大片空白；列宽手动计算
        self.roster_table.setFixedHeight(
            min(max(len(self.rosters), 1), 8) * 36 + 42)
        fit_table_columns(self.roster_table, stretch_last=True)

    def _delete_selected_roster(self):
        row = self.roster_table.currentRow()
        if row < 0 or row >= self.roster_table.rowCount():
            InfoBar.warning("未选中名单", "先在名单列表中点选一行", duration=3000, parent=self)
            return
        sem = self.roster_table.item(row, 0).text()
        n = len(self.rosters[sem].rows)
        dialogs.confirm(
            self.window(), "删除名单",
            f"删除「{sem}」（{n} 人）？磁盘上的 xlsx 文件不会被删除。",
            on_result=lambda ok, sem=sem: ok and self._do_delete_roster(sem))

    def _do_delete_roster(self, sem: str):
        self.rosters.pop(sem, None)
        self._roster_paths.pop(sem, None)
        self._refresh_roster()
        self._save_config()
        InfoBar.success("已删除", f"「{sem}」已从配置移除", duration=3000, parent=self)

    def _bind_roster_table_menu(self):
        def actions(row: int):
            if row < 0:
                return [Action(FIF.DOWNLOAD, "下载名单模板",
                               triggered=self._download_template)]
            return [
                Action(FIF.DOWNLOAD, "下载名单模板", triggered=self._download_template),
                Action(FIF.DELETE, "删除此名单", triggered=self._delete_selected_roster),
            ]

        bind_table_context_menu(
            self.roster_table, actions,
            select_row=lambda row: self.roster_table.setCurrentCell(row, 0))

    def _bind_secretary_table_menu(self):
        def actions(row: int):
            if row < 0:
                return [Action(FIF.DOWNLOAD, "下载支书模板",
                               triggered=self._download_secretary_template)]
            return [
                Action(FIF.DOWNLOAD, "下载支书模板",
                       triggered=self._download_secretary_template),
                Action(FIF.DELETE, "删除此支书", triggered=self._delete_selected_secretaries),
            ]

        bind_table_context_menu(
            self.sec_table, actions,
            select_row=lambda row: self.sec_table.setCurrentCell(row, 0))

    def focus_accounts(self, user: str = ""):
        """从通知跳过来：展开「支部账号」卡片，并定位到指定账号那一行。"""
        card = getattr(self, "acc_card", None)
        if card is not None:
            if not card._expanded:
                card.set_expanded(True, animate=False)
            self.ensureWidgetVisible(card)
        user = (user or "").strip()
        if not user:
            return
        row = next((i for i in range(self.table.rowCount())
                    if self.table.item(i, 1) and self.table.item(i, 1).text() == user), -1)
        if row < 0:
            return
        self.table.selectRow(row)
        item = self.table.item(row, 1)
        if item is not None:
            self.table.scrollToItem(item)
        self.ensureWidgetVisible(self.table)

    def _parse_roster(self, path: str) -> Roster:
        from app.services.excel import import_roster

        return import_roster(path)

    def _on_rosters_parsed(self, results: list):
        """启动名单后台解析完成：数据落内存后用 0ms 定时器应用——
        把表格变更移出 queued 信号处理栈（worker 信号直调中重建 items
        会在 6.12 下触发 delegate override 崩溃）。"""
        QTimer.singleShot(0, lambda: self._apply_rosters_parsed(results))

    def _apply_rosters_parsed(self, results: list):
        for sem, path, roster, err in results:
            if err:
                InfoBar.warning(f"{sem} 名单加载失败", err, duration=5000, parent=self)
                continue
            self.rosters[sem] = roster
            self._roster_paths[sem] = path
        self._refresh_roster()

    def _import_roster(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择名单", "", "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        sem = os.path.splitext(os.path.basename(path))[0]
        self.roster_btn.setEnabled(False)
        w = CallbackWorker(lambda: self._parse_roster(path), self)
        w.ok.connect(lambda roster, sem=sem, path=path: self._on_roster_imported(sem, path, roster))
        w.fail.connect(lambda msg: (InfoBar.error("名单导入失败", msg, duration=5000, parent=self),
                                    self.roster_btn.setEnabled(True)))
        self._workers.append(w)
        w.start()

    def _on_roster_imported(self, sem: str, path: str, roster: Roster):
        self.roster_btn.setEnabled(True)
        self.rosters[sem] = roster
        self._roster_paths[sem] = path
        self._refresh_roster()
        self._save_config()
        InfoBar.success(f"已导入 {sem}", f"{len(roster.rows)} 名团员", duration=3000, parent=self)

    def _download_template(self):
        from app.services.export_data import default_name

        path, _ = QFileDialog.getSaveFileName(
            self, "保存名单模板",
            default_name("团员名单模板", ".xlsx"), "Excel (*.xlsx)")
        if not path:
            return
        from app.services.excel import save_roster_template

        save_roster_template(path)
        InfoBar.success("模板已保存", path, duration=4000, parent=self)

    def _download_secretary_template(self):
        """支书名单模板：只要支部（班级）和团支书姓名两列。"""
        from app.services.export_data import default_name

        path, _ = QFileDialog.getSaveFileName(
            self, "保存支书名单模板",
            default_name("支书名单模板", ".xlsx"), "Excel (*.xlsx)")
        if not path:
            return
        from app.services.excel import save_secretary_template

        save_secretary_template(path)
        InfoBar.success("模板已保存", path, duration=4000, parent=self)

    def _pick_workspace(self):
        d = QFileDialog.getExistingDirectory(self, "选择工作区目录")
        if not d:
            return
        self.workspace_dir = d
        self._ws_touched = True
        self._ws_dirs = [d]
        self._setup_watcher(self._ws_dirs)
        self._save_config()
        # 立即扫一遍存量 PDF
        self._ws_known = {e.get("path") for e in self._pending_entries()}
        self._scan_workspace()

    def _setup_watcher(self, dirs):
        """监听所有来源目录（只监听顶层，子文件夹的变化靠上传页刷新兜住）。"""
        from PyQt5.QtCore import QFileSystemWatcher

        if isinstance(dirs, str):
            dirs = [dirs]
        dirs = [d for d in (dirs or []) if d]
        self._ws_dirs = list(dirs)
        if self._watcher is None:
            self._watcher = QFileSystemWatcher(self)
            self._watcher.directoryChanged.connect(lambda _: self._scan_timer.start())
        for p in self._watcher.directories():
            self._watcher.removePath(p)
        watching = [d for d in dirs if self._watcher.addPath(d)]
        if not watching:
            self.workspace_row.set_desc("尚未选择")
            return
        if len(watching) == 1:
            name = os.path.basename(watching[0].rstrip(os.sep)) or watching[0]
            self.workspace_row.set_desc(f"正在监听 {name}（含子文件夹），新 PDF 自动处理")
        else:
            self.workspace_row.set_desc(f"正在监听 {len(watching)} 个目录（含子文件夹），新 PDF 自动处理")

    def _pending_entries(self) -> list[dict]:
        from app.services.workspace import load_pending

        return load_pending()

    def _scan_workspace(self):
        from app.services.workspace import new_pdf_files

        dirs = list(getattr(self, "_ws_dirs", []) or [])
        if not dirs:
            d = self.workspace_dir.strip()
            dirs = [d] if d else []
        files = [path for d in dirs if os.path.isdir(d)
                 for path in new_pdf_files(d, self._ws_known)]
        if not files:
            return
        self._ws_known.update(files)
        if self._ws_worker and self._ws_worker.isRunning():
            return  # 上一批还在处理；这批等下次目录变化再扫
        self._ws_worker = WorkspaceWorker(files, self)
        self._ws_worker.file_done.connect(self._on_ws_file_done)
        self._ws_worker.start()

    def _on_ws_file_done(self, name: str, level: str, message: str, path: str):
        """工作区文件处理结果：正常情况不弹窗（避免每次启动刷一串「已加入」）；
        命名/压缩等异常才进公告面板，并给一个「去上传」入口。"""
        self.workspace_changed.emit()
        center = getattr(getattr(self, "window_ref", None), "notice_center", None)
        if center is None:
            return
        key = f"workspace:{path}"
        if level == "ok":
            center.remove(key)
            return
        from app.services.notices import Notice

        center.upsert(Notice(key=key, title=f"工作区：{name}", detail=message,
                             action="upload", action_text="去上传", tone="warning",
                             brief=f"{name} 需要处理", scope="upload"))

    def _pick_header_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择头图", "", "图片 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return
        store.update(lambda cfg: cfg.__setitem__("headerImage", path))
        self._refresh_banners(path)
        InfoBar.success("头图已设置", os.path.basename(path), duration=2500, parent=self)

    def _clear_header_image(self):
        store.update(lambda cfg: cfg.__setitem__("headerImage", ""))
        self._refresh_banners("")
        InfoBar.info("头图已清除", "首页恢复默认头图", duration=2500, parent=self)

    def _refresh_banners(self, path: str):
        """头图是全局设置：所有页面的 banner 同步刷新（首页 + 其余各页）。"""
        window = getattr(self, "window_ref", None)
        names = ("home_page", "upload_page", "org_page",
                 "org_manage_page", "messages_page", "settings_page")
        pages = [getattr(window, n, None) for n in names] if window else [self.home_page]
        for page in pages:
            banner = getattr(page, "banner", None)
            if banner is not None:
                banner.set_image_path(path)

    def _on_theme_changed(self, index: int):
        from qfluentwidgets import InfoBar

        from app.setup import apply_theme_mode

        mode = ("auto", "light", "dark")[index]
        label = "跟随系统" if index == 0 else "已生效"

        def _done():
            InfoBar.success("明暗模式已切换", label, duration=2000, parent=self)

        if self.window_ref is not None and hasattr(self.window_ref, "apply_theme_async"):
            # 交给主窗口做淡出/淡入，切换过程中界面不会定格
            self.window_ref.apply_theme_async(mode, done=_done)
            return
        apply_theme_mode(mode)
        store.update(lambda cfg: cfg.__setitem__("themeMode", mode))
        _done()

    def _sync_material_hint(self):
        """说明里写清这台机器上这一档实际会变成什么。

        Win10 没有 Mica 接口，选了会按亚克力呈现——不写出来，用户只会看到
        「切了没反应」，还得自己猜是没生效还是本来就这样。
        """
        from app.services.materials import material_hint

        index = self.material_combo.currentIndex()
        if 0 <= index < len(MATERIAL_ORDER):
            self.material_row.set_desc(material_hint(MATERIAL_ORDER[index]))

    def _on_material_changed(self, index: int):
        material = MATERIAL_ORDER[index]
        store.update(lambda cfg: cfg.__setitem__("translucentMaterial", material))
        self._sync_material_hint()
        if self.window_ref is not None:
            self.window_ref.apply_material(material)

    def _import_secretaries(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择团支书名单", "", "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        try:
            from app.services.excel import import_secretaries

            rows = import_secretaries(path)
        except Exception as e:
            InfoBar.error("团支书名单导入失败", str(e), duration=5000, parent=self)
            return
        store.update(lambda cfg: cfg.__setitem__("secretaries", rows))
        self._refresh_secretary_status(rows)
        InfoBar.success("团支书名单已导入", f"{len(rows)} 个支部", duration=4000, parent=self)
        if self.window_ref is not None:
            self.window_ref.org_manage_page.refresh()

    def _refresh_secretary_status(self, rows: list | None = None):
        """团支书名单表：一行一个支部（支部 / 团支书 / 手机号码）。"""
        rows = store.load().get("secretaries", []) if rows is None else rows
        self.sec_table.blockSignals(True)
        self.sec_table.setRowCount(len(rows))
        for i, sec in enumerate(rows):
            for col, key in enumerate(("branch", "name", "mobile")):
                item = QTableWidgetItem(str(sec.get(key, "") or ""))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.sec_table.setItem(i, col, item)
        self.sec_table.blockSignals(False)
        self.sec_table.setFixedHeight(min(max(len(rows), 1), 8) * 36 + 42)
        fit_table_columns(self.sec_table, stretch_last=True)
        self.sec_row.set_desc(f"已登记 {len(rows)} 个支部" if rows else "尚未导入")

    def _delete_selected_secretaries(self):
        """删除选中行的团支书记录（与删除所选学期名单同款交互）。"""
        rows = store.load().get("secretaries", []) or []
        row = self.sec_table.currentRow()
        if row < 0 or row >= len(rows):
            InfoBar.warning("未选中记录", "先在团支书名单表中点选一行", duration=3000, parent=self)
            return
        removed = rows.pop(row)
        store.update(lambda cfg: cfg.__setitem__("secretaries", rows))
        self._refresh_secretary_status(rows)
        InfoBar.success("已删除", f"支部 {removed.get('branch', '')} 的团支书记录已移除", duration=3000, parent=self)

    def client(self) -> SiteClient | None:
        acc = next((a for a in self.accounts if a["userName"] == self.active_user), None)
        if acc is None and self.accounts:
            acc = self.accounts[0]
        if not acc or not acc.get("password"):
            return None
        if self._client and self._client.user_name == acc["userName"] and self._client.password == acc["password"]:
            return self._client
        self._client = SiteClient(acc["userName"], acc["password"])
        return self._client

    def scope_oid(self) -> str:
        return ""



class WorkspaceWorker(QThread):
    """处理工作区新 PDF：命名检查（可解析则改名）→ 压缩 → 入待上传列表。"""

    file_done = Signal(str, str, str, str)  # name, level(ok/warn), message, path

    def __init__(self, files: list[str], parent=None):
        super().__init__(parent)
        self.files = files

    def run(self):
        from app.services.workspace import add_pending, prepare_pdf

        for p in self.files:
            try:
                info = prepare_pdf(p)
                add_pending(info["path"], info.get("message", ""))
                name = info["name"]
                msgs = []
                if info.get("renamed_from"):
                    msgs.append(f"原名 {info['renamed_from']}，已改为 {name}")
                if info.get("compressed"):
                    msgs.append(info["message"])
                if info.get("problem"):
                    self.file_done.emit(info.get("renamed_from") or name, "warn", f"命名不合规：{info['problem']}；已加入待上传列表", info["path"])
                else:
                    self.file_done.emit(name, "ok", "；".join(msgs) or "命名检查通过，已加入待上传列表", info["path"])
            except Exception as e:
                self.file_done.emit(os.path.basename(p), "warn", f"处理失败：{e}", p)
