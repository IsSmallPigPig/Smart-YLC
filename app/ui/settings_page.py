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
    QVBoxLayout,
    QTableWidgetItem,
    QTreeWidgetItem,
    QWidget,
)

from concurrent.futures import ThreadPoolExecutor, as_completed

from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    EditableComboBox,
    FluentIcon as FIF,
    InfoBar,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
    TreeWidget,
)

from app.ui import dialogs
from app.ui.page import CollapsibleCard, FluentPage, note_label, sub_title
from app.domain.naming import Roster
from app.domain.org_rule import OrgRule, describe_code, parse_class_code
from app.domain.transfer import is_graduated
from app.services import store
from app.services.api import SiteClient

HEALTH_LABEL = {
    "ok": "正常",
    "failed": "密码错误",
    "captcha": "需验证码（先去网页端登录一次）",
    "cooldown": "登录冷却（验证码/风控，1 小时后自动重试）",
    "passwordChange": "需改密（EXPIREDPASS/WEAKPASS）",
    "needMemberName": "需姓名校验",
    "network": "网络错误",
    "unknown": "未检测",
}
THEME_INDEX = {"auto": 0, "light": 1, "dark": 2}
HEALTH_COLOR = {
    "ok": "#2e7d32",
    "failed": "#c62828",
    "captcha": "#ef6c00",
    "cooldown": "#9e9e9e",
    "passwordChange": "#ef6c00",
    "needMemberName": "#ef6c00",
    "network": "#9e9e9e",
    "unknown": "#9e9e9e",
}


def guess_semester() -> str:
    now = datetime.now()
    return f"{now.year}秋" if now.month >= 7 else f"{now.year}春"


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
    """登录当前账号后携带 cookie 在浏览器打开站点（网络操作放线程，避免卡 UI）。"""

    done = Signal(bool, str)  # ok, message

    def __init__(self, user: str, pwd: str, parent=None):
        super().__init__(parent)
        self.user, self.pwd = user, pwd

    def run(self):
        try:
            from app.services import webopen

            client = SiteClient(self.user, self.pwd)
            ok, msg = webopen.open_logged_in(client)
            self.done.emit(ok, msg)
        except Exception as e:
            self.done.emit(False, f"打开失败：{e}")


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
        self._ws_known: set[str] = set()
        self.home_page = None  # 由 MainWindow 注入，头图设置后即时生效
        self.window_ref = None  # 由 MainWindow 注入，毛玻璃开关生效用
        self._filter_label = ""  # 当前团支部筛选（完整备注名，空 = 不筛选）

        lay = self.vBoxLayout

        # ── 卡片一：团支部账号（当前账号 / 测活 / 账号表 / 添加导入） ──
        acc_card = CollapsibleCard("团支部账号", expanded=True)
        a = acc_card.body_lay
        a.addWidget(note_label("测活复用上次登录的会话探测，有效不登录；触发验证码/风控的账号 1 小时内不再尝试登录"))
        row0 = QHBoxLayout()
        row0.addWidget(BodyLabel("当前账号"))
        self.active_combo = ComboBox()
        self.active_combo.currentTextChanged.connect(self._on_active_changed)
        self.web_btn = PushButton(FIF.GLOBE, "打开网页端")
        self.web_btn.setToolTip("在浏览器打开当前账号的网页端（携带登录态）")
        self.web_btn.clicked.connect(self._open_web)
        self.health_btn = PushButton(FIF.SYNC, "全部测活")
        self.health_btn.clicked.connect(self.start_health_check)
        self.refresh_all_btn = PrimaryPushButton(FIF.SYNC, "刷新所有数据")
        self.refresh_all_btn.setToolTip("成员缓存 → 站内信 → 组织状况，全部在工作线程串行刷新")
        self.refresh_all_btn.clicked.connect(self._refresh_all_clicked)
        row0.addWidget(self.active_combo, 1)
        row0.addWidget(self.web_btn)
        row0.addWidget(self.health_btn)
        row0.addWidget(self.refresh_all_btn)
        a.addLayout(row0)

        row_t = QHBoxLayout()
        self.sel_all_btn = PushButton("全选")
        self.sel_all_btn.setToolTip("全选 / 取消全选（仅可见行）")
        self.sel_all_btn.clicked.connect(self._toggle_select_all)
        self.del_checked_btn = PushButton(FIF.DELETE, "删除勾选")
        self.del_checked_btn.setToolTip("删除勾选账号")
        self.del_checked_btn.clicked.connect(self._delete_checked)
        self.filter_btn = PushButton(FIF.FILTER, "筛选")
        self.filter_btn.setToolTip("按团支部筛选（树状：学部 → 年级 → 班级）")
        self.filter_btn.clicked.connect(self._show_filter_menu)
        self.filter_note = note_label("")
        row_t.addWidget(self.sel_all_btn)
        row_t.addWidget(self.del_checked_btn)
        row_t.addWidget(self.filter_btn)
        row_t.addSpacing(8)
        row_t.addWidget(self.filter_note)
        row_t.addStretch(1)
        a.addLayout(row_t)

        # 账号表：勾选框 + 账号 + 密码 + 备注 + 届别 + 状态 + 健康状态 + 最近检查
        # 账号/密码/备注/届别可编辑，状态按届别自动算（毕业应转出），其余只读
        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(["", "账号", "密码", "备注", "届别", "状态", "健康状态", "最近检查"])
        self.table.setColumnWidth(0, 46)
        self.table.setMinimumHeight(180)
        a.addWidget(self.table)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.ResizeMode.Fixed)  # 勾选框列固定宽
        for col in (1, 2, 3, 4, 5, 6):  # 内容自适应，保证完整展示
            header.setSectionResizeMode(col, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, header.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        # 编辑器关闭时补刷被推迟的表格刷新（编辑期间测活结果回来不能立刻重建）
        self.table.itemDelegate().closeEditor.connect(self._on_editor_closed)

        a.addWidget(sub_title("添加 / 导入账号"))
        a.addWidget(note_label("同账号同密码自动去重；同名账号密码不同时会询问如何处理"))
        row1 = QHBoxLayout()
        self.new_user = LineEdit()
        self.new_user.setPlaceholderText("账号")
        self.new_pwd = LineEdit()
        self.new_pwd.setPlaceholderText("密码")
        self.new_remark = LineEdit()
        self.new_remark.setPlaceholderText("备注（如 高一3班团支部）")
        self.new_year = LineEdit()
        self.new_year.setPlaceholderText("届别（毕业年份，可选）")
        self.add_btn = PrimaryPushButton(FIF.ADD, "添加")
        self.add_btn.clicked.connect(self._add_manual)
        row1.addWidget(self.new_user, 1)
        row1.addWidget(self.new_pwd, 1)
        row1.addWidget(self.new_remark, 1)
        row1.addWidget(self.new_year, 1)
        row1.addWidget(self.add_btn)
        a.addLayout(row1)
        row2 = QHBoxLayout()
        self.import_btn = PushButton(FIF.DOCUMENT, "批量导入")
        self.import_btn.clicked.connect(self._import_accounts)
        self.tpl_btn = PushButton(FIF.DOWNLOAD, "下载账号模板")
        self.tpl_btn.setToolTip("下载账号导入模板（备注 / 账号 / 密码）")
        self.tpl_btn.clicked.connect(self._download_account_template)
        self.export_cfg_btn = PushButton(FIF.SHARE, "导出配置 JSON")
        self.export_cfg_btn.setToolTip("导出系统配置为 JSON 文件（完整备份，含账号密码）")
        self.export_cfg_btn.clicked.connect(self._export_config_json)
        row2.addWidget(self.import_btn)
        row2.addWidget(self.tpl_btn)
        row2.addWidget(self.export_cfg_btn)
        row2.addStretch(1)
        a.addLayout(row2)
        lay.addWidget(acc_card)

        # ── 卡片二：名单导入（预设团员名单 + 团支书名单） ──
        ros_card = CollapsibleCard("名单导入", expanded=True)
        r = ros_card.body_lay
        r.addWidget(sub_title("预设团员名单"))
        r.addWidget(note_label("xlsx 列：姓名 / 手机号码 / 班级（团支部编号，如 12407）；学期可下拉选择、输入新名新建或直接导入，导入后按班级自动分流"))
        row3 = QHBoxLayout()
        row3.addWidget(BodyLabel("学期"))
        self.semester_combo = EditableComboBox()
        self.semester_combo.addItems([guess_semester()])
        self.semester_combo.setCurrentText(guess_semester())
        self.semester_combo.setPlaceholderText("选择或输入学期（如 2027春）")
        self.semester_combo.currentTextChanged.connect(self._on_semester_changed)
        self.new_sem_btn = PushButton(FIF.ADD, "新建学期")
        self.new_sem_btn.setToolTip("按学期框里的名字新建一个空学期，之后再导入名单")
        self.new_sem_btn.clicked.connect(self._create_semester)
        self.roster_edit = LineEdit()
        self.roster_edit.setPlaceholderText("选择名单文件")
        self.roster_btn = PrimaryPushButton("导入到该学期")
        self.roster_btn.clicked.connect(self._import_roster)
        row3.addWidget(self.semester_combo)
        row3.addWidget(self.new_sem_btn)
        row3.addWidget(self.roster_edit, 1)
        row3.addWidget(self.roster_btn)
        r.addLayout(row3)
        row_tpl = QHBoxLayout()
        self.template_btn = PushButton(FIF.DOWNLOAD, "下载名单模板")
        self.template_btn.clicked.connect(self._download_template)
        row_tpl.addWidget(self.template_btn)
        row_tpl.addStretch(1)
        r.addLayout(row_tpl)
        # 已导入名单列表：一行一个学期，便于管理多个表格
        self.roster_table = TableWidget()
        self.roster_table.setColumnCount(3)
        self.roster_table.setHorizontalHeaderLabels(["学期", "人数", "名单文件"])
        self.roster_table.setEditTriggers(self.roster_table.EditTrigger.NoEditTriggers)
        self.roster_table.setMinimumHeight(120)
        r.addWidget(self.roster_table)
        row_rdel = QHBoxLayout()
        self.roster_del_btn = PushButton(FIF.DELETE, "删除所选学期名单")
        self.roster_del_btn.setToolTip("从配置中移除该学期名单（不删除磁盘上的 xlsx 文件）")
        self.roster_del_btn.clicked.connect(self._delete_selected_roster)
        row_rdel.addWidget(self.roster_del_btn)
        row_rdel.addStretch(1)
        r.addLayout(row_rdel)
        r.addWidget(sub_title("团支书名单"))
        r.addWidget(note_label("xlsx 列：支部（编号或名称） / 团支书（姓名） / 手机号码（可选）；展示在「组织管理」的支部信息里"))
        row_sec = QHBoxLayout()
        self.sec_btn = PushButton(FIF.DOCUMENT, "导入团支书名单")
        self.sec_btn.clicked.connect(self._import_secretaries)
        self.sec_status = note_label("未导入")
        row_sec.addWidget(self.sec_btn)
        row_sec.addSpacing(8)
        row_sec.addWidget(self.sec_status)
        row_sec.addStretch(1)
        r.addLayout(row_sec)
        lay.addWidget(ros_card)

        # ── 卡片三：检查与编号规则（默认收起） ──
        rule_card = CollapsibleCard("检查与编号规则", expanded=False)
        r2 = rule_card.body_lay
        r2.addWidget(sub_title("团支部编号规则"))
        r2.addWidget(note_label(
            "编号 = 学部前缀（1 位）+ 入学年份后两位（2 位）+ 班号（2 位）；无前缀的 4 位编号归「无前缀学部」。"
            "年级按当前学年自动推算（如 24 级今年是高三/初三）。用于导入账号时预填备注和账号筛选"))
        row_rule = QHBoxLayout()
        row_rule.addWidget(BodyLabel("学部前缀"))
        self.rule_prefix_edit = LineEdit()
        self.rule_prefix_edit.setPlaceholderText("1=普通高中，2=国际高中")
        row_rule.addWidget(self.rule_prefix_edit, 1)
        row_rule.addWidget(BodyLabel("无前缀学部"))
        self.rule_noprefix_edit = LineEdit()
        self.rule_noprefix_edit.setPlaceholderText("初中部")
        self.rule_noprefix_edit.setFixedWidth(120)
        row_rule.addWidget(self.rule_noprefix_edit)
        r2.addLayout(row_rule)
        self.rule_preview = note_label("")
        r2.addWidget(self.rule_preview)
        self.rule_prefix_edit.editingFinished.connect(self._on_rule_changed)
        self.rule_noprefix_edit.editingFinished.connect(self._on_rule_changed)
        r2.addWidget(sub_title("文档检查（入团志愿书 PDF）"))
        r2.addWidget(note_label("「文档检查」页除命名外，还核对 PDF 总页数是否等于预期页数；留空则不核对页数"))
        row6 = QHBoxLayout()
        row6.addWidget(BodyLabel("预期页数"))
        self.expected_pages_edit = LineEdit()
        self.expected_pages_edit.setPlaceholderText("如 4；留空则不核对页数")
        self.expected_pages_edit.setFixedWidth(160)
        self.expected_pages_edit.editingFinished.connect(self._save_config)
        row6.addWidget(self.expected_pages_edit)
        row6.addStretch(1)
        r2.addLayout(row6)
        lay.addWidget(rule_card)

        # ── 卡片四：外观（明暗模式 / 毛玻璃 / 首页头图，默认收起） ──
        look_card = CollapsibleCard("外观", expanded=False)
        r3 = look_card.body_lay
        theme_row = QHBoxLayout()
        theme_row.addWidget(BodyLabel("明暗模式"))
        theme_row.addStretch(1)
        self.theme_combo = ComboBox()
        for label in ("跟随系统", "浅色模式", "暗色模式"):
            self.theme_combo.addItem(label)
        self.theme_combo.setCurrentIndex(THEME_INDEX.get(store.load().get("themeMode", "auto"), 0))
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self.theme_combo)
        r3.addLayout(theme_row)
        material_row = QHBoxLayout()
        material_row.addWidget(BodyLabel("窗口背景透明材质"))
        material_row.addStretch(1)
        self.material_combo = ComboBox()
        for label in ("关闭", "Acrylic 毛玻璃", "Sidebar 浅毛玻璃", "HudWindow 深色毛玻璃"):
            self.material_combo.addItem(label)
        _cfg = store.load()
        _material = _cfg.get("translucentMaterial") or ("acrylic" if _cfg.get("translucent") else "none")
        self.material_combo.setCurrentIndex({"none": 0, "acrylic": 1, "sidebar": 2, "hud": 3}.get(_material, 0))
        self.material_combo.currentIndexChanged.connect(self._on_material_changed)
        material_row.addWidget(self.material_combo)
        r3.addLayout(material_row)
        r3.addWidget(sub_title("首页头图"))
        r3.addWidget(note_label("图片会铺满工具首页顶部并自然过渡到背景；清除后显示占位渐变"))
        row7 = QHBoxLayout()
        self.header_pick_btn = PushButton(FIF.PHOTO, "选择图片")
        self.header_pick_btn.clicked.connect(self._pick_header_image)
        self.header_clear_btn = PushButton(FIF.DELETE, "清除头图")
        self.header_clear_btn.clicked.connect(self._clear_header_image)
        row7.addWidget(self.header_pick_btn)
        row7.addWidget(self.header_clear_btn)
        row7.addStretch(1)
        r3.addLayout(row7)
        lay.addWidget(look_card)

        # ── 卡片五：工作区与通知（默认收起） ──
        ws_card = CollapsibleCard("工作区与通知", expanded=False)
        r4 = ws_card.body_lay
        r4.addWidget(sub_title("工作区（自动检测新 PDF）"))
        r4.addWidget(note_label("目录里出现新 PDF 时自动：命名检查并改名 → 超过 9.5MB 自动压缩 → 加入批量上传列表"))
        row5 = QHBoxLayout()
        self.ws_edit = LineEdit()
        self.ws_edit.setPlaceholderText("选择工作区目录（自动监听新文件）")
        self.ws_btn = PushButton(FIF.FOLDER, "选择目录")
        self.ws_btn.clicked.connect(self._pick_workspace)
        self.ws_status = note_label("未监听")
        row5.addWidget(self.ws_edit, 1)
        row5.addWidget(self.ws_btn)
        row5.addWidget(self.ws_status)
        r4.addLayout(row5)
        r4.addWidget(sub_title("每日通知与后台自启动"))
        r4.addWidget(note_label("到点通过系统通知推送团员情况；开启自启动后开机即在后台运行"))
        row4 = QHBoxLayout()
        row4.addWidget(BodyLabel("每天"))
        self.notify_time_edit = LineEdit()
        self.notify_time_edit.setFixedWidth(80)
        self.notify_time_edit.setPlaceholderText("08:00")
        self.notify_time_edit.setClearButtonEnabled(False)
        row4.addWidget(self.notify_time_edit)
        row4.addStretch(1)
        self.autostart_btn = PushButton()
        self.autostart_btn.clicked.connect(self._toggle_autostart)
        row4.addWidget(self.autostart_btn)
        r4.addLayout(row4)
        lay.addWidget(ws_card)

        self._watcher = None
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(800)
        self._scan_timer.timeout.connect(self._scan_workspace)

        self._load_config()
        self._refresh_table()

        # 后台定时健康检查：每 15 分钟
        self._timer = QTimer(self)
        self._timer.setInterval(15 * 60 * 1000)
        self._timer.timeout.connect(self.start_health_check)
        if self.accounts:
            self._timer.start()
            self.start_health_check()

    # ---------- 配置持久化 ----------

    def _repair_remarks(self, cfg: dict) -> int:
        """历史遗留修复：备注为空或误填成账号号的账号，按编号规则重新生成备注
        （如 ssfz12601 → 普通高中高一一班）。规则解析不出（编号无效）保持原样。"""
        o = cfg.get("orgRule") or {}
        rule = OrgRule(
            prefixes=o.get("prefixes") or {"1": "普通高中", "2": "国际高中"},
            no_prefix=o.get("noPrefix") or "初中部",
        )
        fixed = 0
        for acc in self.accounts:
            user = acc.get("userName", "")
            remark = (acc.get("remark") or "").strip()
            if remark and remark != user:
                continue
            label = describe_code(user, rule)
            if label and label != remark:
                acc["remark"] = label
                fixed += 1
        if fixed:
            store.update(lambda c, accs=self.accounts: c.__setitem__("accounts", accs))
        return fixed

    def _load_config(self):
        cfg = store.load()
        self.accounts = list(cfg.get("accounts") or [])
        self._repair_remarks(cfg)
        self.active_user = cfg.get("activeUser") or (self.accounts[0]["userName"] if self.accounts else "")
        self.notify_time_edit.setText(store.notify_time(cfg))
        self._refresh_autostart_btn()

        # 名单：分学期（只新建未导入的学期没有文件路径，也要保留）
        self.rosters = {}
        self._roster_paths = {}
        semesters = dict(cfg.get("rosters") or {})
        if cfg.get("rosterPath") and not semesters:
            semesters[guess_semester()] = cfg["rosterPath"]  # 旧版单文件迁移
        for sem, path in semesters.items():
            if path and os.path.isfile(path):
                try:
                    self.rosters[sem] = self._parse_roster(path)
                    self._roster_paths[sem] = path
                except ValueError as e:
                    InfoBar.warning(f"{sem} 名单加载失败", str(e), duration=5000, parent=self)
            else:
                # 无路径（新建未导入）或文件已不在：保留学期，内容为空
                self.rosters.setdefault(sem, Roster(sem))
                if path:
                    self._roster_paths[sem] = path
        self.semester_combo.blockSignals(True)
        self.semester_combo.clear()
        self.semester_combo.addItems(sorted(self.rosters) or [guess_semester()])
        self.semester_combo.setCurrentText(guess_semester())
        self.semester_combo.blockSignals(False)
        self.roster_edit.setText(self._roster_paths.get(self.semester_combo.currentText().strip(), ""))
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
        r = cfg.get("orgRule") or {}
        prefixes = r.get("prefixes") or {"1": "普通高中", "2": "国际高中"}
        self.rule_prefix_edit.setText(OrgRule.prefix_text(prefixes))
        self.rule_noprefix_edit.setText(r.get("noPrefix") or "初中部")
        self._update_rule_preview()

        # 团支书名单状态
        self._refresh_secretary_status()

    # ---------- 团支部编号规则 ----------

    def org_rule(self) -> OrgRule:
        prefixes = OrgRule.parse_prefix_text(self.rule_prefix_edit.text())
        return OrgRule(
            prefixes=prefixes or {"1": "普通高中", "2": "国际高中"},
            no_prefix=self.rule_noprefix_edit.text().strip() or "初中部",
        )

    def _update_rule_preview(self):
        r = self.org_rule()
        a, b = describe_code("12407", r), describe_code("2407", r)
        self.rule_preview.setText(f"预览：12407 → {a or '无法解析'}；2407 → {b or '无法解析'}")

    def _on_rule_changed(self):
        self._save_config()
        self._update_rule_preview()
        if self._filter_label:
            self._clear_filter()  # 规则变了，旧筛选可能失效

    # ---------- 账号筛选（树状：学部 → 年级 → 班级） ----------

    def _show_filter_menu(self):
        """筛选弹窗：fluent TreeWidget 树状（学部 → 年级 → 班级），点班级生效并关闭。"""
        rule = self.org_rule()
        tree: dict[str, dict[str, dict[str, str]]] = {}
        for a in self.accounts:
            info = parse_class_code(a["userName"], rule)
            if info:
                tree.setdefault(info.dept, {}).setdefault(info.grade, {})[str(info.class_no)] = info.label

        dlg = dialogs.FluentPanelDialog(self.window())
        dlg.subtitle = SubtitleLabel("按团支部筛选", dlg.widget)
        dlg.viewLayout.addWidget(dlg.subtitle)

        tw = TreeWidget(dlg.widget)
        tw.setHeaderHidden(True)
        tw.setMinimumHeight(300)

        def _add(text: str, label: str | None, parent_item=None) -> QTreeWidgetItem:
            # 顶层节点必须以 tree 为父构造（自动挂树）；label=None 表示纯展开节点
            item = (QTreeWidgetItem(parent_item, [text]) if parent_item is not None
                    else QTreeWidgetItem(tw, [text]))
            if label is not None:
                item.setData(0, Qt.ItemDataRole.UserRole, label)
            return item

        _add("显示全部", "")  # 顶层；label="" 表示清除筛选
        if not tree:
            empty = _add("（没有能按规则解析的账号）", None)
            empty.setDisabled(True)
        for dept in sorted(tree):
            d = _add(dept, None)
            for grade in sorted(tree[dept]):
                g = _add(grade, None, d)
                for cls in sorted(tree[dept][grade], key=int):
                    _add(f"{cls} 班", tree[dept][grade][cls], g)
        tw.expandAll()
        def _on_clicked(item: QTreeWidgetItem, _col: int):
            label = item.data(0, Qt.ItemDataRole.UserRole)
            if label is None:  # 学部/年级节点：单击切换展开
                item.setExpanded(not item.isExpanded())
                return
            if label == "":
                self._clear_filter()
            else:
                self._apply_filter(label)
            dlg.accept()

        tw.itemClicked.connect(_on_clicked)
        dlg.viewLayout.addWidget(tw)

        dlg.widget.setMinimumWidth(420)
        dlg.yesButton.setText("关闭")
        dlg.hideCancelButton()
        dlg.exec()

    def _apply_filter(self, label: str):
        self._filter_label = label
        self._apply_row_visibility()

    def _clear_filter(self):
        self._filter_label = ""
        self._apply_row_visibility()

    def _apply_row_visibility(self):
        n = 0
        for i, acc in enumerate(self.accounts):
            info = parse_class_code(acc["userName"], self.org_rule())
            match = (not self._filter_label) or (info.label == self._filter_label)
            self.table.setRowHidden(i, not match)
            n += match
        if self._filter_label:
            self.filter_note.setText(f"筛选：{self._filter_label}（{n} 个账号）")
        else:
            self.filter_note.setText("")

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
                "orgRule": {"prefixes": self.org_rule().prefixes, "noPrefix": self.org_rule().no_prefix},
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
                self.roster_table.setItem(i, col, QTableWidgetItem(text))
                self.roster_table.item(i, col).setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.roster_table.blockSignals(False)

    def _delete_selected_roster(self):
        row = self.roster_table.currentRow()
        if row < 0 or row >= self.roster_table.rowCount():
            InfoBar.warning("未选中名单", "先在名单列表中点选一行", duration=3000, parent=self)
            return
        sem = self.roster_table.item(row, 0).text()
        n = len(self.rosters[sem].rows)
        if not dialogs.confirm(self, "删除学期名单",
                               f"删除 {sem} 的名单（{n} 人）？磁盘上的 xlsx 文件不会被删除。"):
            return
        self.rosters.pop(sem, None)
        self._roster_paths.pop(sem, None)
        idx = self.semester_combo.findText(sem)
        if idx >= 0:
            self.semester_combo.removeItem(idx)
        self.roster_edit.setText("")
        self._refresh_roster()
        self._save_config()
        InfoBar.success("已删除", f"{sem} 的名单已从配置移除", duration=3000, parent=self)

    # ---------- 自启动 / 每日通知 ----------

    def _refresh_autostart_btn(self):
        from app.services import autostart

        if autostart.is_installed():
            self.autostart_btn.setText("关闭开机自启动")
        else:
            self.autostart_btn.setText("开启开机自启动（后台静默）")

    def _toggle_autostart(self):
        from app.services import autostart

        if autostart.is_installed():
            autostart.uninstall()
            InfoBar.info("已关闭自启动", "后台托盘不再开机运行", duration=3000, parent=self)
        else:
            if not self.accounts:
                InfoBar.warning("先配置账号", "自启动的后台模式每天要检查团员情况，请先在上方添加账号", duration=4000, parent=self)
                return
            path = autostart.install()
            InfoBar.success("已开启自启动", f"LaunchAgent：{path}", duration=4000, parent=self)
        self._refresh_autostart_btn()

    # ---------- 账号增删 / 去重 / 冲突 ----------

    def _resolve_conflict(self, user: str, existing: dict) -> str:
        if not dialogs.confirm(
            self.window(), "账号密码冲突",
            f"账号 {user} 已存在，但密码与现存的（备注：{existing.get('remark') or '无'}）不一致。\n"
            "点击「覆盖」用新密码替换；点击「保留原密码」跳过这一条。",
            yes="覆盖", cancel="保留原密码",
        ):
            return "keep"
        return "overwrite"

    def _upsert_account(self, user: str, pwd: str, remark: str = "") -> str:
        user = user.strip()
        existing = next((a for a in self.accounts if a["userName"] == user), None)
        if existing:
            if existing["password"] == pwd:
                return "dedup"
            if self._resolve_conflict(user, existing) == "overwrite":
                existing["password"] = pwd
                existing["remark"] = remark or existing.get("remark", "")
                existing["health"] = {"status": "unknown", "message": "", "checkedAt": ""}
                return "overwritten"
            return "kept"
        self.accounts.append({
            "userName": user, "password": pwd, "remark": remark,
            "health": {"status": "unknown", "message": "", "checkedAt": ""},
        })
        return "added"

    def _refresh_all_clicked(self):
        if self.window_ref is None:
            return
        self.window_ref.refresh_all(silent=False)

    def _after_accounts_changed(self):
        self._save_config()
        self._refresh_table()
        self.start_health_check()
        # 新账号进入后自动刷新所有数据（成员缓存、站内信、组织状况）
        if self.window_ref is not None:
            self.window_ref.refresh_all(silent=True)

    def _add_manual(self):
        user, pwd = self.new_user.text().strip(), self.new_pwd.text()
        if not user or not pwd:
            InfoBar.warning("账号或密码未填", "两栏都填好再点添加", duration=2500, parent=self)
            return
        year = self.new_year.text().strip()
        if year:
            from app.domain.transfer import parse_graduate_year

            if parse_graduate_year(year) is None:
                InfoBar.warning("届别无效", "届别请填毕业年份（如 2023），或留空", duration=3000, parent=self)
                return
        remark = self.new_remark.text().strip() or describe_code(user, self.org_rule())
        result = self._upsert_account(user, pwd, remark)
        if result != "kept":
            acc = next(a for a in self.accounts if a["userName"] == user)
            acc["graduateYear"] = year
        self.new_user.clear()
        self.new_pwd.clear()
        self.new_remark.clear()
        self.new_year.clear()
        self._after_accounts_changed()
        if result == "dedup":
            InfoBar.info("已去重", f"账号 {user} 同密码已存在，自动跳过", duration=3000, parent=self)
        elif result == "kept":
            InfoBar.info("已保留", f"账号 {user} 保留了原密码", duration=3000, parent=self)

    def _import_accounts(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择账号文件", "", "表格或文本 (*.xlsx *.xlsm *.txt *.csv)")
        if not path:
            return
        rows = self._read_account_file(path)
        if not rows:
            InfoBar.warning("没有读到账号",
                            "每行应为：账号 密码（备注、届别可空）；xlsx 需有「账号」「密码」列",
                            duration=4000, parent=self)
            return
        rule = self.org_rule()
        stat = {"added": 0, "dedup": 0, "overwritten": 0, "kept": 0}
        prefilled = 0
        bad_years: list[str] = []
        for account, pwd, remark, year in rows:
            if not remark or remark.strip() == account:
                # 备注留空或误填成账号号 → 按编号规则预填（如 ssfz12601 → 普通高中高一一班）
                remark = describe_code(account, rule)
                prefilled += 1 if remark else 0
            stat[self._upsert_account(account, pwd, remark)] += 1
            if year:
                from app.domain.transfer import parse_graduate_year

                if parse_graduate_year(year) is None:
                    bad_years.append(account)
                    continue
                acc = next(a for a in self.accounts if a["userName"] == account)
                acc["graduateYear"] = year
        self._after_accounts_changed()
        msg = f"新增 {stat['added']}，去重跳过 {stat['dedup']}，覆盖 {stat['overwritten']}，冲突保留 {stat['kept']}"
        if prefilled:
            msg += f"；按编号规则预填备注 {prefilled} 个"
        if bad_years:
            msg += f"；届别无效未设置：{'、'.join(bad_years[:5])}"
        InfoBar.success("导入完成", msg, duration=5000, parent=self)

    def _read_account_file(self, path: str) -> list[tuple[str, str, str, str]]:
        """读账号文件 → (账号, 密码, 备注, 届别)。

        xlsx 表头格式：「账号」「密码」必填，「备注」「届别」可选；
        旧格式（备注、账号、密码三列，或纯文本行）兼容识别；
        备注留空的行按编号规则在导入时预填。"""
        import re as _re

        from app.domain.org_rule import parse_account_row

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
                    parsed = parse_account_row([c for c in raw])
                    if parsed:
                        rows.append((*parsed, ""))
        else:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    parsed = parse_account_row(_re.split(r"[,;\s]+", line.strip()))
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
        InfoBar.success("模板已保存", f"{path}（{n} 行示例，可直接改后批量导入）", duration=5000, parent=self)

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
            InfoBar.warning("未选择", "勾选要删除的账号，或点行内的删除按钮", duration=2500, parent=self)
            return
        if not dialogs.confirm(
            self.window(), "删除账号",
            f"确定从列表删除 {'、'.join(users[:5])}{'等' if len(users) > 5 else ''}，共 {len(users)} 个？（不影响站点数据）",
            yes="删除", cancel="取消",
        ):
            return
        for r in sorted(rows, reverse=True):
            if 0 <= r < len(self.accounts):
                self.accounts.pop(r)
        if self.active_user not in [a["userName"] for a in self.accounts]:
            self.active_user = self.accounts[0]["userName"] if self.accounts else ""
        self._after_accounts_changed()
        InfoBar.success("已删除", f"移除 {len(users)} 个账号", duration=3000, parent=self)

    def _toggle_select_all(self):
        """有未勾选的就全选，否则全部取消（只动可见行，被筛选隐藏的行不碰）。"""
        rows = [i for i in range(self.table.rowCount()) if not self.table.isRowHidden(i)]
        any_unchecked = any(
            self.table.item(i, 0) and self.table.item(i, 0).checkState() != Qt.CheckState.Checked
            for i in rows
        )
        state = Qt.CheckState.Checked if any_unchecked else Qt.CheckState.Unchecked
        for i in rows:
            if self.table.item(i, 0):
                self.table.item(i, 0).setCheckState(state)

    def _delete_checked(self):
        rows = [i for i in range(self.table.rowCount())
                if not self.table.isRowHidden(i)
                and self.table.item(i, 0) and self.table.item(i, 0).checkState() == Qt.CheckState.Checked]
        self._remove_accounts(rows)

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
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.accounts))
        for i, acc in enumerate(self.accounts):
            # 勾选框列
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, chk)
            # 可编辑列：账号 / 密码 / 备注 / 届别
            for col, key in self.EDITABLE_COLS.items():
                self.table.setItem(i, col, QTableWidgetItem(str(acc.get(key, "") or "")))
            # 状态列：按届别自动判定毕业应转出
            graduated = is_graduated(acc)
            gy = acc.get("graduateYear")
            status_text = f"{gy} 届 · 毕业应转出" if graduated else ("在读" if gy else "—")
            self.table.setItem(i, 5, QTableWidgetItem(status_text))
            self.table.item(i, 5).setFlags(self.READONLY_FLAGS)
            if graduated:
                self.table.item(i, 5).setForeground(QColor("#ef6c00"))
            h = acc.get("health") or {}
            status, msg = h.get("status", "unknown"), h.get("message", "")
            label = HEALTH_LABEL.get(status, status) + (f"：{msg}" if msg and status != "ok" else "")
            self.table.setItem(i, 6, QTableWidgetItem(label))
            self.table.item(i, 6).setFlags(self.READONLY_FLAGS)
            self.table.item(i, 6).setForeground(QColor(HEALTH_COLOR.get(status, "#9e9e9e")))
            checked = h.get("checkedAt", "")
            self.table.setItem(i, 7, QTableWidgetItem(checked.replace("T", " ")[:19] if checked else "—"))
            self.table.item(i, 7).setFlags(self.READONLY_FLAGS)
        self.table.blockSignals(False)
        self._apply_row_visibility()  # 应用当前团支部筛选
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
                InfoBar.warning("届别无效", "届别请填毕业年份（如 2023），或留空表示在读未定", duration=3000, parent=self)
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
            self.start_health_check()

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

    def start_health_check(self):
        if not self.accounts:
            return
        if self._worker and self._worker.isRunning():
            return
        self.health_btn.setEnabled(False)
        self._worker = HealthWorker([dict(a) for a in self.accounts])
        self._worker.account_checked.connect(self._on_account_checked)
        self._worker.finished.connect(lambda: self.health_btn.setEnabled(True))
        self._worker.start()

    # ---------- 浏览器打开（携带登录态） ----------

    def _open_web(self):
        """优先打开勾选行的第一个账号，否则打开当前使用账号。"""
        if not self.accounts:
            InfoBar.warning("没有账号", "请先添加团支部账号", duration=2500, parent=self)
            return
        if self._web_worker and self._web_worker.isRunning():
            return
        checked = [i for i in range(self.table.rowCount())
                   if not self.table.isRowHidden(i)
                   and self.table.item(i, 0) and self.table.item(i, 0).checkState() == Qt.CheckState.Checked]
        idx = checked[0] if checked else next(
            (i for i, a in enumerate(self.accounts) if a["userName"] == self.active_user), 0)
        if not (0 <= idx < len(self.accounts)):
            return
        acc = self.accounts[idx]
        self.web_btn.setEnabled(False)
        self._web_worker = WebOpenWorker(acc["userName"], acc["password"], self)
        self._web_worker.done.connect(self._on_web_opened)
        self._web_worker.start()

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
        self._save_config()
        self._refresh_table()

    # ---------- 名单（分学期 + 模板） ----------

    def _parse_roster(self, path: str) -> Roster:
        from app.services.excel import import_roster

        return import_roster(path)

    def _import_roster(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择名单", "", "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        sem = self.semester_combo.currentText().strip()
        if not sem:
            InfoBar.warning("学期为空", "在学期框选择已有学期，或直接输入新学期名（如 2027春）", duration=3000, parent=self)
            return
        try:
            self.rosters[sem] = self._parse_roster(path)
        except Exception as e:
            InfoBar.error("名单导入失败", str(e), duration=5000, parent=self)
            return
        self._roster_paths[sem] = path
        # 新学期名自动加入下拉列表（建立学期）
        items = [self.semester_combo.itemText(i) for i in range(self.semester_combo.count())]
        if sem not in items:
            self.semester_combo.addItem(sem)
        self.semester_combo.setCurrentText(sem)
        self.roster_edit.setText(path)
        self._refresh_roster()
        self._save_config()
        InfoBar.success(f"已导入 {sem}", f"{len(self.rosters[sem].rows)} 名团员", duration=3000, parent=self)

    def _on_semester_changed(self, sem: str):
        """切换学期时回显该学期已导入的名单文件。"""
        self.roster_edit.setText(self._roster_paths.get(sem.strip(), ""))

    def _create_semester(self):
        """按学期框里的名字新建空学期（不用先导入名单）。"""
        sem = self.semester_combo.currentText().strip()
        if not sem:
            InfoBar.warning("学期为空", "在学期框输入学期名（如 2027春）再点新建", duration=3000, parent=self)
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
        self.roster_edit.setText("")
        self._refresh_roster()
        self._save_config()
        InfoBar.success("学期已新建", f"{sem}：选择名单文件后导入到该学期", duration=3000, parent=self)

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
        InfoBar.info("头图已清除", "工具首页恢复占位渐变", duration=2500, parent=self)

    # ---------- 外观（明暗模式 + 毛玻璃） ----------

    def _on_theme_changed(self, index: int):
        from qfluentwidgets import InfoBar

        from app.setup import apply_theme_mode

        mode = ("auto", "light", "dark")[index]
        apply_theme_mode(mode)
        store.update(lambda cfg: cfg.__setitem__("themeMode", mode))
        if self.window_ref is not None:
            self.window_ref.on_theme_changed()
        InfoBar.success("明暗模式已切换", ("跟随系统" if index == 0 else "已切换，界面即时生效"),
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
        InfoBar.success("团支书名单已导入", f"{len(rows)} 个支部（在「组织管理」展示）", duration=4000, parent=self)
        if self.window_ref is not None:
            self.window_ref.org_manage_page.refresh()

    def _refresh_secretary_status(self, rows: list | None = None):
        rows = store.load().get("secretaries", []) if rows is None else rows
        self.sec_status.setText(f"已登记 {len(rows)} 个支部" if rows else "未导入")

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
