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

from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QTableWidgetItem,
    QWidget,
)

from concurrent.futures import ThreadPoolExecutor, as_completed

from qfluentwidgets import (
    CheckBox,
    ComboBox,
    EditableComboBox,
    FluentIcon as FIF,
    InfoBar,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    TableWidget,
    ToolButton,
)

from app.ui import dialogs
from app.ui.page import CallbackWorker, CollapsibleCard, FluentPage, FullRow, SettingRow, fit_table_columns, note_label
from app.ui.rule_dialog import RuleDialog
from app.domain.naming import Roster
from app.domain.org_rule import OrgRule, RuleError, describe_code, explain_code, sample_codes
from app.domain.transfer import is_graduated
from app.services import store
from app.domain.account_health import HEALTH_COLOR, HEALTH_LABEL
from app.services.api import SiteClient

THEME_INDEX = {"auto": 0, "light": 1, "dark": 2}


def guess_semester() -> str:
    now = datetime.now()
    return f"{now.year}秋" if now.month >= 7 else f"{now.year}春"


class SelectAllHeader(QHeaderView):
    """账号表表头：第 0 列居中放全选勾选框，位置随列宽与横向滚动更新。"""

    def __init__(self, table):
        super().__init__(Qt.Orientation.Horizontal, table)
        self.box = CheckBox(self)
        self.box.setTristate(True)
        self.box.setFixedSize(22, 22)
        self.box.setToolTip("全选")
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


class WebOpenWorker(QThread):
    """逐账号在指定浏览器打开网页端（网络与 AppleScript 操作放线程，避免卡 UI）。

    targets: [(userName, password, browser), …]；浏览器由调用方按账号分配
    （Arc / Safari 的 Cookie 互相独立，各保持一个账号登录）。"""

    done = Signal(bool, str)  # ok, message

    def __init__(self, targets: list[tuple[str, str, str]], parent=None):
        super().__init__(parent)
        self.targets = targets

    def run(self):
        from app.services import webopen

        oks: list[str] = []
        fails: list[str] = []
        for user, pwd, browser in self.targets:
            try:
                client = SiteClient(user, pwd)
                ok, msg = webopen.open_logged_in(client, browser)
            except Exception as e:
                ok, msg = False, f"打开失败：{e}"
            (oks if ok else fails).append(f"{user}（{browser}）" if ok else f"{user}：{msg}")
        if not fails:
            self.done.emit(True, "；".join(oks))
        else:
            self.done.emit(False, ("；".join(oks) + "；" if oks else "") + "失败：" + "；".join(fails))


class SettingsPage(FluentPage):
    roster_changed = Signal(object)
    workspace_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page-settings")
        self.rosters: dict[str, Roster] = {}  # 学期 → 名单
        self._roster_paths: dict[str, str] = {}  # 学期 → xlsx 路径（持久化）
        self.roster: Roster | None = None  # 全部学期合并（供检查/上传用）
        self.accounts: list[dict] = []
        self.active_user = ""
        self._client: SiteClient | None = None
        self._worker: HealthWorker | None = None
        self._ws_worker: WorkspaceWorker | None = None
        self._web_worker: WebOpenWorker | None = None
        self._workers: list[CallbackWorker] = []  # 后台解析 worker 引用，防 GC
        self._ws_known: set[str] = set()
        self.home_page = None  # 由 MainWindow 注入，头图设置后即时生效
        self.window_ref = None  # 由 MainWindow 注入，毛玻璃开关生效用
        self._rule = OrgRule()  # 学校自定义编号规则，_load_config 时从配置恢复

        lay = self.vBoxLayout

        def group(title: str, expanded: bool) -> CollapsibleCard:
            card = CollapsibleCard(title, expanded=expanded)
            lay.addWidget(card)
            return card

        def row(card: CollapsibleCard, widget, *, last: bool = False):
            widget.set_separator(not last)
            card.body_lay.addWidget(widget)

        def controls(*widgets, spacing: int = 8) -> QWidget:
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(spacing)
            for widget in widgets:
                h.addWidget(widget)
            return box

        # ── 团支部账号 ──
        acc_card = group("团支部账号", True)
        self.active_combo = ComboBox()
        self.active_combo.setFixedWidth(190)
        self.active_combo.currentTextChanged.connect(self._on_active_changed)
        self.web_btn = PushButton(FIF.GLOBE, "打开网页端")
        self.web_btn.setToolTip("打开勾选账号的网页端，未勾选时打开当前账号")
        self.web_btn.clicked.connect(self._open_web)
        row(acc_card, SettingRow("当前账号", icon=FIF.PEOPLE,
                                 control=controls(self.active_combo, self.web_btn), control_width=340))

        self.health_btn = PushButton(FIF.SYNC, "全部测活")
        self.health_btn.clicked.connect(lambda: self.start_health_check(force=True))
        self.refresh_all_btn = PrimaryPushButton(FIF.SYNC, "刷新所有数据")
        self.refresh_all_btn.setToolTip("依次刷新成员缓存、站内信、组织状况")
        self.refresh_all_btn.clicked.connect(self._refresh_all_clicked)
        row(acc_card, SettingRow("测活与刷新", icon=FIF.SYNC,
                                 control=controls(self.health_btn, self.refresh_all_btn), control_width=340))

        self.del_checked_btn = ToolButton(FIF.DELETE)
        self.del_checked_btn.setToolTip("删除勾选")
        self.del_checked_btn.clicked.connect(self._delete_checked)
        self.refresh_btn = ToolButton(FIF.UPDATE)
        self.refresh_btn.setToolTip("刷新所选账号")
        self.refresh_btn.clicked.connect(self._refresh_selected)

        # 账号表：勾选框 + 账号 + 密码 + 备注 + 届别 + 状态 + 健康状态 + 最近检查
        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
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
        header.setSectionResizeMode(7, header.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._sync_active_from_selection)
        self.table.itemDelegate().closeEditor.connect(self._on_editor_closed)
        table_block = FullRow()
        tools = QHBoxLayout()
        tools.addWidget(self.del_checked_btn)
        tools.addWidget(self.refresh_btn)
        tools.addStretch(1)
        table_block.lay.addLayout(tools)
        table_block.lay.addWidget(self.table)
        row(acc_card, table_block)

        self.new_user = LineEdit()
        self.new_user.setPlaceholderText("账号")
        self.new_user.setFixedWidth(108)
        self.new_pwd = LineEdit()
        self.new_pwd.setPlaceholderText("密码")
        self.new_pwd.setFixedWidth(108)
        self.new_remark = LineEdit()
        self.new_remark.setPlaceholderText("备注")
        self.new_remark.setFixedWidth(108)
        self.new_year = LineEdit()
        self.new_year.setPlaceholderText("届别")
        self.new_year.setFixedWidth(88)
        self.add_btn = PrimaryPushButton(FIF.ADD, "添加")
        self.add_btn.clicked.connect(self._add_manual)
        row(acc_card, SettingRow("添加账号", icon=FIF.ADD,
                                 control=controls(self.new_user, self.new_pwd, self.new_remark,
                                                  self.new_year, self.add_btn, spacing=6),
                                 control_width=540))

        self.import_btn = PushButton(FIF.DOCUMENT, "批量导入")
        self.import_btn.clicked.connect(self._import_accounts)
        self.tpl_btn = PushButton(FIF.DOWNLOAD, "下载账号模板")
        self.tpl_btn.setToolTip("下载账号导入模板")
        self.tpl_btn.clicked.connect(self._download_account_template)
        self.export_cfg_btn = PushButton(FIF.SHARE, "导出配置 JSON")
        self.export_cfg_btn.setToolTip("导出系统配置")
        self.export_cfg_btn.clicked.connect(self._export_config_json)
        row(acc_card, SettingRow("账号文件", icon=FIF.DOCUMENT,
                                 control=controls(self.import_btn, self.tpl_btn, self.export_cfg_btn),
                                 control_width=420), last=True)

        # ── 名单导入 ──
        ros_card = group("名单导入", True)
        self.semester_combo = EditableComboBox()
        self.semester_combo.addItems([guess_semester()])
        self.semester_combo.setCurrentText(guess_semester())
        self.semester_combo.setPlaceholderText("选择或输入学期")
        self.semester_combo.setFixedWidth(150)
        self.semester_combo.currentTextChanged.connect(self._on_semester_changed)
        self.new_sem_btn = PushButton(FIF.ADD, "新建学期")
        self.new_sem_btn.setToolTip("按学期名新建空学期")
        self.new_sem_btn.clicked.connect(self._create_semester)
        self.roster_btn = PrimaryPushButton("导入到该学期")
        self.roster_btn.clicked.connect(self._import_roster)
        row(ros_card, SettingRow("团员名单", icon=FIF.DOCUMENT,
                                 control=controls(self.semester_combo, self.new_sem_btn, self.roster_btn),
                                 control_width=430))

        self.template_btn = PushButton(FIF.DOWNLOAD, "下载名单模板")
        self.template_btn.clicked.connect(self._download_template)
        self.roster_del_btn = PushButton(FIF.DELETE, "删除所选学期名单")
        self.roster_del_btn.setToolTip("从配置移除该学期名单")
        self.roster_del_btn.clicked.connect(self._delete_selected_roster)
        self.roster_table = TableWidget()
        self.roster_table.setColumnCount(3)
        self.roster_table.setHorizontalHeaderLabels(["学期", "人数", "名单文件"])
        self.roster_table.setEditTriggers(self.roster_table.EditTrigger.NoEditTriggers)
        header = self.roster_table.horizontalHeader()
        header.setSectionResizeMode(2, header.ResizeMode.Stretch)
        self.roster_table.setColumnWidth(2, 320)
        roster_block = FullRow()
        roster_block.lay.addWidget(self.roster_table)
        roster_tools = QHBoxLayout()
        roster_tools.addWidget(self.template_btn)
        roster_tools.addWidget(self.roster_del_btn)
        roster_tools.addStretch(1)
        roster_block.lay.addLayout(roster_tools)
        row(ros_card, roster_block)

        self.sec_btn = PushButton(FIF.DOCUMENT, "导入团支书名单")
        self.sec_btn.clicked.connect(self._import_secretaries)
        self.sec_del_btn = PushButton(FIF.DELETE, "删除所选团支书")
        self.sec_del_btn.clicked.connect(self._delete_selected_secretaries)
        self.sec_status = note_label("未导入")
        self.sec_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row(ros_card, SettingRow("团支书名单", icon=FIF.PEOPLE,
                                 control=controls(self.sec_btn, self.sec_del_btn, self.sec_status),
                                 control_width=430))
        self.sec_table = TableWidget()
        self.sec_table.setColumnCount(3)
        self.sec_table.setHorizontalHeaderLabels(["支部", "团支书", "手机号码"])
        self.sec_table.setEditTriggers(self.sec_table.EditTrigger.NoEditTriggers)
        sec_block = FullRow()
        sec_block.lay.addWidget(self.sec_table)
        row(ros_card, sec_block, last=True)

        # ── 检查与编号规则 ──
        rule_card = group("检查与编号规则", False)
        self.rule_state = note_label("")
        self.rule_btn = PushButton(FIF.EDIT, "编辑规则")
        self.rule_btn.clicked.connect(self._open_rule_dialog)
        row(rule_card, SettingRow("编号规则", icon=FIF.EDUCATION,
                                 control=controls(self.rule_state, self.rule_btn),
                                 control_width=300))
        self.expected_pages_edit = LineEdit()
        self.expected_pages_edit.setPlaceholderText("如 4")
        self.expected_pages_edit.setFixedWidth(160)
        self.expected_pages_edit.editingFinished.connect(self._save_config)
        row(rule_card, SettingRow("预期页数", icon=FIF.DOCUMENT, control=self.expected_pages_edit))
        self.rule_preview = note_label("")
        preview = FullRow()
        preview.lay.addWidget(self.rule_preview)
        row(rule_card, preview, last=True)

        # ── 外观 ──
        look_card = group("外观", False)
        self.theme_combo = ComboBox()
        for label in ("跟随系统", "浅色模式", "暗色模式"):
            self.theme_combo.addItem(label)
        self.theme_combo.setCurrentIndex(THEME_INDEX.get(store.load().get("themeMode", "auto"), 0))
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        row(look_card, SettingRow("明暗模式", icon=FIF.BRIGHTNESS, control=self.theme_combo))
        self.material_combo = ComboBox()
        for label in ("关闭", "Acrylic 毛玻璃", "Sidebar 浅毛玻璃", "HudWindow 深色毛玻璃"):
            self.material_combo.addItem(label)
        _cfg = store.load()
        _material = _cfg.get("translucentMaterial") or ("acrylic" if _cfg.get("translucent") else "none")
        self.material_combo.setCurrentIndex({"none": 0, "acrylic": 1, "sidebar": 2, "hud": 3}.get(_material, 0))
        self.material_combo.currentIndexChanged.connect(self._on_material_changed)
        row(look_card, SettingRow("窗口背景", icon=FIF.TILES, control=self.material_combo))
        self.header_pick_btn = PushButton(FIF.PHOTO, "选择图片")
        self.header_pick_btn.clicked.connect(self._pick_header_image)
        self.header_clear_btn = PushButton(FIF.DELETE, "清除头图")
        self.header_clear_btn.clicked.connect(self._clear_header_image)
        row(look_card, SettingRow("首页头图", icon=FIF.PHOTO,
                                  control=controls(self.header_pick_btn, self.header_clear_btn),
                                  control_width=300), last=True)

        # ── 工作区与通知 ──
        ws_card = group("工作区与通知", False)
        self.ws_edit = LineEdit()
        self.ws_edit.setPlaceholderText("选择工作区目录")
        self.ws_edit.setFixedWidth(230)
        self.ws_btn = PushButton(FIF.FOLDER, "选择目录")
        self.ws_btn.clicked.connect(self._pick_workspace)
        self.ws_status = note_label("未监听")
        self.ws_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        ws_ctrl = QWidget()
        wv = QVBoxLayout(ws_ctrl)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.setSpacing(4)
        wv.addWidget(controls(self.ws_edit, self.ws_btn))
        wv.addWidget(self.ws_status, 0, Qt.AlignmentFlag.AlignRight)
        row(ws_card, SettingRow("工作区", icon=FIF.FOLDER, control=ws_ctrl, control_width=340))
        self.notify_time_edit = LineEdit()
        self.notify_time_edit.setFixedWidth(90)
        self.notify_time_edit.setPlaceholderText("08:00")
        self.notify_time_edit.setClearButtonEnabled(False)
        row(ws_card, SettingRow("每日通知", icon=FIF.DATE_TIME, control=self.notify_time_edit))
        self.autostart_btn = PushButton()
        self.autostart_btn.clicked.connect(self._toggle_autostart)
        row(ws_card, SettingRow("开机自启动", icon=FIF.POWER_BUTTON, control=self.autostart_btn), last=True)

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
        self._refresh_autostart_btn()

        # 名单：分学期（只新建未导入的学期没有文件路径，也要保留）。
        # 所有学期立即占位（下拉/名单表即时完整），xlsx 解析放后台线程，完成后替换数据
        self.rosters = {}
        self._roster_paths = {}
        semesters = dict(cfg.get("rosters") or {})
        if cfg.get("rosterPath") and not semesters:
            semesters[guess_semester()] = cfg["rosterPath"]  # 旧版单文件迁移
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
        self.semester_combo.blockSignals(True)
        self.semester_combo.clear()
        self.semester_combo.addItems(sorted(self.rosters) or [guess_semester()])
        self.semester_combo.setCurrentText(guess_semester())
        self.semester_combo.blockSignals(False)
        self._refresh_roster()

        # 工作区
        ws_dir = cfg.get("workspaceDir", "")
        self.ws_edit.setText(ws_dir)
        if ws_dir and os.path.isdir(ws_dir):
            self._setup_watcher(ws_dir)

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
        r = self.org_rule()
        self.rule_state.setText(f"{len(r.effective_patterns())} 条规则")
        parts = []
        for code in sample_codes(r):
            info, why = explain_code(code, r)
            parts.append(f"{code}  {info.label if info else why}")
        self.rule_preview.setText("，".join(parts))

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
                "expectedPages": self.expected_pages(),
                "orgRule": self.org_rule().to_config(),
            })

        # 拿最新配置再改：工作线程刚写入的 memberCache/messagesCache 不会被旧快照覆盖
        store.update(_write)
        self._client = None

    def _refresh_roster(self):
        """合并各学期名单为总表（供检查/上传使用），刷新名单管理列表。"""
        merged = Roster("combined")
        for sem in sorted(self.rosters):
            merged.rows.extend(self.rosters[sem].rows)
        self.roster = merged if merged.rows else None
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
            self.window(), "删除学期名单",
            f"删除 {sem} 的名单（{n} 人）？磁盘上的 xlsx 文件不会被删除。",
            on_result=lambda ok, sem=sem: ok and self._do_delete_roster(sem))

    def _do_delete_roster(self, sem: str):
        self.rosters.pop(sem, None)
        self._roster_paths.pop(sem, None)
        idx = self.semester_combo.findText(sem)
        if idx >= 0:
            self.semester_combo.removeItem(idx)
        self._refresh_roster()
        self._save_config()
        InfoBar.success("已删除", f"{sem} 的名单已从配置移除", duration=3000, parent=self)

    # ---------- 自启动 / 每日通知 ----------

    def _refresh_autostart_btn(self):
        from app.services import autostart

        if autostart.is_installed():
            self.autostart_btn.setText("关闭开机自启动")
        else:
            self.autostart_btn.setText("开启开机自启动")

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

    def _add_manual(self):
        user, pwd = self.new_user.text().strip(), self.new_pwd.text()
        if not user or not pwd:
            InfoBar.warning("账号或密码未填", "填写账号和密码", duration=2500, parent=self)
            return
        year = self.new_year.text().strip()
        if year:
            from app.domain.transfer import parse_graduate_year

            if parse_graduate_year(year) is None:
                InfoBar.warning("届别无效", "届别填毕业年份，如 2023，可留空", duration=3000, parent=self)
                return
        remark = self.new_remark.text().strip() or describe_code(user, self.org_rule())
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
            return
        self._add_manual_finish(user, pwd, remark, year,
                                "dedup" if existing else "added")

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
        self.new_user.clear()
        self.new_pwd.clear()
        self.new_remark.clear()
        self.new_year.clear()
        self._after_accounts_changed()
        if result == "added":
            InfoBar.success("已添加", f"账号 {user}", duration=3000, parent=self)
        elif result == "dedup":
            InfoBar.info("已去重", f"账号 {user} 同密码已存在，自动跳过", duration=3000, parent=self)
        elif result == "kept":
            InfoBar.info("已保留", f"账号 {user} 保留了原密码", duration=3000, parent=self)

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

        path, _ = QFileDialog.getSaveFileName(
            self, "保存账号模板", "团支部账号模板.xlsx", "Excel (*.xlsx)")
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

        default_name = f"智慧团建配置-{datetime.now().strftime('%Y%m%d')}.json"
        path, _ = QFileDialog.getSaveFileName(self, "导出系统配置", default_name, "JSON (*.json)")
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
                status_item.setForeground(QColor("#ef6c00"))
            self.table.setItem(i, 5, status_item)
            h = acc.get("health") or {}
            status, msg = h.get("status", "unknown"), h.get("message", "")
            label = HEALTH_LABEL.get(status, status) + (f"：{msg}" if msg and status != "ok" else "")
            health_item = QTableWidgetItem(label)
            health_item.setFlags(self.READONLY_FLAGS)
            health_item.setForeground(QColor(HEALTH_COLOR.get(status, "#9e9e9e")))
            self.table.setItem(i, 6, health_item)
            checked = h.get("checkedAt", "")
            check_item = QTableWidgetItem(checked.replace("T", " ")[:19] if checked else "")
            check_item.setFlags(self.READONLY_FLAGS)
            self.table.setItem(i, 7, check_item)
        self.table.blockSignals(False)
        self._update_select_all_box()
        # 健康状态文案很长，列宽上限压到 180，否则「最近检查」列会被挤成 30px
        fit_table_columns(self.table, stretch_last=True, skip_cols={0}, max_col_width=180)
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

    def _open_web(self):
        """打开勾选账号的网页端（每个勾选账号一个标签页），未勾选时打开当前使用账号。

        浏览器登录位有限：Arc 与 Safari 的 Cookie 互相独立，各自同时只能保持
        一个账号登录，多开的账号会顶掉先打开账号的 SESSION（登录失效）。
        账号按 Arc → Safari 轮流分配；超过浏览器数的账号不打开并明确告知。"""
        from app.services import webopen

        if not self.accounts:
            InfoBar.warning("没有账号", "请先添加团支部账号", duration=2500, parent=self)
            return
        if self._web_worker and self._web_worker.isRunning():
            return
        checked = self._checked_rows()
        if checked:
            targets = [self.accounts[i] for i in checked if 0 <= i < len(self.accounts)]
        else:
            idx = next((i for i, a in enumerate(self.accounts) if a["userName"] == self.active_user), 0)
            targets = [self.accounts[idx]]
        browsers = webopen.available_browsers()
        overflow = [a["userName"] for a in targets[len(browsers):]]
        targets = targets[:len(browsers)]
        self.web_btn.setEnabled(False)
        self._web_worker = WebOpenWorker(
            [(a["userName"], a["password"], browsers[i]) for i, a in enumerate(targets)], self)
        self._web_worker.done.connect(self._on_web_opened)
        self._web_worker.start()
        if overflow:
            InfoBar.warning(
                "部分账号未打开",
                f"{'、'.join(overflow)} 未打开：本机 {('、'.join(browsers))} 各只能保持一个账号的登录态"
                f"（共 {len(browsers)} 个），多开会使已打开账号登录失效；请处理完再分批打开",
                duration=10000, parent=self)

    def _on_web_opened(self, ok: bool, msg: str):
        self.web_btn.setEnabled(True)
        if ok:
            InfoBar.success("打开网页", msg, duration=5000, parent=self)
        else:
            InfoBar.warning("打开网页", msg, duration=8000, parent=self)

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
        sem = self.semester_combo.currentText().strip()
        if not sem:
            InfoBar.warning("学期为空", "选择已有学期或输入新学期名", duration=3000, parent=self)
            return
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
        # 新学期名自动加入下拉列表（建立学期）
        items = [self.semester_combo.itemText(i) for i in range(self.semester_combo.count())]
        if sem not in items:
            self.semester_combo.addItem(sem)
        self.semester_combo.setCurrentText(sem)
        self._refresh_roster()
        self._save_config()
        InfoBar.success(f"已导入 {sem}", f"{len(roster.rows)} 名团员", duration=3000, parent=self)

    def _on_semester_changed(self, sem: str):
        """切换学期。"""

    def _create_semester(self):
        """按学期框里的名字新建空学期（不用先导入名单）。"""
        sem = self.semester_combo.currentText().strip()
        if not sem:
            InfoBar.warning("学期为空", "输入学期名再新建", duration=3000, parent=self)
            return
        if sem in self.rosters:
            InfoBar.info("学期已存在", f"{sem} 已在列表中，可直接导入名单", duration=3000, parent=self)
            return
        self.rosters[sem] = Roster(sem)
        self._roster_paths[sem] = ""
        items = [self.semester_combo.itemText(i) for i in range(self.semester_combo.count())]
        if sem not in items:
            self.semester_combo.addItem(sem)
        self.semester_combo.setCurrentText(sem)
        self._refresh_roster()
        self._save_config()
        InfoBar.success("学期已新建", f"{sem} 已新建，选择名单文件后导入", duration=3000, parent=self)

    def _download_template(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存名单模板", "团员名单模板.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        from app.services.excel import save_roster_template

        save_roster_template(path)
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
            self.ws_status.setText(f"监听中：{os.path.basename(d) or d}")

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
        apply_theme_mode(mode)
        store.update(lambda cfg: cfg.__setitem__("themeMode", mode))
        if self.window_ref is not None:
            self.window_ref.on_theme_changed()
        InfoBar.success("明暗模式已切换", ("跟随系统" if index == 0 else "已生效"),
                        duration=2500, parent=self)

    def _on_material_changed(self, index: int):
        material = ("none", "acrylic", "sidebar", "hud")[index]
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
        self.sec_status.setText(f"已登记 {len(rows)} 个支部" if rows else "未导入")

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
