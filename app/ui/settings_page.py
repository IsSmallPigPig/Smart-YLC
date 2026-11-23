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
from app.ui.settings_config_mixin import SettingsConfigMixin
from app.ui.settings_account_mixin import SettingsAccountMixin
from app.ui.settings_health_mixin import SettingsHealthMixin
from app.ui.settings_data_mixin import SettingsDataMixin, WorkspaceWorker

# 表格里用的短标签：完整说明（含处理办法）放 tooltip
HEALTH_SHORT = {"captcha": "需验证码", "cooldown": "登录冷却"}
from app.services.api import SiteClient

THEME_INDEX = {"auto": 0, "light": 1, "dark": 2}
MATERIAL_ORDER = ("none", "acrylic", "mica")   # 下拉框顺序，与 _material_index 一致


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

    def __init__(self, accounts: list[dict], parent=None):
        super().__init__(parent)   # 挂页面：页面析构时 Qt 会等线程结束，
        self.accounts = accounts   # 无父线程被 GC 时直接 abort
        self._stop = False

    def stop(self):
        """浮条上点关闭：不再往下测，已拿到的结果照常落盘。"""
        self._stop = True

    def _cancelled(self) -> bool:
        """用户取消，或页面正在析构（Qt 对带父线程会 requestInterruption）。"""
        return self._stop or self.isInterruptionRequested()

    def _check_one(self, acc: dict) -> tuple[str, str, str]:
        from app.services import login_gate

        user, pwd = acc.get("userName", ""), acc.get("password", "")
        if not user or not pwd:
            return user, "unknown", "账号或密码为空"
        if self._cancelled():
            return user, "unknown", "已取消"
        try:
            kind, message = login_gate.ensure_logged_in(SiteClient(user, pwd))
            return user, self.KIND_MAP.get(kind, "failed"), message
        except Exception as e:
            return user, "network", f"测活线程异常：{e}"

    def run(self):
        workers = min(8, max(1, len(self.accounts)))
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = [pool.submit(self._check_one, dict(a)) for a in self.accounts]
            for fut in as_completed(futures):
                if self._cancelled():
                    break
                try:
                    self.account_checked.emit(*fut.result())
                except Exception as e:  # 单账号线程异常不影响其余结果
                    self.account_checked.emit("", "network", f"测活线程异常：{e}")
        finally:
            # 取消（用户关浮条 / 页面要析构）时不能陪着剩下的请求走完超时：
            # with 块的隐式 shutdown(wait=True) 会把整批账号测完才退出，
            # PyQt5 下页面先被销毁、运行中的 QThread 跟着析构，直接 abort。
            # 已派发的请求最多挂到一个 20s 超时；析构路径上 Qt 等的就是它。
            pool.shutdown(wait=not self._cancelled(), cancel_futures=self._cancelled())




class SettingsPage(SettingsConfigMixin, SettingsAccountMixin,
                   SettingsHealthMixin, SettingsDataMixin, FluentPage):
    roster_changed = Signal(object)
    workspace_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page-settings")
        # 顶部留白让出头图位置（头图通栏贴顶、覆盖在内容之上），其余三边与卡片对齐
        self.vBoxLayout.setContentsMargins(32, PageBanner.HEIGHT + 16, 32, 24)
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
        # 配置载入完成前禁止回写：构造期控件还是半初始化状态，self.accounts 也还不存在，
        # 这时整份写回会把磁盘上的账号/名单/工作区抹成空值（见 _save_config）
        self._loading = True
        self._loaded = False

        lay = self.vBoxLayout
        # 头图通栏贴顶（overlay，不占布局），标题与说明叠在 banner 上；
        # 内容用顶部留白下推到头图之下。系统配置里改头图时由 _pick_header_image 统一刷新
        self.header = PageBanner("系统配置", "管理团支部账号、名单、通知与外观", self.scrollWidget)
        self.banner = self.header
        self.header.set_image_path(store.load().get("headerImage", ""))
        self._place_banner()

        def group(title: str, expanded: bool, desc: str = "") -> CollapsibleCard:
            card = CollapsibleCard(title, expanded=expanded, desc=desc)
            lay.addWidget(card)
            return card

        def row(card: CollapsibleCard, widget, *, last: bool = False):
            widget.set_separator(not last)
            card.body_lay.addWidget(widget)

        def controls(*widgets, spacing: int = 8, uniform: bool = True) -> QWidget:
            """一行里的控件统一成同一个宽度（`CONTROL_WIDTH`），右边缘才对得齐。

            按钮、下拉按钮、下拉框、输入框的实际宽度本来各不相同（106 / 125 / 76），
            按内容取宽会让每行的控件参差；这里统一按四字下拉按钮的宽度定死。
            uniform=False 用于「容器里已经排好了自启两个按钮」这种，只限总宽。"""
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(spacing)
            for widget in widgets:
                widget.setMinimumWidth(0)
                widget.setMaximumWidth(16777215)  # 解掉单控件上的 setFixedWidth
                if uniform:
                    widget.setFixedWidth(CONTROL_WIDTH)
                h.addWidget(widget, 0)
            box.setMaximumWidth(CONTROL_MAX_WIDTH)
            return box

        # ── 支部账号 ──
        acc_card = group("支部账号", True, "账号、登录状态与同步")
        self.acc_card = acc_card  # 供通知跳转时展开并定位
        self.active_combo = ComboBox()
        self.active_combo.setPlaceholderText("尚未添加")
        self.active_combo.currentTextChanged.connect(self._on_active_changed)
        self.web_btn = PushButton(FIF.GLOBE, "打开网页")
        set_tip(self.web_btn, "在独立窗口打开所选账号的网页端")
        self.web_btn.clicked.connect(self._open_web)
        row(acc_card, SettingRow(
            "当前账号", desc="网页端用哪个支部登录", icon=FIF.PEOPLE,
            control=controls(self.active_combo, self.web_btn)))

        self.health_btn = PushButton(FIF.HEART, "全部测活")
        set_tip(self.health_btn, "查一遍所有账号的登录状态")
        self.health_btn.clicked.connect(lambda: self.start_health_check(force=True))
        self.refresh_all_btn = PrimaryPushButton(FIF.CLOUD_DOWNLOAD, "同步数据")
        set_tip(self.refresh_all_btn, "刷新成员缓存、站内信与组织状况")
        self.refresh_all_btn.clicked.connect(self._refresh_all_clicked)
        row(acc_card, SettingRow("登录状态", desc="测活并同步全部账号",
                                 icon=FIF.HEART,
                                 control=controls(self.refresh_all_btn, self.health_btn)))

        self.del_checked_btn = PushButton(FIF.DELETE, "删除勾选")
        set_tip(self.del_checked_btn, "删除勾选的账号")
        self.del_checked_btn.clicked.connect(self._delete_checked)
        self.refresh_btn = PushButton(FIF.UPDATE, "刷新所选")
        set_tip(self.refresh_btn, "刷新选中的账号")
        self.refresh_btn.clicked.connect(self._refresh_selected)

        # 账号表：勾选框 + 账号 + 密码 + 备注 + 届别 + 状态 + 健康状态 + 最近检查
        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(P.RADIUS["card"])  # 与卡片、窗口圆角同心
        # 隐藏左侧行号列：表格自带 1、2、3… 的序号，和勾选框并排太挤且没有信息量
        self.table.verticalHeader().hide()
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
        self._bind_account_table_menu()
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
        row(acc_card, SettingRow("添加账号", desc="登录站点后自动读支部名",
                                 icon=FIF.ADD,
                                 control=controls(self.add_open_btn)))

        # 账号表格导入只管账号本身；系统备份（整份设置）单独放到「系统备份」卡片，
        # 免得它挂在「账号文件」下面让人误以为只备份支部账号。
        self.file_btn = DropDownPushButton(FIF.LIBRARY, "账号表格")
        set_tip(self.file_btn, "导入文件或下载模版")
        self.file_menu = RoundMenu(parent=self.file_btn)
        act_sheet = Action(FIF.DOCUMENT, "导入文件")
        act_sheet.triggered.connect(self._import_accounts)
        act_template = Action(FIF.DOWNLOAD, "下载模版")
        act_template.triggered.connect(self._download_account_template)
        for action in (act_sheet, act_template):
            self.file_menu.addAction(action)
        self.file_btn.setMenu(self.file_menu)
        row(acc_card, SettingRow("账号表格", desc="从 xlsx 导入或下载模板",
                                 icon=FIF.SAVE_AS,
                                 control=controls(self.file_btn)),
            last=True)

        # ── 名单导入 ──
        ros_card = group("名单管理", True, "导入团员与团支书名单")
        self.roster_btn = PrimaryPushButton(FIF.PEOPLE, "导入名单")
        self.roster_btn.clicked.connect(self._import_roster)
        self.roster_more_btn = DropDownPushButton(FIF.SETTING, "名单管理")
        set_tip(self.roster_more_btn, "下载模板或删除选中的名单")
        roster_menu = RoundMenu(parent=self.roster_more_btn)
        act_roster_tpl = Action(FIF.DOWNLOAD, "下载名单模板")
        act_roster_tpl.triggered.connect(self._download_template)
        act_roster_del = Action(FIF.DELETE, "删除选中名单")
        act_roster_del.triggered.connect(self._delete_selected_roster)
        roster_menu.addAction(act_roster_tpl)
        roster_menu.addAction(act_roster_del)
        self.roster_more_btn.setMenu(roster_menu)
        self.roster_row = SettingRow("团员名单", desc="尚未导入",
                                     icon=FIF.PEOPLE,
                                     control=controls(self.roster_btn, self.roster_more_btn))
        row(ros_card, self.roster_row)

        self.roster_table = TableWidget()
        self.roster_table.verticalHeader().hide()
        self.roster_table.setColumnCount(3)
        self.roster_table.setBorderRadius(P.RADIUS["card"])  # 与卡片、窗口圆角同心
        self.roster_table.setHorizontalHeaderLabels(["名单", "人数", "名单文件"])
        self.roster_table.setEditTriggers(self.roster_table.EditTrigger.NoEditTriggers)
        header = self.roster_table.horizontalHeader()
        header.setSectionResizeMode(2, header.ResizeMode.Stretch)
        self.roster_table.setColumnWidth(2, 320)
        self._bind_roster_table_menu()
        roster_block = FullRow()
        roster_block.lay.addWidget(self.roster_table)
        row(ros_card, roster_block)

        self.sec_btn = PrimaryPushButton(FIF.CERTIFICATE, "导入支书")
        self.sec_btn.clicked.connect(self._import_secretaries)
        self.sec_more_btn = DropDownPushButton(FIF.SETTING, "支书管理")
        set_tip(self.sec_more_btn, "下载模板或删除选中的支书")
        sec_menu = RoundMenu(parent=self.sec_more_btn)
        act_sec_tpl = Action(FIF.DOWNLOAD, "下载支书模板")
        act_sec_tpl.triggered.connect(self._download_secretary_template)
        act_sec_del = Action(FIF.DELETE, "删除选中支书")
        act_sec_del.triggered.connect(self._delete_selected_secretaries)
        sec_menu.addAction(act_sec_tpl)
        sec_menu.addAction(act_sec_del)
        self.sec_more_btn.setMenu(sec_menu)
        self.sec_row = SettingRow("支书名单", desc="尚未导入",
                                  icon=FIF.CERTIFICATE,
                                  control=controls(self.sec_btn, self.sec_more_btn))
        row(ros_card, self.sec_row)
        self.sec_table = TableWidget()
        self.sec_table.verticalHeader().hide()
        self.sec_table.setColumnCount(3)
        self.sec_table.setBorderRadius(P.RADIUS["card"])  # 与卡片、窗口圆角同心
        self.sec_table.setHorizontalHeaderLabels(["支部", "团支书", "手机号码"])
        self.sec_table.setEditTriggers(self.sec_table.EditTrigger.NoEditTriggers)
        self._bind_secretary_table_menu()
        sec_block = FullRow()
        sec_block.lay.addWidget(self.sec_table)
        row(ros_card, sec_block, last=True)

        # ── 编号规则 ──
        rule_card = group("上传规则", False, "编号规则与志愿书页数")
        self.rule_btn = PushButton(FIF.EDIT, "编辑规则")
        self.rule_btn.clicked.connect(self._open_rule_dialog)
        self.rule_row = SettingRow("编号规则", desc="尚未设置",
                                   icon=FIF.CODE, control=controls(self.rule_btn))
        row(rule_card, self.rule_row)
        self.expected_pages_edit = LineEdit()
        self.expected_pages_edit.setPlaceholderText(
            str(DEFAULT_EXPECTED_PAGES))
        self.expected_pages_edit.editingFinished.connect(self._save_config)
        row(rule_card, SettingRow("预期页数", desc="入团志愿书的页数，留空则不核对",
                                  icon=FIF.DOCUMENT,
                                  control=controls(self.expected_pages_edit),
                                  separator=False))

        # ── 外观设置 ──
        look_card = group("外观设置", False, "主题、材质与首页头图")
        self.theme_combo = ComboBox()
        for label in ("跟随系统", "浅色模式", "暗色模式"):
            self.theme_combo.addItem(label)
        self.theme_combo.setCurrentIndex(THEME_INDEX.get(store.load().get("themeMode", "auto"), 0))
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        row(look_card, SettingRow("明暗模式", desc="跟随系统或固定深浅",
                                  icon=FIF.BRIGHTNESS, control=controls(self.theme_combo)))
        self.material_combo = ComboBox()
        # 只提供 fluent widgets 官方的两种材质；「关闭」是功能项，材质名用官方英文写法
        for label in ("关闭模糊", "Acrylic", "Mica"):
            self.material_combo.addItem(label)
        _cfg = store.load()
        from app.services.materials import blur_supported, preferred

        _material = preferred(_cfg)
        self.material_combo.setCurrentIndex(MATERIAL_ORDER.index(_material) if _material in MATERIAL_ORDER else 0)
        self.material_combo.currentIndexChanged.connect(self._on_material_changed)
        # 没有模糊通路的系统（Windows 10 及更早、Linux）直接禁用：留着能点却
        # 什么都不发生，只会让人反复试。说明行会讲清原因。
        self.material_combo.setEnabled(blur_supported())
        self.material_row = SettingRow(
            "窗口背景", desc="", icon=FIF.TRANSPARENT,
            control=controls(self.material_combo))
        row(look_card, self.material_row)
        self._sync_material_hint()
        self.header_pick_btn = PushButton(FIF.PHOTO, "选择图片")
        self.header_pick_btn.clicked.connect(self._pick_header_image)
        self.header_clear_btn = PushButton(FIF.SYNC, "恢复默认")
        set_tip(self.header_clear_btn, "清除头图，恢复默认")
        self.header_clear_btn.clicked.connect(self._clear_header_image)
        row(look_card, SettingRow("首页头图", desc="首页顶部横幅，可换成本地图片",
                                  icon=FIF.PHOTO,
                                  control=controls(self.header_pick_btn, self.header_clear_btn),
                                  ), last=True)

        # ── 工作配置 ──
        ws_card = group("工作与通知", False, "目录、通知与开机启动")
        # 工作目录只作为值保存：以前这个无父 LineEdit 是隐藏的游离顶层窗口，
        # 一旦哪里 setVisible(True) 就会像「08:00」输入框那样弹出独立小窗。
        self.workspace_dir = ""
        # 只有用户在设置页亲手选过目录，才允许把列表收成这一个；其余任何一次
        # _save_config 都必须以 workspaceDirs 为准，否则上传页加的多目录会被这里
        # 用「第一个目录」的单值反推回去、甚至整列清空（每次启动都要重选）。
        self._ws_touched = False
        self.ws_btn = PushButton(FIF.FOLDER, "选择目录")
        self.ws_btn.clicked.connect(self._pick_workspace)
        self.workspace_row = SettingRow(
            "工作目录", desc="尚未选择", icon=FIF.FOLDER,
            control=controls(self.ws_btn))
        row(ws_card, self.workspace_row)

        self.notify_mode = ComboBox()
        for key in notify_schedule.MODES:
            self.notify_mode.addItem(notify_schedule.MODE_LABELS[key], userData=key)
        # 时间与间隔只作为值保存，不放进页面布局：以前这俩是没有父控件的
        # LineEdit / ComboBox，_sync_notify_controls 一 setVisible 就变成独立顶层
        # 小窗浮在屏幕上（「08:00」输入框和「每 N 小时」下拉框）。
        self._notify_time = store.DEFAULT_NOTIFY_TIME
        self._notify_interval_hours = notify_schedule.DEFAULT_INTERVAL_HOURS
        self.notify_more_btn = PushButton(FIF.SETTING, "通知设置")
        set_tip(self.notify_more_btn, "发送时间、间隔与测试通知")
        self.notify_more_btn.clicked.connect(self._open_notify_dialog)
        self.notify_row = SettingRow(
            "通知频率", desc="提醒未读消息的间隔", icon=FIF.DATE_TIME,
            control=controls(self.notify_mode, self.notify_more_btn))
        row(ws_card, self.notify_row)
        self.notify_mode.currentIndexChanged.connect(self._on_notify_mode_changed)

        # 未开启时普通按钮「开启自启」，已开启时高亮按钮「关闭自启」，状态一眼可见
        self.autostart_btn = PushButton(FIF.POWER_BUTTON, "开启自启")
        self.autostart_btn.clicked.connect(self._toggle_autostart)
        self.autostart_on_btn = PrimaryPushButton(FIF.POWER_BUTTON, "关闭自启")
        self.autostart_on_btn.clicked.connect(self._toggle_autostart)
        for btn in (self.autostart_btn, self.autostart_on_btn):
            btn.setFixedWidth(CONTROL_WIDTH)
        self.autostart_box = QWidget()
        auto_lay = QHBoxLayout(self.autostart_box)
        auto_lay.setContentsMargins(0, 0, 0, 0)
        auto_lay.setSpacing(8)
        auto_lay.addWidget(self.autostart_btn)
        auto_lay.addWidget(self.autostart_on_btn)
        row(ws_card, SettingRow("开机自启", desc="登录系统后自动在托盘运行",
                                icon=FIF.POWER_BUTTON,
                                control=controls(self.autostart_box, uniform=False),
                                separator=False))

        # ── 系统备份 ──
        # 这里备份/恢复的是整份 config.json：账号、名单、工作区、通知、外观与编号规则
        # 全在里面，换机时整份搬走。单独成卡就是为了不让人误以为只备份支部账号。
        backup_card = group("系统备份", False,
                            "导出或恢复全部软件设置，迁移到您的其他设备")
        self.backup_export_btn = PushButton(FIF.SHARE, "导出备份")
        set_tip(self.backup_export_btn, "把全部设置导出成一个 JSON 文件")
        self.backup_export_btn.clicked.connect(self._export_config_json)
        self.backup_import_btn = PushButton(FIF.CLOUD_DOWNLOAD, "恢复备份")
        set_tip(self.backup_import_btn, "用备份文件替换当前全部设置")
        self.backup_import_btn.clicked.connect(self._import_config_json)
        row(backup_card, SettingRow(
            "全部设置",
            desc="导出所有您的工作设置到本地",
            icon=FIF.SAVE_AS,
            control=controls(self.backup_import_btn, self.backup_export_btn)))
        self.cloud_backup_btn = PushButton(FIF.CLOUD, "云端备份")
        set_tip(self.cloud_backup_btn, "用 6 位提取码在两台机器之间同步设置")
        self.cloud_backup_btn.clicked.connect(self._open_cloud_backup)
        row(backup_card, SettingRow(
            "云端备份",
            desc="使用提取码口令从云端恢复您的配置",
            icon=FIF.CLOUD,
            control=controls(self.cloud_backup_btn)),
            last=True)

        from app.ui.about_card import AboutCard

        self._about_card = AboutCard(self)
        lay.addWidget(self._about_card)

        self._watcher = None
        self._ws_dirs: list[str] = []   # 来源目录列表（上传页可以加多个）
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(800)
        self._scan_timer.timeout.connect(self._scan_workspace)

        try:
            self._load_config()
        finally:
            self._loading = False
        self._loaded = True
        self._refresh_table()

        # 刷新登录状态：每 15 分钟轮询一次到期账号，各账号 4 小时内不重复刷新
        self._timer = QTimer(self)
        self._timer.setInterval(15 * 60 * 1000)
        self._timer.timeout.connect(self.start_health_check)
        if self.accounts:
            self._timer.start()
            self.start_health_check()

    # ---------- 配置持久化 ----------



    # ---------- 团支部编号规则 ----------











    # ---------- 自启动 / 通知 ----------









    # ---------- 账号增删 / 去重 / 冲突 ----------









    # ---------- 齿轮弹窗：一行放不下的次要项 ----------







    # ---------- 账号删除（行内按钮 + 勾选批量） ----------











    # ---------- 表格右键菜单（Fluent RoundMenu） ----------













    # ---------- 表格 / 当前账号 ----------

    READONLY_FLAGS = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    # 列号 → 账号字段；届别=毕业年份（如 2023），留空表示在读未定
    EDITABLE_COLS = {1: "userName", 2: "password", 3: "remark", 4: "graduateYear"}






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
        # 手动触发（按钮 / 新增账号 / 改密码）跟组织状况一样挂右下角进度浮条；
        # 定时轮询不打扰。结果不再逐个账号弹消息条，结束时汇总成一条。
        self._health_visible = force
        self._health_done = 0
        self._health_total = len(accounts)
        self._health_checked: list[str] = []
        self._health_cancelled = False
        if force:
            self._show_health_progress()

        self._worker = HealthWorker(accounts, parent=self)
        self._worker.account_checked.connect(self._on_account_checked)
        # 槽必须是页面的绑定方法：finished 在 worker 线程发出，闭包槽会被
        # 直接投递到 worker 线程执行（碰控件=段错误），绑定方法才走队列回 GUI 线程
        self._worker.finished.connect(self._on_health_buttons_back)
        self._worker.finished.connect(self._on_health_finished)
        self._worker.start()


    # ---------- 测活进度 ----------







    # ---------- 浏览器打开（携带登录态） ----------










    # ---------- 名单（分学期 + 模板） ----------








    # ---------- 工作区 ----------






    # ---------- 首页头图（设置入口在此，生效于工具首页） ----------

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_banner()

    def showEvent(self, e):
        super().showEvent(e)
        self._place_banner()

    def _place_banner(self):
        """头图几何：铺满页面宽度、贴顶，跟随窗口宽度变化。"""
        if getattr(self, "header", None) is not None:
            self.header.setGeometry(0, 0, self.scrollWidget.width(), PageBanner.HEIGHT)




    # ---------- 外观（明暗模式 + 毛玻璃） ----------




    # ---------- 团支书名单 ----------




    # ---------- 对外提供 ----------


