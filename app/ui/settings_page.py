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
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QTableWidgetItem, QWidget, QMenu

from concurrent.futures import ThreadPoolExecutor, as_completed

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    EditableComboBox,
    FluentIcon as FIF,
    InfoBar,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TableWidget,
    ToolButton,
)

from app.ui.page import FluentPage, note_label
from app.domain.naming import Roster
from app.domain.org_rule import OrgRule, describe_code, parse_class_code
from app.domain.transfer import is_graduated
from app.services import store
from app.services.api import SiteClient

HEALTH_LABEL = {
    "ok": "正常",
    "failed": "密码错误",
    "captcha": "需验证码（先去网页端登录一次）",
    "passwordChange": "需改密（EXPIREDPASS/WEAKPASS）",
    "needMemberName": "需姓名校验",
    "network": "网络错误",
    "unknown": "未检测",
}
HEALTH_COLOR = {
    "ok": "#2e7d32",
    "failed": "#c62828",
    "captcha": "#ef6c00",
    "passwordChange": "#ef6c00",
    "needMemberName": "#ef6c00",
    "network": "#9e9e9e",
    "unknown": "#9e9e9e",
}


def guess_semester() -> str:
    now = datetime.now()
    return f"{now.year}秋" if now.month >= 7 else f"{now.year}春"


class HealthWorker(QThread):
    """并发测活：每个账号独立线程探测，互不影响，结果完成一个回报一个。"""

    account_checked = Signal(str, str, str)  # userName, status, message

    def __init__(self, accounts: list[dict]):
        super().__init__()
        self.accounts = accounts

    def _check_one(self, acc: dict) -> tuple[str, str, str]:
        user, pwd = acc.get("userName", ""), acc.get("password", "")
        if not user or not pwd:
            return user, "unknown", "账号或密码为空"
        try:
            client = SiteClient(user, pwd)
            probe = client.session_account()
            if probe.ok:
                return user, "ok", probe.message or "会话有效"
            verdict = client.login()
            mapping = {
                "ok": "ok",
                "failed": "failed",
                "captcha": "captcha",
                "passwordChange": "passwordChange",
                "needMemberName": "needMemberName",
            }
            kind = str((verdict.data or {}).get("kind", "failed")) if isinstance(verdict.data, dict) else "failed"
            return user, mapping.get(kind, "failed"), verdict.message
        except Exception as e:
            return user, "network", str(e)

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
        self._filter_label = ""  # 当前团支部筛选（完整备注名，空 = 不筛选）

        lay = self.vBoxLayout

        # 当前使用账号
        top = CardWidget()
        top_lay = QVBoxLayout(top)
        top_lay.setContentsMargins(20, 16, 20, 16)
        top_lay.addWidget(StrongBodyLabel("当前使用的团支部账号"))
        top_lay.addWidget(note_label("测活先只读探测会话，会话有效不触发登录，避免误增密码错误次数"))
        top_lay.addSpacing(4)
        row0 = QHBoxLayout()
        self.active_combo = ComboBox()
        self.active_combo.currentTextChanged.connect(self._on_active_changed)
        self.health_btn = PushButton(FIF.SYNC, "全部测活")
        self.health_btn.clicked.connect(self.start_health_check)
        row0.addWidget(self.active_combo, 1)
        row0.addWidget(self.health_btn)
        top_lay.addLayout(row0)
        lay.addWidget(top)

        # 表格上方工具行：图标按钮（无文字）
        tools = QHBoxLayout()
        self.sel_all_btn = ToolButton(FIF.CHECKBOX)
        self.sel_all_btn.setToolTip("全选 / 取消全选（仅可见行）")
        self.sel_all_btn.clicked.connect(self._toggle_select_all)
        self.del_checked_btn = ToolButton(FIF.DELETE)
        self.del_checked_btn.setToolTip("删除勾选账号")
        self.del_checked_btn.clicked.connect(self._delete_checked)
        self.filter_btn = ToolButton(FIF.FILTER)
        self.filter_btn.setToolTip("按团支部筛选（树状：学部 → 年级 → 班级）")
        self.filter_btn.clicked.connect(self._show_filter_menu)
        self.recheck_btn = ToolButton(FIF.SYNC)
        self.recheck_btn.setToolTip("全部测活")
        self.recheck_btn.clicked.connect(self.start_health_check)
        self.web_btn = ToolButton(FIF.GLOBE)
        self.web_btn.setToolTip("在浏览器打开当前账号的网页端（携带登录态）")
        self.web_btn.clicked.connect(self._open_web)
        tools.addWidget(self.sel_all_btn)
        tools.addWidget(self.del_checked_btn)
        tools.addWidget(self.filter_btn)
        tools.addWidget(self.recheck_btn)
        tools.addWidget(self.web_btn)
        self.filter_note = note_label("")
        tools.addSpacing(8)
        tools.addWidget(self.filter_note)
        tools.addStretch(1)
        lay.addLayout(tools)

        # 账号表：勾选框 + 账号 + 密码 + 备注 + 届别 + 状态 + 健康状态 + 最近检查
        # 账号/密码/备注/届别可编辑，状态按届别自动算（毕业应转出），其余只读
        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(["", "账号", "密码", "备注", "届别", "状态", "健康状态", "最近检查"])
        self.table.setColumnWidth(0, 32)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in (1, 2, 3, 4, 5, 6):  # 内容自适应，保证完整展示
            header.setSectionResizeMode(col, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, header.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        self.add_table(self.table, 2)

        # 添加账号
        add = CardWidget()
        add_lay = QVBoxLayout(add)
        add_lay.setContentsMargins(20, 16, 20, 16)
        add_lay.addWidget(StrongBodyLabel("添加 / 导入账号"))
        add_lay.addWidget(note_label("同账号同密码自动去重；同名账号密码不同时会询问如何处理"))
        add_lay.addSpacing(4)
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
        add_lay.addLayout(row1)
        row2 = QHBoxLayout()
        self.import_btn = PushButton(FIF.DOCUMENT, "批量导入")
        self.import_btn.clicked.connect(self._import_accounts)
        row2.addWidget(self.import_btn)
        row2.addStretch(1)
        add_lay.addLayout(row2)
        lay.addWidget(add)

        # 名单卡片（分学期）
        ros = CardWidget()
        ros_lay = QVBoxLayout(ros)
        ros_lay.setContentsMargins(20, 16, 20, 16)
        title_row = QHBoxLayout()
        title_row.addWidget(StrongBodyLabel("预设团员名单"))
        self.roster_status = note_label("未导入")
        title_row.addSpacing(8)
        title_row.addWidget(self.roster_status)
        title_row.addStretch(1)
        self.template_btn = PushButton(FIF.DOWNLOAD, "下载名单模板")
        self.template_btn.clicked.connect(self._download_template)
        title_row.addWidget(self.template_btn)
        ros_lay.addLayout(title_row)
        ros_lay.addWidget(note_label("xlsx 列：姓名 / 手机号码 / 班级（团支部编号，如 12407）；学期框可下拉选择，也可直接输入新学期名（如 2027春）建立，导入后按班级自动分流"))
        ros_lay.addSpacing(4)
        row3 = QHBoxLayout()
        self.semester_combo = EditableComboBox()
        self.semester_combo.addItems([guess_semester()])
        self.semester_combo.setCurrentText(guess_semester())
        self.semester_combo.setPlaceholderText("选择或输入学期（如 2027春）")
        self.semester_combo.currentTextChanged.connect(self._on_semester_changed)
        self.roster_edit = LineEdit()
        self.roster_edit.setPlaceholderText("选择名单文件")
        self.roster_btn = PushButton("导入到该学期")
        self.roster_btn.clicked.connect(self._import_roster)
        row3.addWidget(BodyLabel("学期"))
        row3.addWidget(self.semester_combo)
        row3.addWidget(self.roster_edit, 1)
        row3.addWidget(self.roster_btn)
        ros_lay.addLayout(row3)
        lay.addWidget(ros)

        # 团支部编号规则卡（备注预填 + 筛选树共用）
        rule_card = CardWidget()
        rule_lay = QVBoxLayout(rule_card)
        rule_lay.setContentsMargins(20, 16, 20, 16)
        rule_lay.addWidget(StrongBodyLabel("团支部编号规则"))
        rule_lay.addWidget(note_label(
            "编号 = 学部前缀（1 位）+ 入学年份后两位（2 位）+ 班号（2 位）；无前缀的 4 位编号归「无前缀学部」。"
            "年级按当前学年自动推算（如 24 级今年是高三/初三）。用于导入账号时预填备注和账号筛选"))
        rule_lay.addSpacing(4)
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
        rule_lay.addLayout(row_rule)
        self.rule_preview = note_label("")
        rule_lay.addWidget(self.rule_preview)
        self.rule_prefix_edit.editingFinished.connect(self._on_rule_changed)
        self.rule_noprefix_edit.editingFinished.connect(self._on_rule_changed)
        lay.addWidget(rule_card)

        # 文档检查配置（命名 + 总页数）
        doc = CardWidget()
        doc_lay = QVBoxLayout(doc)
        doc_lay.setContentsMargins(20, 16, 20, 16)
        doc_lay.addWidget(StrongBodyLabel("文档检查（入团志愿书 PDF）"))
        doc_lay.addWidget(note_label("「文档检查」页除命名外，还核对 PDF 总页数是否等于预期页数；留空则不核对页数"))
        doc_lay.addSpacing(4)
        row6 = QHBoxLayout()
        row6.addWidget(BodyLabel("预期页数"))
        self.expected_pages_edit = LineEdit()
        self.expected_pages_edit.setPlaceholderText("如 4；留空则不核对页数")
        self.expected_pages_edit.setFixedWidth(160)
        self.expected_pages_edit.editingFinished.connect(self._save_config)
        row6.addWidget(self.expected_pages_edit)
        row6.addStretch(1)
        doc_lay.addLayout(row6)
        lay.addWidget(doc)

        # 首页头图
        header = CardWidget()
        header_lay = QVBoxLayout(header)
        header_lay.setContentsMargins(20, 16, 20, 16)
        header_lay.addWidget(StrongBodyLabel("首页头图"))
        header_lay.addWidget(note_label("图片会铺满工具首页顶部并自然过渡到背景；清除后显示占位渐变"))
        header_lay.addSpacing(4)
        row7 = QHBoxLayout()
        self.header_pick_btn = PushButton(FIF.PHOTO, "选择图片")
        self.header_pick_btn.clicked.connect(self._pick_header_image)
        self.header_clear_btn = PushButton(FIF.DELETE, "清除头图")
        self.header_clear_btn.clicked.connect(self._clear_header_image)
        row7.addWidget(self.header_pick_btn)
        row7.addWidget(self.header_clear_btn)
        row7.addStretch(1)
        header_lay.addLayout(row7)
        lay.addWidget(header)

        # 工作区卡片
        ws = CardWidget()
        ws_lay = QVBoxLayout(ws)
        ws_lay.setContentsMargins(20, 16, 20, 16)
        ws_lay.addWidget(StrongBodyLabel("工作区（自动检测新 PDF）"))
        ws_lay.addWidget(note_label("目录里出现新 PDF 时自动：命名检查并改名 → 超过 9.5MB 自动压缩 → 加入批量上传列表"))
        ws_lay.addSpacing(4)
        row5 = QHBoxLayout()
        self.ws_edit = LineEdit()
        self.ws_edit.setPlaceholderText("选择工作区目录（自动监听新文件）")
        self.ws_btn = PushButton(FIF.FOLDER, "选择目录")
        self.ws_btn.clicked.connect(self._pick_workspace)
        self.ws_status = note_label("未监听")
        row5.addWidget(self.ws_edit, 1)
        row5.addWidget(self.ws_btn)
        row5.addWidget(self.ws_status)
        ws_lay.addLayout(row5)
        lay.addWidget(ws)

        # 每日通知 + 自启动卡片
        daily = CardWidget()
        daily_lay = QVBoxLayout(daily)
        daily_lay.setContentsMargins(20, 16, 20, 16)
        daily_lay.addWidget(StrongBodyLabel("每日通知与后台自启动"))
        daily_lay.addWidget(note_label("到点通过系统通知推送团员情况；开启自启动后开机即在后台运行"))
        daily_lay.addSpacing(4)
        row4 = QHBoxLayout()
        row4.addWidget(BodyLabel("每天"))
        self.notify_time_edit = LineEdit()
        self.notify_time_edit.setFixedWidth(80)
        self.notify_time_edit.setPlaceholderText("08:00")
        self.notify_time_edit.setClearButtonEnabled(False)
        row4.addWidget(self.notify_time_edit)
        row4.addSpacing(6)
        row4.addStretch(1)
        self.autostart_btn = PushButton()
        self.autostart_btn.clicked.connect(self._toggle_autostart)
        row4.addWidget(self.autostart_btn)
        daily_lay.addLayout(row4)
        lay.addWidget(daily)

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

    def _load_config(self):
        cfg = store.load()
        self.accounts = list(cfg.get("accounts") or [])
        self.active_user = cfg.get("activeUser") or (self.accounts[0]["userName"] if self.accounts else "")
        self.notify_time_edit.setText(store.notify_time(cfg))
        self._refresh_autostart_btn()

        # 名单：分学期
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
                    self.roster_status.setText(f"{sem} 名单加载失败：{e}")
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
        rule = self.org_rule()
        tree: dict[str, dict[str, dict[str, str]]] = {}
        for a in self.accounts:
            info = parse_class_code(a["userName"], rule)
            if info:
                tree.setdefault(info.dept, {}).setdefault(info.grade, {})[str(info.class_no)] = info.label
        menu = QMenu(self)
        menu.addAction("显示全部").triggered.connect(self._clear_filter)
        if not tree:
            menu.addAction("（没有能按规则解析的账号）").setEnabled(False)
        for dept in sorted(tree):
            sub = menu.addMenu(dept)
            for grade in sorted(tree[dept]):
                sub2 = sub.addMenu(grade)
                for cls in sorted(tree[dept][grade], key=int):
                    label = tree[dept][grade][cls]
                    sub2.addAction(f"{cls} 班").triggered.connect(
                        lambda checked=False, lb=label: self._apply_filter(lb))
        menu.exec(self.filter_btn.mapToGlobal(self.filter_btn.rect().bottomLeft()))

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
        cfg = store.load()
        cfg.update({
            "activeUser": self.active_user,
            "accounts": self.accounts,
            "rosters": dict(self._roster_paths),
            "workspaceDir": self.ws_edit.text().strip(),
            "notifyTime": self.notify_time_edit.text().strip() or store.DEFAULT_NOTIFY_TIME,
            "expectedPages": self.expected_pages(),
            "orgRule": {"prefixes": self.org_rule().prefixes, "noPrefix": self.org_rule().no_prefix},
        })
        store.save(cfg)
        self._client = None

    def _refresh_roster(self):
        """合并各学期名单为总表（供检查/上传使用），刷新标题旁状态小字。"""
        merged = Roster("combined")
        for sem in sorted(self.rosters):
            merged.rows.extend(self.rosters[sem].rows)
        self.roster = merged if merged.rows else None
        if self.rosters:
            parts = [f"{sem} {len(r.rows)} 人" for sem, r in sorted(self.rosters.items())]
            self.roster_status.setText(f"已导入 {len(merged.rows)} 人（{' / '.join(parts)}）")
        else:
            self.roster_status.setText("未导入")
        self.roster_changed.emit(self.roster)

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
        box = MessageBox(
            "账号密码冲突",
            f"账号 {user} 已存在，但密码与现存的（备注：{existing.get('remark') or '无'}）不一致。\n"
            "点击「覆盖」用新密码替换；点击「保留原密码」跳过这一条。",
            self.window(),
        )
        box.yesButton.setText("覆盖")
        box.cancelButton.setText("保留原密码")
        box.exec()
        return "overwrite" if box.result() else "keep"

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

    def _after_accounts_changed(self):
        self._save_config()
        self._refresh_table()
        self.start_health_check()

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
            InfoBar.warning("没有读到账号", "每行应为：备注（可空） 账号 密码；xlsx 需有「账号」「密码」列", duration=4000, parent=self)
            return
        rule = self.org_rule()
        stat = {"added": 0, "dedup": 0, "overwritten": 0, "kept": 0}
        prefilled = 0
        for account, pwd, remark in rows:
            if not remark:
                remark = describe_code(account, rule)  # 备注留空 → 按编号规则预填
                prefilled += 1 if remark else 0
            stat[self._upsert_account(account, pwd, remark)] += 1
        self._after_accounts_changed()
        msg = f"新增 {stat['added']}，去重跳过 {stat['dedup']}，覆盖 {stat['overwritten']}，冲突保留 {stat['kept']}"
        if prefilled:
            msg += f"；按编号规则预填备注 {prefilled} 个"
        InfoBar.success("导入完成", msg, duration=5000, parent=self)

    def _read_account_file(self, path: str) -> list[tuple[str, str, str]]:
        """读账号文件 → (账号, 密码, 备注)。新格式：备注第一列、账号第二列、密码第三列；
        旧格式（账号、密码、备注）兼容识别；备注留空的行按编号规则在导入时预填。"""
        import re as _re

        from app.domain.org_rule import parse_account_row

        rows: list[tuple[str, str, str]] = []
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
                for raw in data[1:]:
                    if not raw:
                        continue

                    def cell(i, raw=raw):
                        return str(raw[i]).strip() if i is not None and i < len(raw) and raw[i] is not None else ""

                    if cell(u) and cell(p):
                        rows.append((cell(u), cell(p), cell(r)))
            else:
                for raw in data:
                    if not raw:
                        continue
                    parsed = parse_account_row([c for c in raw])
                    if parsed:
                        rows.append(parsed)
        else:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    parsed = parse_account_row(_re.split(r"[,;\s]+", line.strip()))
                    if parsed:
                        rows.append(parsed)
        return rows

    # ---------- 账号删除（行内按钮 + 勾选批量） ----------

    def _remove_accounts(self, rows: list[int]):
        users = [self.accounts[r]["userName"] for r in rows if 0 <= r < len(self.accounts)]
        if not users:
            InfoBar.warning("未选择", "勾选要删除的账号，或点行内的删除按钮", duration=2500, parent=self)
            return
        box = MessageBox(
            "删除账号",
            f"确定从列表删除 {'、'.join(users[:5])}{'等' if len(users) > 5 else ''}，共 {len(users)} 个？（不影响站点数据）",
            self.window(),
        )
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        box.exec()
        if not box.result():
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
        cfg = store.load()
        cfg["headerImage"] = path
        store.save(cfg)
        if self.home_page:
            self.home_page.banner.set_image_path(path)
        InfoBar.success("头图已设置", os.path.basename(path), duration=2500, parent=self)

    def _clear_header_image(self):
        cfg = store.load()
        cfg["headerImage"] = ""
        store.save(cfg)
        if self.home_page:
            self.home_page.banner.set_image_path("")
        InfoBar.info("头图已清除", "工具首页恢复占位渐变", duration=2500, parent=self)

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
