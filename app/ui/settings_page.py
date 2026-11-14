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

from PySide6.QtCore import QThread, QTimer, Signal, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QTableWidgetItem,
    QWidget,
)

from concurrent.futures import ThreadPoolExecutor, as_completed

from qfluentwidgets import (
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    TableWidget,
    ToolButton,
)

from app.ui import dialogs
from app.ui.browser_page import MAX_TABS as MAX_WEB_TABS
from app.ui.page import (CONTROL_MAX_WIDTH, CallbackWorker, CollapsibleCard, FluentPage,
                          set_tip,
                          FullRow, PageHeader, SettingRow, fit_table_columns,
                          note_label)
from app.ui.rule_dialog import RuleDialog
from app.ui import palette as P
from app.domain.naming import Roster
from app.domain.org_rule import OrgRule, RuleError, describe_code
from app.domain.transfer import is_graduated
from app.services import notify_schedule, store
from app.domain.account_health import HEALTH_COLOR, HEALTH_LABEL

# 表格里用的短标签：完整说明（含处理办法）放 tooltip
HEALTH_SHORT = {"captcha": "需验证码", "cooldown": "登录冷却"}
from app.services.api import SiteClient

THEME_INDEX = {"auto": 0, "light": 1, "dark": 2}


class SelectAllHeader(QHeaderView):
    """账号表表头：第 0 列居中放全选勾选框，位置随列宽与横向滚动更新。"""

    def __init__(self, table):
        super().__init__(Qt.Orientation.Horizontal, table)
        self.box = CheckBox(self)
        self.box.setTristate(True)
        self.box.setFixedSize(22, 22)
        set_tip(self.box, "全选")
        self.setHighlightSections(False)
        for sig in (self.sectionResized, self.sectionMoved, self.geometriesChanged):
            sig.connect(self._place_box)
        table.horizontalScrollBar().valueChanged.connect(self._place_box)

    def _place_box(self, *_):
        # 行内勾选框由 fluent delegate 固定在单元格 x+15；表头对齐同一条竖线
        opt = QStyleOptionButton()
        opt.initFrom(self.box)
        dx = self.box.style().subElementRect(QStyle.SE_CheckBoxIndicator, opt, self.box).left()
        x = self.sectionViewportPosition(0) + 15 - dx
        self.box.move(x, (self.height() - self.box.height()) // 2)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_box()

    def showEvent(self, e):
        super().showEvent(e)
        self._place_box()


class HealthWorker(QThread):
    """并发测活：恢复已保存会话 → 只读探测，探测有效不登录；冷却中的账号
    （验证码/风控）本轮不尝试登录，只回报冷却状态。"""

    account_checked = Signal(str, str, str)  # userName, status, message

    # 登录闸门的 kind → 测活展示状态
    KIND_MAP = {
        "ok": "ok", "failed": "failed", "captcha": "captcha",
        "passwordChange": "passwordChange", "needMemberName": "needMemberName",
        "risk": "cooldown", "cooldown": "cooldown", "network": "network",
    }

    def __init__(self, accounts: list[dict]):
        super().__init__()
        self.accounts = accounts

    def _check_one(self, acc: dict) -> tuple[str, str, str]:
        from app.services import login_gate

        user, pwd = acc.get("userName", ""), acc.get("password", "")
        if not user or not pwd:
            return user, "unknown", "账号或密码为空"
        try:
            kind, message = login_gate.ensure_logged_in(SiteClient(user, pwd))
            return user, self.KIND_MAP.get(kind, "failed"), message
        except Exception as e:
            return user, "network", f"测活线程异常：{e}"

    def run(self):
        workers = min(8, max(1, len(self.accounts)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._check_one, dict(a)) for a in self.accounts]
            for fut in as_completed(futures):
                try:
                    self.account_checked.emit(*fut.result())
                except Exception as e:  # 单账号线程异常不影响其余结果
                    self.account_checked.emit("", "network", f"测活线程异常：{e}")


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
                    msgs.append(f"已改名：{info['renamed_from']} → {name}")
                if info.get("compressed"):
                    msgs.append(info["message"])
                if info.get("problem"):
                    self.file_done.emit(info.get("renamed_from") or name, "warn", f"命名不合规：{info['problem']}；已加入待上传列表", info["path"])
                else:
                    self.file_done.emit(name, "ok", "；".join(msgs) or "命名检查通过，已加入待上传列表", info["path"])
            except Exception as e:
                self.file_done.emit(os.path.basename(p), "warn", f"处理失败：{e}", p)


class SettingsPage(FluentPage):
    roster_changed = Signal(object)
    workspace_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page-settings")
        # 系统配置内容整体上移一点：顶部边距从 24 收到 14，卡片更贴近标题栏
        self.vBoxLayout.setContentsMargins(32, 14, 32, 24)
        self.rosters: dict[str, Roster] = {}  # 学期 → 名单
        self._roster_paths: dict[str, str] = {}  # 学期 → xlsx 路径（持久化）
        self.roster: Roster | None = None  # 全部学期合并（供检查/上传用）
        self.accounts: list[dict] = []
        self.active_user = ""
        self._client: SiteClient | None = None
        self._worker: HealthWorker | None = None
        self._ws_worker: WorkspaceWorker | None = None
        self._workers: list[CallbackWorker] = []  # 后台解析 worker 引用，防 GC
        self._ws_known: set[str] = set()
        self.home_page = None  # 由 MainWindow 注入，头图设置后即时生效
        self.window_ref = None  # 由 MainWindow 注入，毛玻璃开关生效用
        self._rule = OrgRule()  # 学校自定义编号规则，_load_config 时从配置恢复

        lay = self.vBoxLayout
        self.header = PageHeader("系统配置", "账号、名单与编号规则、外观、通知与更新")
        lay.addWidget(self.header)

        def group(title: str, expanded: bool) -> CollapsibleCard:
            card = CollapsibleCard(title, expanded=expanded)
            lay.addWidget(card)
            return card

        def row(card: CollapsibleCard, widget, *, last: bool = False):
            widget.set_separator(not last)
            card.body_lay.addWidget(widget)

        def controls(*widgets, spacing: int = 8) -> QWidget:
            """一行里的控件按内容取宽，整组不超过六字按钮的宽度。

            超出的部分不再硬撑这一列，而是由调用方把次要项收进齿轮弹窗。"""
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(spacing)
            for widget in widgets:
                widget.setMinimumWidth(0)
                widget.setMaximumWidth(16777215)  # 解掉单控件上的 setFixedWidth
                h.addWidget(widget, 0)
            box.setMaximumWidth(CONTROL_MAX_WIDTH)
            return box

        # ── 支部账号 ──
        acc_card = group("支部账号", True)
        self.active_combo = ComboBox()
        self.active_combo.currentTextChanged.connect(self._on_active_changed)
        self.web_btn = ToolButton(FIF.GLOBE)
        set_tip(self.web_btn, "在独立窗口打开所选账号的网页端，标签之间互不顶号")
        self.web_btn.clicked.connect(self._open_web)
        row(acc_card, SettingRow(
            "当前账号", desc="上传与网页端默认使用的账号；打开网页端会弹出独立窗口",
            icon=FIF.PEOPLE, control=controls(self.active_combo, self.web_btn)))

        self.health_btn = ToolButton(FIF.HEART)
        set_tip(self.health_btn, "全部测活：查一遍所有账号的登录状态")
        self.health_btn.clicked.connect(lambda: self.start_health_check(force=True))
        self.refresh_all_btn = PrimaryPushButton(FIF.CLOUD_DOWNLOAD, "同步数据")
        set_tip(self.refresh_all_btn, "依次刷新成员缓存、站内信、组织状况")
        self.refresh_all_btn.clicked.connect(self._refresh_all_clicked)
        row(acc_card, SettingRow("账号测活", desc="测活查登录状态，同步数据拉成员与站内信",
                                 icon=FIF.SYNC,
                                 control=controls(self.refresh_all_btn, self.health_btn)))

        self.del_checked_btn = ToolButton(FIF.DELETE)
        set_tip(self.del_checked_btn, "删除勾选")
        self.del_checked_btn.clicked.connect(self._delete_checked)
        self.refresh_btn = ToolButton(FIF.UPDATE)
        set_tip(self.refresh_btn, "刷新所选账号")
        self.refresh_btn.clicked.connect(self._refresh_selected)

        # 账号表：勾选框 + 账号 + 密码 + 备注 + 届别 + 状态 + 健康状态 + 最近检查
        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(10)  # 与卡片、窗口圆角同心
        self.table.setColumnCount(8)
        self._select_all_header = SelectAllHeader(self.table)
        self.table.setHorizontalHeader(self._select_all_header)
        self._select_all_header.box.clicked.connect(self._on_select_all_clicked)
        self.table.setHorizontalHeaderLabels(["", "账号", "密码", "备注", "届别", "状态", "健康状态", "最近检查"])
        self.table.setColumnWidth(0, 46)
        self.table.setMinimumHeight(180)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.ResizeMode.Fixed)
        header.setSectionResizeMode(3, header.ResizeMode.Stretch)  # 备注吃掉多余宽度
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._sync_active_from_selection)
        self.table.itemDelegate().closeEditor.connect(self._on_editor_closed)
        # 健康状态异常时点这一格直接进内嵌浏览器手动登录；双击整行打开该账号
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        table_block = FullRow()
        tools = QHBoxLayout()
        tools.addWidget(self.del_checked_btn)
        tools.addWidget(self.refresh_btn)
        tools.addStretch(1)
        table_block.lay.addLayout(tools)
        table_block.lay.addWidget(self.table)
        row(acc_card, table_block)

        # 一行放不下四个输入框，改成「添加账号」按钮打开表单弹窗
        self.add_open_btn = PushButton(FIF.ADD, "添加账号")
        set_tip(self.add_open_btn, "填账号与密码，确定后立刻测活")
        self.add_open_btn.clicked.connect(self._open_add_account)
        row(acc_card, SettingRow("添加账号", desc="账号与密码都填好再点添加",
                                 icon=FIF.DICTIONARY_ADD,
                                 control=controls(self.add_open_btn)))

        self.import_btn = PushButton(FIF.DOCUMENT, "批量导入")
        self.import_btn.clicked.connect(self._import_accounts)
        self.tpl_btn = PushButton(FIF.DOWNLOAD, "账号模板")
        set_tip(self.tpl_btn, "下载账号导入模板")
        self.tpl_btn.clicked.connect(self._download_account_template)
        self.export_cfg_btn = PushButton(FIF.SHARE, "导出配置")
        set_tip(self.export_cfg_btn, "导出系统配置 JSON")
        self.export_cfg_btn.clicked.connect(self._export_config_json)
        self.file_btn = PushButton(FIF.LIBRARY, "导入导出")
        set_tip(self.file_btn, "批量导入账号、下载模板、导出配置")
        self.file_btn.clicked.connect(self._open_file_dialog)
        row(acc_card, SettingRow("账号文件", desc="导入或导出配置，便于换机迁移", icon=FIF.LIBRARY,
                                 control=controls(self.file_btn)),
            last=True)

        # ── 名单导入 ──
        ros_card = group("名单导入", True)
        self.roster_btn = PrimaryPushButton("导入名单")
        self.roster_btn.clicked.connect(self._import_roster)
        self.template_btn = ToolButton(FIF.DOWNLOAD)
        set_tip(self.template_btn, "下载名单模板")
        self.template_btn.clicked.connect(self._download_template)
        self.roster_del_btn = ToolButton(FIF.DELETE)
        set_tip(self.roster_del_btn, "删除选中的名单")
        self.roster_del_btn.clicked.connect(self._delete_selected_roster)
        self.roster_row = SettingRow("团员名单", desc="导入姓名与班级，供检查与上传匹配",
                                     icon=FIF.DICTIONARY,
                                     control=controls(self.roster_btn, self.template_btn,
                                                       self.roster_del_btn))
        row(ros_card, self.roster_row)

        self.roster_table = TableWidget()
        self.roster_table.setColumnCount(3)
        self.roster_table.setBorderRadius(10)  # 与卡片、窗口圆角同心
        self.roster_table.setHorizontalHeaderLabels(["名单", "人数", "名单文件"])
        self.roster_table.setEditTriggers(self.roster_table.EditTrigger.NoEditTriggers)
        header = self.roster_table.horizontalHeader()
        header.setSectionResizeMode(2, header.ResizeMode.Stretch)
        self.roster_table.setColumnWidth(2, 320)
        roster_block = FullRow()
        roster_block.lay.addWidget(self.roster_table)
        row(ros_card, roster_block)

        self.sec_btn = PrimaryPushButton("导入支书")
        self.sec_btn.clicked.connect(self._import_secretaries)
        self.sec_tpl_btn = ToolButton(FIF.DOWNLOAD)
        set_tip(self.sec_tpl_btn, "下载支书名单模板（只需班级和姓名）")
        self.sec_tpl_btn.clicked.connect(self._download_secretary_template)
        self.sec_del_btn = ToolButton(FIF.DELETE)
        set_tip(self.sec_del_btn, "删除选中的支书")
        self.sec_del_btn.clicked.connect(self._delete_selected_secretaries)
        self.sec_row = SettingRow("支书名单", desc="支部与团支书姓名，用于核对与通知",
                                  icon=FIF.CERTIFICATE,
                                  control=controls(self.sec_btn, self.sec_tpl_btn,
                                                   self.sec_del_btn))
        row(ros_card, self.sec_row)
        self.sec_table = TableWidget()
        self.sec_table.setColumnCount(3)
        self.sec_table.setBorderRadius(10)  # 与卡片、窗口圆角同心
        self.sec_table.setHorizontalHeaderLabels(["支部", "团支书", "手机号码"])
        self.sec_table.setEditTriggers(self.sec_table.EditTrigger.NoEditTriggers)
        sec_block = FullRow()
        sec_block.lay.addWidget(self.sec_table)
        row(ros_card, sec_block, last=True)

        # ── 编号规则 ──
        rule_card = group("编号规则", False)
        self.rule_btn = PushButton(FIF.EDIT, "编辑规则")
        self.rule_btn.clicked.connect(self._open_rule_dialog)
        self.rule_row = SettingRow("编号规则", desc="从账号里识别学部、年级与班级",
                                   icon=FIF.EDUCATION, control=controls(self.rule_btn))
        row(rule_card, self.rule_row)
        self.expected_pages_edit = LineEdit()
        self.expected_pages_edit.setPlaceholderText("如 4")
        self.expected_pages_edit.editingFinished.connect(self._save_config)
        row(rule_card, SettingRow("预期页数", desc="入团志愿书的页数，留空则不核对",
                                  icon=FIF.FIT_PAGE,
                                  control=controls(self.expected_pages_edit),
                                  separator=False))

        # ── 外观设置 ──
        look_card = group("外观设置", False)
        self.theme_combo = ComboBox()
        for label in ("跟随系统", "浅色模式", "暗色模式"):
            self.theme_combo.addItem(label)
        self.theme_combo.setCurrentIndex(THEME_INDEX.get(store.load().get("themeMode", "auto"), 0))
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        row(look_card, SettingRow("明暗模式", desc="跟随系统，或固定浅色与暗色",
                                  icon=FIF.BRIGHTNESS, control=controls(self.theme_combo)))
        self.material_combo = ComboBox()
        # 只提供 fluent widgets 官方的两种材质：Acrylic 毛玻璃 与 Mica
        for label in ("关闭", "Acrylic 毛玻璃", "Mica"):
            self.material_combo.addItem(label)
        _cfg = store.load()
        from app.services.materials import normalize

        _material = normalize(_cfg.get("translucentMaterial")
                              or ("acrylic" if _cfg.get("translucent") else "none"))
        self.material_combo.setCurrentIndex({"none": 0, "acrylic": 1, "mica": 2}.get(_material, 0))
        self.material_combo.currentIndexChanged.connect(self._on_material_changed)
        row(look_card, SettingRow("窗口背景", desc="Acrylic 与 Mica：Mica 在 Windows 11 生效",
                                  icon=FIF.TILES, control=controls(self.material_combo)))
        self.header_pick_btn = PushButton(FIF.PHOTO, "选择图片")
        self.header_pick_btn.clicked.connect(self._pick_header_image)
        self.header_clear_btn = ToolButton(FIF.DELETE)
        set_tip(self.header_clear_btn, "清除头图，恢复默认")
        self.header_clear_btn.clicked.connect(self._clear_header_image)
        row(look_card, SettingRow("首页头图", desc="图片铺满首页顶部并向下渐隐",
                                  icon=FIF.PHOTO,
                                  control=controls(self.header_pick_btn, self.header_clear_btn),
                                  ), last=True)

        # ── 工作配置 ──
        ws_card = group("工作配置", False)
        self.ws_edit = LineEdit()
        self.ws_edit.setPlaceholderText("选择工作目录")
        self.ws_btn = PushButton(FIF.FOLDER, "选择目录")
        self.ws_btn.clicked.connect(self._pick_workspace)
        self.workspace_row = SettingRow(
            "工作目录", desc="未选择目录", icon=FIF.FOLDER,
            control=controls(self.ws_btn))
        row(ws_card, self.workspace_row)

        self.notify_mode = ComboBox()
        for key in notify_schedule.MODES:
            self.notify_mode.addItem(notify_schedule.MODE_LABELS[key], userData=key)
        self.notify_time_edit = LineEdit()
        self.notify_time_edit.setPlaceholderText("08:00")
        self.notify_time_edit.setClearButtonEnabled(False)
        self.notify_time_edit.editingFinished.connect(self._save_config)
        self.notify_interval = ComboBox()
        for hours in notify_schedule.INTERVAL_CHOICES:
            self.notify_interval.addItem(f"每 {hours} 小时", userData=hours)
        self.notify_more_btn = ToolButton(FIF.SETTING)
        set_tip(self.notify_more_btn, "发送时间、间隔与手动测试")
        self.notify_more_btn.clicked.connect(self._open_notify_dialog)
        self.notify_row = SettingRow(
            "通知频率", icon=FIF.DATE_TIME,
            control=controls(self.notify_mode, self.notify_more_btn, spacing=6))
        row(ws_card, self.notify_row)
        self.notify_mode.currentIndexChanged.connect(self._on_notify_mode_changed)
        self.notify_interval.currentIndexChanged.connect(self._on_notify_mode_changed)

        # 未开启时普通按钮「开启自启」，已开启时高亮按钮「关闭自启」，状态一眼可见
        self.autostart_btn = PushButton()
        self.autostart_btn.clicked.connect(self._toggle_autostart)
        self.autostart_on_btn = PrimaryPushButton()
        self.autostart_on_btn.clicked.connect(self._toggle_autostart)
        self.autostart_box = QWidget()
        auto_lay = QHBoxLayout(self.autostart_box)
        auto_lay.setContentsMargins(0, 0, 0, 0)
        auto_lay.setSpacing(8)
        auto_lay.addWidget(self.autostart_btn)
        auto_lay.addWidget(self.autostart_on_btn)
        row(ws_card, SettingRow("开机自启", desc="登录后自动在托盘后台运行",
                                icon=FIF.POWER_BUTTON,
                                control=controls(self.autostart_box),
                                separator=False))

        from app.ui.about_card import AboutCard

        lay.addWidget(AboutCard(self))

        self._watcher = None
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(800)
        self._scan_timer.timeout.connect(self._scan_workspace)

        self._load_config()
        self._refresh_table()

        # 刷新登录状态：每 15 分钟轮询一次到期账号，各账号 4 小时内不重复刷新
        self._timer = QTimer(self)
        self._timer.setInterval(15 * 60 * 1000)
        self._timer.timeout.connect(self.start_health_check)
        if self.accounts:
            self._timer.start()
            self.start_health_check()

    # ---------- 配置持久化 ----------

    def _repair_remarks(self, previous: OrgRule | None = None) -> int:
        """补上为空或误填成账号号的备注；换规则时同步旧规则自动生成的备注。

        手填备注一律保留；新规则解析不出时保留原备注，不丢已有信息。"""
        fixed = 0
        for acc in self.accounts:
            user = acc.get("userName", "")
            remark = (acc.get("remark") or "").strip()
            if remark and remark != user:
                if previous is None or remark != describe_code(user, previous):
                    continue  # 不是旧规则自动生成的
            label = describe_code(user, self._rule)
            if label and label != remark:
                acc["remark"] = label
                fixed += 1
        if fixed:
            store.update(lambda c, accs=self.accounts: c.__setitem__("accounts", accs))
        return fixed

    def _load_config(self):
        cfg = store.load()
        try:
            self._rule = OrgRule.from_config(cfg)
        except RuleError as e:
            self._rule = OrgRule()
            InfoBar.error("编号规则无法读取", str(e), duration=10000, parent=self)
        self.accounts = list(cfg.get("accounts") or [])
        self._repair_remarks()
        self.active_user = cfg.get("activeUser") or (self.accounts[0]["userName"] if self.accounts else "")
        self.notify_time_edit.setText(store.notify_time(cfg))
        kind = notify_schedule.mode(cfg)
        self.notify_mode.setCurrentIndex(max(0, self.notify_mode.findData(kind)))
        hours = notify_schedule.interval_hours(cfg)
        idx_h = self.notify_interval.findData(hours)
        if idx_h < 0:
            self.notify_interval.addItem(f"每 {hours} 小时", userData=hours)
            idx_h = self.notify_interval.count() - 1
        self.notify_interval.setCurrentIndex(idx_h)
        self._sync_notify_controls()
        self._refresh_autostart_btn()

        # 名单：分学期（只新建未导入的学期没有文件路径，也要保留）。
        # 所有学期立即占位（下拉/名单表即时完整），xlsx 解析放后台线程，完成后替换数据
        self.rosters = {}
        self._roster_paths = {}
        semesters = dict(cfg.get("rosters") or {})
        if cfg.get("rosterPath") and not semesters:
            semesters["名单"] = cfg["rosterPath"]  # 旧版单文件迁移
        pending = []
        for sem, path in semesters.items():
            self.rosters[sem] = Roster(sem)
            self._roster_paths[sem] = path or ""
            if path and os.path.isfile(path):
                pending.append((sem, path))
        if pending:
            def _parse_all():
                out = []
                for sem, path in pending:
                    try:
                        out.append((sem, path, self._parse_roster(path), None))
                    except ValueError as e:
                        out.append((sem, path, None, str(e)))
                return out

            w = CallbackWorker(_parse_all, self)
            w.ok.connect(self._on_rosters_parsed)
            w.fail.connect(lambda msg: InfoBar.error("名单加载失败", msg, duration=5000, parent=self))
            self._workers.append(w)
            w.start()
        self._refresh_roster()

        # 工作区
        ws_dir = cfg.get("workspaceDir", "")
        self.ws_edit.setText(ws_dir)
        if ws_dir and os.path.isdir(ws_dir):
            self._setup_watcher(ws_dir)
        elif not ws_dir:
            self.workspace_row.set_desc("先选择工作目录，里面的 PDF 会自动加入待处理")

        # 文档检查：预期页数
        pages = cfg.get("expectedPages", "")
        self.expected_pages_edit.setText(str(pages) if pages else "")

        # 团支部编号规则
        self._update_rule_preview()

        # 团支书名单状态
        self._refresh_secretary_status()

    # ---------- 团支部编号规则 ----------

    def org_rule(self) -> OrgRule:
        return self._rule

    def _update_rule_preview(self):
        count = len(self.org_rule().effective_patterns())
        self.rule_row.set_desc(f"从账号里识别学部、年级与班级，当前 {count} 条规则")

    def _open_rule_dialog(self):
        dlg = RuleDialog(self.org_rule(), self.window())
        dlg.rule_saved.connect(self._on_rule_saved)
        dlg.show()

    def _on_rule_saved(self, rule: OrgRule):
        previous, self._rule = self._rule, rule
        if self._repair_remarks(previous):
            self._refresh_table()
        self._save_config()
        self._update_rule_preview()

    def expected_pages(self) -> int:
        """文档检查的预期页数；未填或非法时返回 0（不核对页数）。"""
        t = self.expected_pages_edit.text().strip()
        try:
            return max(0, int(t))
        except ValueError:
            return 0

    def _save_config(self):
        def _write(cfg: dict):
            cfg.update({
                "activeUser": self.active_user,
                "accounts": self.accounts,
                "rosters": dict(self._roster_paths),
                "workspaceDir": self.ws_edit.text().strip(),
                "notifyTime": self.notify_time_edit.text().strip() or store.DEFAULT_NOTIFY_TIME,
                "notifyMode": self._notify_mode(),
                "notifyInterval": self.notify_interval.currentData()
                or notify_schedule.DEFAULT_INTERVAL_HOURS,
                "expectedPages": self.expected_pages(),
                "orgRule": self.org_rule().to_config(),
            })

        # 拿最新配置再改：工作线程刚写入的 memberCache/messagesCache 不会被旧快照覆盖
        store.update(_write)
        self._client = None

    def _refresh_roster(self):
        """合并各份名单为总表（供检查/上传使用），刷新名单管理列表。"""
        merged = Roster("combined")
        for sem in sorted(self.rosters):
            merged.rows.extend(self.rosters[sem].rows)
        self.roster = merged if merged.rows else None
        if getattr(self, "roster_row", None) is not None:
            n = len(merged.rows)
            self.roster_row.set_desc(
                f"已导入 {n} 名团员，导入姓名与班级供检查与上传匹配" if n
                else "导入姓名与班级，供检查与上传匹配")
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

    # ---------- 自启动 / 通知 ----------

    def _notify_mode(self) -> str:
        return self.notify_mode.currentData() or notify_schedule.DEFAULT_MODE

    def _notify_cfg(self) -> dict:
        return {
            "notifyMode": self._notify_mode(),
            "notifyTime": self.notify_time_edit.text().strip() or store.DEFAULT_NOTIFY_TIME,
            "notifyInterval": self.notify_interval.currentData()
            or notify_schedule.DEFAULT_INTERVAL_HOURS,
        }

    def _sync_notify_controls(self):
        kind = self._notify_mode()
        self.notify_time_edit.setVisible(kind == "daily")
        self.notify_interval.setVisible(kind == "interval")
        self.notify_row.set_desc(notify_schedule.describe(self._notify_cfg()))

    def _on_notify_mode_changed(self, *_):
        self._sync_notify_controls()
        self._save_config()

    def _test_notify(self, btn=None):
        """手动发一条测试通知：osascript 不占 GUI 线程。

        btn 为点击来源按钮（弹窗每次新建），发送期间置灰作反馈；不传则只发不置灰。"""
        def _send():
            from app.services.notify import send

            return send("团员情况（测试）", "通知通道正常，这是一条测试消息。")

        def _done(ok: bool):
            if btn is not None:
                btn.setEnabled(True)
            if ok:
                InfoBar.success("测试通知已发送", "没看到弹窗时检查系统通知权限",
                                duration=4000, parent=self)
            else:
                InfoBar.warning("测试通知未送达", "当前平台不支持或系统拒绝了通知",
                                duration=5000, parent=self)

        if btn is not None:
            btn.setEnabled(False)
        worker = CallbackWorker(_send, self)
        worker.ok.connect(_done)
        worker.fail.connect(lambda msg: (
            btn.setEnabled(True) if btn is not None else None,
            InfoBar.error("测试通知失败", msg, duration=5000, parent=self)))
        self._workers.append(worker)
        worker.start()

    def _refresh_autostart_btn(self):
        from app.services import autostart

        on = autostart.is_installed()
        self.autostart_btn.setText("开启自启")
        self.autostart_on_btn.setText("关闭自启")
        self.autostart_btn.setVisible(not on)
        self.autostart_on_btn.setVisible(on)

    def _toggle_autostart(self):
        from app.services import autostart

        if autostart.is_installed():
            autostart.uninstall()
            InfoBar.info("已关闭自启动", "后台托盘不再开机运行", duration=3000, parent=self)
        else:
            if not self.accounts:
                InfoBar.warning("先配置账号", "先添加团支部账号", duration=4000, parent=self)
                return
            path = autostart.install()
            InfoBar.success("已开启自启动", f"LaunchAgent：{path}", duration=4000, parent=self)
        self._refresh_autostart_btn()

    # ---------- 账号增删 / 去重 / 冲突 ----------

    def _import_accounts(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择账号文件", "", "表格或文本 (*.xlsx *.xlsm *.txt *.csv)")
        if not path:
            return
        # xlsx 解析放后台线程，大文件不卡界面
        self.import_btn.setEnabled(False)
        w = CallbackWorker(lambda: self._read_account_file(path), self)
        w.ok.connect(self._on_account_file_read)
        w.fail.connect(lambda msg: (InfoBar.error("读取失败", msg, duration=5000, parent=self),
                                    self.import_btn.setEnabled(True)))
        self._workers.append(w)
        w.start()

    def _on_account_file_read(self, rows: list):
        self.import_btn.setEnabled(True)
        if not rows:
            InfoBar.warning("没有读到账号",
                            "账号与密码两列，备注和届别可空",
                            duration=4000, parent=self)
            return
        # 异步冲突链：遇到密码冲突弹非模态确认，选完回调继续处理队列
        self._import_ctx = {
            "queue": list(rows),
            "stat": {"added": 0, "dedup": 0, "overwritten": 0, "kept": 0},
            "prefilled": 0,
            "bad_years": [],
        }
        self._import_step()

    def _import_step(self):
        ctx = self._import_ctx
        rule = self.org_rule()
        while ctx["queue"]:
            account, pwd, remark, year = ctx["queue"].pop(0)
            if not remark or remark.strip() == account:
                # 备注留空或误填成账号号 → 按编号规则预填（如 ssfz12601 → 普通高中高一一班）
                remark = describe_code(account, rule)
                ctx["prefilled"] += 1 if remark else 0
            user = account.strip()
            existing = next((a for a in self.accounts if a["userName"] == user), None)
            if existing is None:
                self._apply_import_row("added", user, pwd, remark, year)
                continue
            if existing["password"] == pwd:
                self._apply_import_row("dedup", user, pwd, remark, year)
                continue
            # 密码冲突：非模态弹窗，用户选择后回调继续队列
            dialogs.confirm(
                self.window(), "账号密码冲突",
                f"账号 {user} 已存在，但密码与现存的（备注：{existing.get('remark') or '无'}）不一致。\n"
                "覆盖会替换密码，保留原密码会跳过这一条",
                yes="覆盖", cancel="保留原密码",
                on_result=lambda ov, u=user, p=pwd, r=remark, y=year:
                    self._on_conflict_resolved("overwritten" if ov else "kept", u, p, r, y),
            )
            return
        self._finish_import()

    def _on_conflict_resolved(self, decision, user, pwd, remark, year):
        self._apply_import_row(decision, user, pwd, remark, year)
        self._import_step()

    def _apply_import_row(self, decision, user, pwd, remark, year):
        stat = self._import_ctx["stat"]
        existing = next((a for a in self.accounts if a["userName"] == user), None)
        if decision == "added":
            self.accounts.append({
                "userName": user, "password": pwd, "remark": remark,
                "health": {"status": "unknown", "message": "", "checkedAt": ""},
            })
        elif decision == "overwritten":
            existing["password"] = pwd
            existing["remark"] = remark or existing.get("remark", "")
            existing["health"] = {"status": "unknown", "message": "", "checkedAt": ""}
        stat[decision] += 1
        if year:
            from app.domain.transfer import parse_graduate_year

            if parse_graduate_year(year) is None:
                self._import_ctx["bad_years"].append(user)
                return
            target = existing or next(a for a in self.accounts if a["userName"] == user)
            target["graduateYear"] = year

    def _finish_import(self):
        ctx = self._import_ctx
        self._after_accounts_changed()
        msg = (f"新增 {ctx['stat']['added']}，去重跳过 {ctx['stat']['dedup']}，"
               f"覆盖 {ctx['stat']['overwritten']}，冲突保留 {ctx['stat']['kept']}")
        if ctx["prefilled"]:
            msg += f"；按编号规则预填备注 {ctx['prefilled']} 个"
        if ctx["bad_years"]:
            msg += f"；届别无效未设置：{'、'.join(ctx['bad_years'][:5])}"
        InfoBar.success("导入完成", msg, duration=5000, parent=self)
        self._import_ctx = None

    def _refresh_all_clicked(self):
        if self.window_ref is None:
            return
        self.window_ref.refresh_all(silent=False)

    def _after_accounts_changed(self):
        self._save_config()
        self._refresh_table()
        self.start_health_check(force=True)  # 新增账号要立刻定状态，不受 4 小时节流
        # 新账号进入后自动刷新所有数据（成员缓存、站内信、组织状况）
        if self.window_ref is not None:
            self.window_ref.refresh_all(silent=True)

    # ---------- 齿轮弹窗：一行放不下的次要项 ----------

    def _config_dialog(self, title: str, note: str = "", yes_text: str = "完成"):
        from app.ui.dialogs import ConfigDialog

        return ConfigDialog(title, self.window(), note, yes_text=yes_text)

    def _open_add_account(self):
        """添加账号：四个输入框放进弹窗，行上只留一个四字按钮。

        输入框每次打开都新建并挂在弹窗自身之下（dialog.user_edit 等），
        关窗时随弹窗一起销毁——不复用设置页上的持久控件，避免两次打开弹窗
        时旧控件被反复 reparent 导致的渲染错位/穿透。"""
        from app.ui.dialogs import FormDialog
        from qfluentwidgets import LineEdit

        dialog = FormDialog(
            "添加账号", self.window(),
            "账号与密码都填好再点确认；备注留空按编号规则推断",
            on_submit=lambda: self._add_manual(
                user=dialog.user_edit.text().strip(),
                pwd=dialog.pwd_edit.text(),
                remark=dialog.remark_edit.text().strip(),
            ),
        )
        dialog.body.setSpacing(14)  # 四个输入框之间留出呼吸，不要挤成一块
        dialog.user_edit = LineEdit(dialog)
        dialog.user_edit.setPlaceholderText("团支部账号，如 ssfz12407")
        dialog.pwd_edit = LineEdit(dialog)
        dialog.pwd_edit.setPlaceholderText("登录密码")
        dialog.remark_edit = LineEdit(dialog)
        dialog.remark_edit.setPlaceholderText("备注，留空按编号规则推断")
        for label, widget in (("团支部账号，如 ssfz12407", dialog.user_edit),
                              ("登录密码", dialog.pwd_edit),
                              ("备注，留空按编号规则推断", dialog.remark_edit)):
            widget.setMinimumHeight(36)
            dialog.add_field(label, widget)
        self._add_dialog = dialog
        dialog.show()
        dialog.user_edit.setFocus()

    def _open_file_dialog(self):
        """账号文件：导入、模板、导出三个命令收在一个入口里。"""
        dialog = self._config_dialog("账号文件", "导入或导出配置，便于换机迁移")
        for widget in (self.import_btn, self.tpl_btn, self.export_cfg_btn):
            dialog.add_row(widget)
        dialog.show()

    def _open_notify_dialog(self):
        """通知频率：发送时间、间隔与手动测试。

        时间框与间隔框每次打开都新建并挂在弹窗下，关窗前写回持久属性
        （_save_config 读的是这两个属性），避免两次打开复用控件导致的渲染异常。"""
        from PySide6.QtWidgets import QVBoxLayout, QWidget

        from qfluentwidgets import ComboBox, LineEdit, PushButton

        from app.ui.page import note_label

        dialog = self._config_dialog("通知设置", "每天一次按时间点推送；按间隔则从上次推送起算")
        time_edit = LineEdit(dialog)
        time_edit.setPlaceholderText("08:00")
        time_edit.setClearButtonEnabled(False)
        time_edit.setText(self.notify_time_edit.text())
        dialog.add_field("每天推送的时间点，如 08:00", time_edit)
        interval = ComboBox(dialog)
        for hours in notify_schedule.INTERVAL_CHOICES:
            interval.addItem(f"每 {hours} 小时", userData=hours)
        idx = interval.findData(self.notify_interval.currentData())
        interval.setCurrentIndex(idx if idx >= 0 else 0)
        dialog.add_field("按间隔推送时，每隔多久发一次", interval)

        # 手动测试：上方说明 label，按钮横向铺满弹窗
        test_box = QWidget(dialog)
        tl = QVBoxLayout(test_box)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(4)
        tl.addWidget(note_label("点击下方按钮立即发一条测试通知，确认系统通知通道是否通畅"))
        test_btn = PushButton(FIF.SEND, "发送测试通知")
        test_btn.clicked.connect(lambda: self._test_notify(test_btn))
        tl.addWidget(test_btn)
        dialog.add(test_box)
        dialog.show()

        # 关窗前把弹窗里的值写回持久属性并落盘（deleteLater 是排队执行，
        # finished 信号同步回调时控件仍存活，可安全读取）。
        def _write_back():
            self.notify_time_edit.setText(time_edit.text().strip() or store.DEFAULT_NOTIFY_TIME)
            self.notify_interval.setCurrentIndex(interval.currentIndex())
            self._save_config()

        dialog.finished.connect(_write_back)

    def _add_manual(self, user: str, pwd: str, remark: str) -> bool:
        """校验并添加。返回 False 时弹窗保持打开，错误用信息条提示。

        user/pwd/remark 由弹窗内的输入框经闭包传入（每次打开都是全新控件）。"""
        if not user or not pwd:
            InfoBar.warning("账号或密码未填", "填写账号和密码", duration=2500, parent=self)
            return False
        year = ""  # 届别在表格里就地编辑，添加时不再问
        remark = remark or describe_code(user, self.org_rule())
        existing = next((a for a in self.accounts if a["userName"] == user), None)
        if existing and existing["password"] != pwd:
            # 冲突：非模态弹窗，选择后回调落账
            dialogs.confirm(
                self.window(), "账号密码冲突",
                f"账号 {user} 已存在，但密码与现存的（备注：{existing.get('remark') or '无'}）不一致。\n"
                "覆盖会替换密码，保留原密码会跳过这一条",
                yes="覆盖", cancel="保留原密码",
                on_result=lambda ov, u=user, p=pwd, r=remark, y=year:
                    self._add_manual_finish(u, p, r, y, "overwritten" if ov else "kept"),
            )
            return False
        self._add_manual_finish(user, pwd, remark, year,
                                "dedup" if existing else "added")
        return True

    def _add_manual_finish(self, user, pwd, remark, year, result):
        if result == "overwritten":
            existing = next(a for a in self.accounts if a["userName"] == user)
            existing["password"] = pwd
            existing["remark"] = remark or existing.get("remark", "")
            existing["health"] = {"status": "unknown", "message": "", "checkedAt": ""}
        elif result == "added":
            self.accounts.append({
                "userName": user, "password": pwd, "remark": remark,
                "health": {"status": "unknown", "message": "", "checkedAt": ""},
            })
        if result != "kept":
            acc = next(a for a in self.accounts if a["userName"] == user)
            acc["graduateYear"] = year
        self._after_accounts_changed()
        if result == "added":
            InfoBar.success("已添加", f"账号 {user}", duration=3000, parent=self)
        elif result == "dedup":
            InfoBar.info("已去重", f"账号 {user} 同密码已存在，自动跳过", duration=3000, parent=self)
        elif result == "kept":
            InfoBar.info("已保留", f"账号 {user} 保留了原密码", duration=3000, parent=self)
        dialog = getattr(self, "_add_dialog", None)
        if dialog is not None and result != "kept":
            self._add_dialog = None
            if dialog.isVisible():
                dialog.accept()

    def _read_account_file(self, path: str) -> list[tuple[str, str, str, str]]:
        """读账号文件 → (账号, 密码, 备注, 届别)。

        xlsx 表头格式：「账号」「密码」必填，「备注」「届别」可选；
        旧格式（备注、账号、密码三列，或纯文本行）兼容识别；
        备注留空的行按编号规则在导入时预填。"""
        import re as _re

        from app.domain.org_rule import parse_account_row

        account_re = self.org_rule().account_row_re()
        rows: list[tuple[str, str, str, str]] = []
        if path.lower().endswith((".xlsx", ".xlsm")):
            from openpyxl import load_workbook

            wb = load_workbook(path, read_only=True, data_only=True)
            ws = wb.active
            data = list(ws.iter_rows(values_only=True))
            wb.close()
            header = [str(c or "").strip() for c in (data[0] if data else [])]
            has_header = any(h in ("账号", "用户名", "userName", "account") for h in header)
            if has_header:
                u = next((i for i, h in enumerate(header) if h in ("账号", "用户名", "userName", "account")), 1)
                p = next((i for i, h in enumerate(header) if h in ("密码", "password")), 2)
                r = next((i for i, h in enumerate(header) if h in ("备注", "说明", "remark")), 0)
                y = next((i for i, h in enumerate(header) if h in ("届别", "毕业年份", "届")), None)
                for raw in data[1:]:
                    if not raw:
                        continue

                    def cell(i, raw=raw):
                        return str(raw[i]).strip() if i is not None and i < len(raw) and raw[i] is not None else ""

                    if cell(u) and cell(p):
                        rows.append((cell(u), cell(p), cell(r), cell(y)))
            else:
                for raw in data:
                    if not raw:
                        continue
                    parsed = parse_account_row([c for c in raw], account_re)
                    if parsed:
                        rows.append((*parsed, ""))
        else:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    parsed = parse_account_row(_re.split(r"[,;\s]+", line.strip()), account_re)
                    if parsed:
                        rows.append((*parsed, ""))
        return rows

    # ---------- 账号删除（行内按钮 + 勾选批量） ----------

    def _download_account_template(self):
        from PySide6.QtWidgets import QFileDialog

        from app.services.export_data import default_name

        path, _ = QFileDialog.getSaveFileName(
            self, "保存账号模板",
            default_name("团支部账号模板", ".xlsx"), "Excel (*.xlsx)")
        if not path:
            return
        from app.services.excel import save_account_template

        n = save_account_template(path)
        InfoBar.success("模板已保存", f"{path}，{n} 行示例", duration=5000, parent=self)

    def _export_config_json(self):
        """导出当前系统配置为 JSON 文件（config.json 的完整快照，含账号密码）。"""
        from datetime import datetime

        from PySide6.QtWidgets import QFileDialog

        import json

        from app.services.export_data import default_name as export_name

        path, _ = QFileDialog.getSaveFileName(
            self, "导出系统配置", export_name("系统配置", ".json"), "JSON (*.json)")
        if not path:
            return
        cfg = store.load()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError as e:
            InfoBar.error("导出失败", str(e), duration=5000, parent=self)
            return
        n = len(cfg.get("accounts", []))
        InfoBar.success(
            "配置已导出",
            f"{path}（{n} 个账号等全部配置；文件含密码，请妥善保管）",
            duration=6000, parent=self)

    def _remove_accounts(self, rows: list[int]):
        users = [self.accounts[r]["userName"] for r in rows if 0 <= r < len(self.accounts)]
        if not users:
            InfoBar.warning("未选择", "先勾选要删除的账号", duration=2500, parent=self)
            return
        dialogs.confirm(
            self.window(), "删除账号",
            f"确定从列表删除 {'、'.join(users[:5])}{'等' if len(users) > 5 else ''}，共 {len(users)} 个？（不影响站点数据）",
            yes="删除", cancel="取消",
            on_result=lambda ok, rows=list(rows), users=list(users): ok and self._do_remove_accounts(rows, users))

    def _do_remove_accounts(self, rows: list[int], users: list[str]):
        for r in sorted(rows, reverse=True):
            if 0 <= r < len(self.accounts):
                self.accounts.pop(r)
        if self.active_user not in [a["userName"] for a in self.accounts]:
            self.active_user = self.accounts[0]["userName"] if self.accounts else ""
        self._after_accounts_changed()
        InfoBar.success("已删除", f"移除 {len(users)} 个账号", duration=3000, parent=self)

    # ---------- 账号选择 ----------

    def _checked_rows(self) -> list[int]:
        return [i for i in range(self.table.rowCount())
                if self.table.item(i, 0)
                and self.table.item(i, 0).checkState() == Qt.CheckState.Checked]

    def _selected_rows(self) -> list[int]:
        """勾选优先；未勾选用表格选中的行；都没选时用当前账号。"""
        checked = self._checked_rows()
        if checked:
            return checked
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if rows:
            return rows
        idx = next((i for i, a in enumerate(self.accounts)
                    if a["userName"] == self.active_user), -1)
        return [idx] if idx >= 0 else []

    def _update_select_all_box(self):
        states = [self.table.item(i, 0).checkState() for i in range(self.table.rowCount())
                  if self.table.item(i, 0)]
        if states and all(st == Qt.CheckState.Checked for st in states):
            state = Qt.CheckState.Checked
        elif states and any(st != Qt.CheckState.Unchecked for st in states):
            state = Qt.CheckState.PartiallyChecked
        else:
            state = Qt.CheckState.Unchecked
        box = self._select_all_header.box
        box.blockSignals(True)
        box.setCheckState(state)
        box.blockSignals(False)

    def _on_select_all_clicked(self):
        """有未勾选的就全选，否则全部取消；表头勾选框反映整体状态。"""
        rows = [i for i in range(self.table.rowCount()) if self.table.item(i, 0)]
        any_unchecked = any(self.table.item(i, 0).checkState() != Qt.CheckState.Checked
                            for i in rows)
        state = Qt.CheckState.Checked if any_unchecked else Qt.CheckState.Unchecked
        self.table.blockSignals(True)
        for i in rows:
            self.table.item(i, 0).setCheckState(state)
        self.table.blockSignals(False)
        self._update_select_all_box()
        self._sync_active_from_selection()

    def _sync_active_from_selection(self):
        """只选中一个账号时，当前账号跟随切换。"""
        checked = self._checked_rows()
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        picked = checked or rows
        if len(picked) != 1 or not (0 <= picked[0] < len(self.accounts)):
            return
        user = self.accounts[picked[0]]["userName"]
        if user != self.active_user:
            self.active_combo.setCurrentText(user)

    def _refresh_selected(self):
        """刷新所选账号的登录状态与健康状态；未选时刷新当前账号。"""
        users = [self.accounts[i]["userName"] for i in self._selected_rows()
                 if 0 <= i < len(self.accounts)]
        if not users:
            InfoBar.warning("没有账号", "先添加团支部账号", duration=2500, parent=self)
            return
        self.start_health_check(force=True, users=users)

    def _delete_checked(self):
        self._remove_accounts(self._checked_rows())

    # ---------- 表格 / 当前账号 ----------

    READONLY_FLAGS = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    # 列号 → 账号字段；届别=毕业年份（如 2023），留空表示在读未定
    EDITABLE_COLS = {1: "userName", 2: "password", 3: "remark", 4: "graduateYear"}

    def _refresh_table(self):
        if self.table.state() == QAbstractItemView.State.EditingState:
            # 编辑器开着时重建表格会销毁编辑中的 item，视图进入坏状态卡死
            # （编辑密码触发测活 → 结果回来时往往还在编辑）。推迟到编辑结束。
            self._table_refresh_pending = True
            return
        self._table_refresh_pending = False
        checked_users = {self.accounts[i]["userName"] for i in self._checked_rows()
                         if 0 <= i < len(self.accounts)}
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.accounts))
        for i, acc in enumerate(self.accounts):
            # 所有 item 都在独立状态配置完（flags/text/color）再 setItem——
            # setItem 之后的 setFlags 会触发 dataChanged → 列宽重算 →
            # delegate Python override，PySide6 6.12 下会 shiboken 崩溃
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked if acc["userName"] in checked_users
                              else Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, chk)
            # 可编辑列：账号 / 密码 / 备注 / 届别
            for col, key in self.EDITABLE_COLS.items():
                self.table.setItem(i, col, QTableWidgetItem(str(acc.get(key, "") or "")))
            # 状态列：按届别自动判定毕业应转出
            graduated = is_graduated(acc)
            gy = acc.get("graduateYear")
            status_text = f"{gy} 届毕业应转出" if graduated else ("在读" if gy else "")
            status_item = QTableWidgetItem(status_text)
            status_item.setFlags(self.READONLY_FLAGS)
            if graduated:
                status_item.setForeground(QColor(P.WARNING))
            self.table.setItem(i, 5, status_item)
            h = acc.get("health") or {}
            status, msg = h.get("status", "unknown"), h.get("message", "")
            full = HEALTH_LABEL.get(status, status) + (f"：{msg}" if msg and status != "ok" else "")
            # 表格里只放短标签，长解释进 tooltip——否则这一列要么被截断要么把后面挤没
            health_item = QTableWidgetItem(HEALTH_SHORT.get(status, full))
            health_item.setFlags(self.READONLY_FLAGS)
            health_item.setForeground(QColor(HEALTH_COLOR.get(status, "#9e9e9e")))
            set_tip(health_item, full if status == "ok"
                                   else f"{full}\n点这一格用内嵌浏览器打开，手动登录一次即可恢复")
            self.table.setItem(i, 6, health_item)
            checked = h.get("checkedAt", "")
            stamp = checked.replace("T", " ")[:19] if checked else ""
            # 列宽有限：「11-14 23:04」够判断新旧，完整时间放 tooltip
            short = f"{stamp[5:10]} {stamp[11:16]}" if len(stamp) >= 16 else stamp
            check_item = QTableWidgetItem(short)
            check_item.setFlags(self.READONLY_FLAGS)
            if stamp:
                set_tip(check_item, stamp)
            self.table.setItem(i, 7, check_item)
        self.table.blockSignals(False)
        self._update_select_all_box()
        # 列宽按内容算（含委托内边距 48px），「最近检查」不再事后覆盖，否则又被截断
        fit_table_columns(self.table, stretch_last=False, skip_cols={0}, max_col_width=160)
        # 当前使用下拉
        self.active_combo.blockSignals(True)
        self.active_combo.clear()
        self.active_combo.addItems([a["userName"] for a in self.accounts])
        if self.active_user in [a["userName"] for a in self.accounts]:
            self.active_combo.setCurrentText(self.active_user)
        elif self.accounts:
            self.active_user = self.accounts[0]["userName"]
            self.active_combo.setCurrentText(self.active_user)
        self.active_combo.blockSignals(False)

    def _on_item_changed(self, item: QTableWidgetItem):
        """表格内编辑账号/密码/备注后回写配置；无效改动回退。"""
        row, col = item.row(), item.column()
        if col == 0:
            self._update_select_all_box()
            self._sync_active_from_selection()
            return
        if col not in self.EDITABLE_COLS or not (0 <= row < len(self.accounts)):
            return
        acc = self.accounts[row]
        key = self.EDITABLE_COLS[col]
        text = item.text()
        if key == "password":
            new_value = text  # 密码不去空白（可能含空格），不显示明文提示
        elif key == "userName":
            new_value = text.strip()
        else:
            new_value = text.strip()

        if key == "userName" and (not new_value or any(
                a["userName"] == new_value for j, a in enumerate(self.accounts) if j != row)):
            # 空账号或重名：回退
            self.table.blockSignals(True)
            item.setText(acc["userName"])
            self.table.blockSignals(False)
            InfoBar.warning("账号无效", "账号不能为空，也不能与其他账号重复", duration=3000, parent=self)
            return

        if key == "graduateYear":
            from app.domain.transfer import parse_graduate_year

            new_value = text.strip()
            if new_value and parse_graduate_year(new_value) is None:
                self.table.blockSignals(True)
                item.setText(str(acc.get("graduateYear", "") or ""))
                self.table.blockSignals(False)
                InfoBar.warning("届别无效", "届别填毕业年份，如 2023，可留空", duration=3000, parent=self)
                return

        changed = acc.get(key, "") != new_value
        if not changed:
            return
        acc[key] = new_value
        if key in ("userName", "password"):
            acc["health"] = {"status": "unknown", "message": "", "checkedAt": ""}
        self._save_config()
        if key == "graduateYear":
            self._refresh_table()  # 状态列随届别联动
        if key == "userName":
            if self.active_user == text.strip() or not self.active_user:
                self.active_user = new_value
            self._refresh_combo_only()
        if key in ("userName", "password"):
            self.start_health_check(force=True)  # 改账号密码后立即验证，不受 4 小时节流

    def _on_editor_closed(self, editor, hint):
        if getattr(self, "_table_refresh_pending", False):
            QTimer.singleShot(0, self._refresh_table)

    def _refresh_combo_only(self):
        self.active_combo.blockSignals(True)
        self.active_combo.clear()
        self.active_combo.addItems([a["userName"] for a in self.accounts])
        self.active_combo.setCurrentText(self.active_user)
        self.active_combo.blockSignals(False)

    def _on_active_changed(self, user: str):
        if user:
            self.active_user = user
            self._client = None
            self._save_config()

    def start_health_check(self, force: bool = False, users: list[str] | None = None):
        """刷新登录状态，与「同步系统数据」是独立操作。

        自动触发按每个账号 health.checkedAt 做 4 小时节流：最后刷新时间
        距今未超 4 小时就不再测活、探测或登录。网页端正在使用的账号也
        跳过自动测活，避免重新登录顶掉浏览器会话。手动点按钮、新增账号、
        改账号密码都是显式修正入口，force=True 全量刷新并收回网页占用。
        异常账号不享受节流，要留着让验证码/风控冷却结束后自动恢复。
        数据同步由主窗口 30 秒定时链负责，不受这里影响。"""
        if not self.accounts:
            return
        if self._worker and self._worker.isRunning():
            return
        from app.services import login_gate

        accounts = [dict(a) for a in self.accounts]
        if users is not None:
            picked = set(users)
            accounts = [a for a in accounts if a.get("userName") in picked]
        if force:
            if users is None:
                if login_gate.active_browser_sessions():
                    login_gate.clear_browser_sessions()
            else:
                login_gate.clear_browser_sessions(users)
        else:
            accounts = [a for a in accounts if not login_gate.status_fresh(a)]
            busy = login_gate.active_browser_sessions()
            accounts = [a for a in accounts if a.get("userName") not in busy]
        if not accounts:
            return
        self.health_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)

        def _enable_buttons():
            self.health_btn.setEnabled(True)
            self.refresh_btn.setEnabled(True)

        self._worker = HealthWorker(accounts)
        self._worker.account_checked.connect(self._on_account_checked)
        self._worker.finished.connect(_enable_buttons)
        self._worker.finished.connect(self._flush_health_results)  # 兜底：突发结束后必有一次落盘
        self._worker.start()

    # ---------- 浏览器打开（携带登录态） ----------

    def open_web_for(self, user: str):
        """在「账号网页」窗口里打开指定账号。

        站点遇到验证码或风控时接口登不上，只能在浏览器里人工输一次；表格里点
        异常的健康状态、双击某一行都走这里。
        """
        window = getattr(self, "window_ref", None)
        if window is None or not hasattr(window, "open_browser"):
            InfoBar.warning("无法打开", "账号网页窗口尚未就绪", duration=3000, parent=self)
            return
        window.open_browser(user)

    def _user_at_row(self, row: int) -> str:
        if 0 <= row < len(self.accounts):
            return str(self.accounts[row].get("userName") or "")
        return ""

    def _on_cell_clicked(self, row: int, col: int):
        """健康状态不是正常时，点这一格就打开内嵌浏览器手动登录。"""
        if col != 6:
            return
        user = self._user_at_row(row)
        acc = next((a for a in self.accounts if a.get("userName") == user), None)
        if not user or acc is None:
            return
        if (acc.get("health") or {}).get("status", "unknown") == "ok":
            return
        self.open_web_for(user)

    def _on_cell_double_clicked(self, row: int, col: int):
        """双击任意一行：直接打开这个账号的内嵌浏览器。"""
        user = self._user_at_row(row)
        if user:
            self.open_web_for(user)

    def _open_web(self):
        """在「账号网页」窗口里打开勾选账号（未勾选则当前账号）。

        每个账号一份独立的浏览器存储，标签之间不会互相顶掉登录态；
        打开前先走登录接口拿到会话，再把 Cookie 注入对应标签页。
        """
        if not self.accounts:
            InfoBar.warning("没有账号", "请先添加团支部账号", duration=2500, parent=self)
            return
        window = getattr(self, "window_ref", None)
        if window is None or not hasattr(window, "open_browser"):
            InfoBar.warning("无法打开", "账号网页窗口尚未就绪", duration=3000, parent=self)
            return
        checked = self._checked_rows()
        if checked:
            targets = [self.accounts[i] for i in checked if 0 <= i < len(self.accounts)]
        else:
            index = next((i for i, a in enumerate(self.accounts)
                          if a["userName"] == self.active_user), 0)
            targets = [self.accounts[index]]
        for acc in targets[:MAX_WEB_TABS]:
            self.open_web_for(acc["userName"])
        if len(targets) > MAX_WEB_TABS:
            InfoBar.info("只打开前几个", f"账号网页窗口最多同时保留 {MAX_WEB_TABS} 个标签",
                         duration=4000, parent=self)

    def _on_account_checked(self, user: str, status: str, message: str):
        acc = next((a for a in self.accounts if a["userName"] == user), None)
        if acc:
            acc["health"] = {
                "status": status, "message": message,
                "checkedAt": datetime.now().isoformat(timespec="seconds"),
            }
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

    # ---------- 名单（分学期 + 模板） ----------

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

    # ---------- 工作区 ----------

    def _pick_workspace(self):
        d = QFileDialog.getExistingDirectory(self, "选择工作区目录")
        if not d:
            return
        self.ws_edit.setText(d)
        self._setup_watcher(d)
        self._save_config()
        # 立即扫一遍存量 PDF
        self._ws_known = {e.get("path") for e in self._pending_entries()}
        self._scan_workspace()

    def _setup_watcher(self, d: str):
        from PySide6.QtCore import QFileSystemWatcher

        if self._watcher is None:
            self._watcher = QFileSystemWatcher(self)
            self._watcher.directoryChanged.connect(lambda _: self._scan_timer.start())
        for p in self._watcher.directories():
            self._watcher.removePath(p)
        if self._watcher.addPath(d):
            self.workspace_row.set_desc(f"正在监听 {os.path.basename(d) or d}，新 PDF 自动处理")

    def _pending_entries(self) -> list[dict]:
        from app.services.workspace import load_pending

        return load_pending()

    def _scan_workspace(self):
        from app.services.workspace import new_pdf_files

        d = self.ws_edit.text().strip()
        if not d or not os.path.isdir(d):
            return
        files = new_pdf_files(d, self._ws_known)
        if not files:
            return
        self._ws_known.update(files)
        if self._ws_worker and self._ws_worker.isRunning():
            return  # 上一批还在处理；这批等下次目录变化再扫
        self._ws_worker = WorkspaceWorker(files, self)
        self._ws_worker.file_done.connect(self._on_ws_file_done)
        self._ws_worker.start()

    def _on_ws_file_done(self, name: str, level: str, message: str, path: str):
        self.workspace_changed.emit()
        if level == "ok":
            InfoBar.success(f"工作区：{name}", message, duration=4000, parent=self.window_ref)
        else:
            InfoBar.warning(f"工作区：{name}", message, duration=6000, parent=self.window_ref)

    # ---------- 首页头图（设置入口在此，生效于工具首页） ----------

    def _pick_header_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择头图", "", "图片 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return
        store.update(lambda cfg: cfg.__setitem__("headerImage", path))
        if self.home_page:
            self.home_page.banner.set_image_path(path)
        InfoBar.success("头图已设置", os.path.basename(path), duration=2500, parent=self)

    def _clear_header_image(self):
        store.update(lambda cfg: cfg.__setitem__("headerImage", ""))
        if self.home_page:
            self.home_page.banner.set_image_path("")
        InfoBar.info("头图已清除", "首页恢复默认头图", duration=2500, parent=self)

    # ---------- 外观（明暗模式 + 毛玻璃） ----------

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

    def _on_material_changed(self, index: int):
        material = ("none", "acrylic", "mica")[index]
        store.update(lambda cfg: cfg.__setitem__("translucentMaterial", material))
        if self.window_ref is not None:
            self.window_ref.set_material(material)

    # ---------- 团支书名单 ----------

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
        self.sec_row.set_desc(f"支部与团支书姓名，用于核对与通知；已登记 {len(rows)} 个支部"
                              if rows else "支部与团支书姓名，用于核对与通知；尚未导入")

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

    # ---------- 对外提供 ----------

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
