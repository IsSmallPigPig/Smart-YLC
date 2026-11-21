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




class SettingsConfigMixin:
    """系统配置页的配置持久化、规则、通知与云备份行为。"""

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
        self._notify_time = store.notify_time(cfg)
        kind = notify_schedule.mode(cfg)
        self.notify_mode.setCurrentIndex(max(0, self.notify_mode.findData(kind)))
        self._notify_interval_hours = notify_schedule.interval_hours(cfg)
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

        # 工作区：来源目录可能有多个（上传页可以拖入多个文件夹），这里显示第一个，
        # 监听与自动处理覆盖全部
        from app.services.workspace import load_workspace_dirs

        ws_dirs = load_workspace_dirs(cfg)
        self._ws_dirs = ws_dirs
        self._ws_touched = False      # 以磁盘上的列表为准，没动过就不收窄
        self.workspace_dir = ws_dirs[0] if ws_dirs else ""
        if ws_dirs:
            self._setup_watcher(ws_dirs)
            # 把已在待上传列表里的路径当作「已知」：否则启动后第一次扫目录会把
            # 存量 PDF 全当新文件重处理一遍，每次都提示「已加入待上传列表」。
            self._ws_known = {e.get("path") for e in self._pending_entries()}
        else:
            self.workspace_row.set_desc("尚未选择")

        # 文档检查：预期页数
        pages = cfg.get("expectedPages", DEFAULT_EXPECTED_PAGES)
        if pages in ("", None):
            pages = DEFAULT_EXPECTED_PAGES
        self.expected_pages_edit.setText(str(pages))

        # 团支部编号规则
        self._update_rule_preview()

        # 团支书名单状态
        self._refresh_secretary_status()

    def org_rule(self) -> OrgRule:
        return self._rule

    def _update_rule_preview(self):
        count = len(self.org_rule().effective_patterns())
        self.rule_row.set_desc(f"当前 {count} 条规则" if count else "尚未设置")

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
        """文档检查的预期页数。留空按 DEFAULT_EXPECTED_PAGES（13 页）。"""
        t = self.expected_pages_edit.text().strip()
        if not t:
            return DEFAULT_EXPECTED_PAGES
        try:
            return max(0, int(t))
        except ValueError:
            # 非法输入不能静默按默认值跑：界面还显示着错值，用户以为按它检查。
            from qfluentwidgets import InfoBar

            self.expected_pages_edit.setText(str(DEFAULT_EXPECTED_PAGES))
            InfoBar.warning("预期页数填错了",
                            f"只接受整数，已改回 {DEFAULT_EXPECTED_PAGES} 页。",
                            duration=5000, parent=self)
            return DEFAULT_EXPECTED_PAGES

    def _save_config(self):
        # 载入过程中不许回写。_load_config 里 setCurrentIndex 会触发
        # currentIndexChanged → _on_notify_mode_changed → 这里，而那一刻账号已经
        # 载入、名单（第 508 行）和工作区（第 537 行）还没载入，回写就会把磁盘上的
        # rosters / workspaceDir 抹成空值——每次启动丢一次名单和工作区就是这么来的。
        if self._loading or not getattr(self, "_loaded", False):
            return

        def _write(cfg: dict):
            dirs = list(cfg.get("workspaceDirs") or [])
            if getattr(self, "_ws_touched", False):
                # 刚在设置页选了目录：这就是「只用一个目录」的明确意图
                dirs = [self.workspace_dir.strip()] if self.workspace_dir.strip() else []
                self._ws_touched = False
            elif dirs:
                # 列表是唯一真相（上传页可以加多个、也可以移除）；输入框只显示第一个，
                # 不参与反推，避免某处忘了同步就把列表覆盖掉
                self.workspace_dir = dirs[0]
            cfg.update({
                "activeUser": self.active_user,
                "accounts": self.accounts,
                "rosters": dict(self._roster_paths),
                "workspaceDir": dirs[0] if dirs else "",
                "workspaceDirs": dirs,
                "notifyTime": self._notify_time,
                "notifyMode": self._notify_mode(),
                "notifyInterval": self._notify_interval_hours,
                "expectedPages": self.expected_pages(),
                "orgRule": self.org_rule().to_config(),
            })

        # 拿最新配置再改：工作线程刚写入的 memberCache/messagesCache 不会被旧快照覆盖
        store.update(_write)
        self._client = None
        # 账号改名/改密码/删除后，已经打开的网页标签跟着更新并重登
        window = getattr(self, "window_ref", None)
        sync = getattr(window, "sync_browser_accounts", None)
        if sync is not None:
            sync()

    def _notify_mode(self) -> str:
        return self.notify_mode.currentData() or notify_schedule.DEFAULT_MODE

    def _notify_cfg(self) -> dict:
        return {
            "notifyMode": self._notify_mode(),
            "notifyTime": self._notify_time,
            "notifyInterval": self._notify_interval_hours,
        }

    def _sync_notify_controls(self):
        self.notify_row.set_desc(notify_schedule.describe(self._notify_cfg()))

    def _on_notify_mode_changed(self, *_):
        self._sync_notify_controls()
        self._save_config()

    def _notify_channel_widget(self, parent):
        """一行说清提醒走哪个通道；被系统关掉时给一个直达开关的按钮。"""
        import sys

        from PyQt5.QtWidgets import QHBoxLayout, QWidget

        from qfluentwidgets import PushButton

        from app.services import macos_notify

        box = QWidget(parent)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        if sys.platform != "darwin":
            lay.addWidget(note_label("提醒走系统托盘气泡"), 1)
            return box
        state = macos_notify.permission_state()
        if state == "ok":
            lay.addWidget(note_label("系统通知已开启，提醒以系统横幅弹出"), 1)
        elif state == "denied":
            lay.addWidget(note_label(
                "系统通知被关闭，后台提醒不会弹出；在「系统设置 → 通知」里"
                f"打开「{macos_notify.app_name()}」即可"), 1)
            btn = PushButton(FIF.SETTING, "去开启")
            btn.clicked.connect(macos_notify.open_settings)
            lay.addWidget(btn, 0)
        elif state == "undetermined":
            lay.addWidget(note_label("尚未授权，启动时会询问系统通知权限"), 1)
        else:
            lay.addWidget(note_label("当前运行方式拿不到系统通知，打包成 .app 运行即可"), 1)
        return box

    def _test_notify(self, btn=None):
        """发一条测试提醒：点它会回到主界面，通道与点击一起验。

        只走系统原生通知（macOS 系统通知 / Windows 托盘气泡），不用应用内浮窗；
        原生通道不可用时明确报未送达，并引导去开启系统通知。"""
        from app.ui import bg_toast

        window = getattr(self, "window_ref", None)
        ok = bg_toast.alert(
            "团员情况（测试）", "点这条提醒会回到主界面；通知通道正常。",
            tone="info", tray=getattr(window, "tray", None),
            on_click=getattr(window, "_restore_from_tray", None),
        )
        if ok:
            InfoBar.success("已发出测试提醒", "通知中心里会出现一条测试提醒",
                            duration=4000, parent=self)
        else:
            hint = getattr(window, "_hint_notification_channel", None)
            if hint is not None:
                hint()
            else:
                InfoBar.warning("测试提醒未送达", "当前平台不可用或系统拒绝了通知",
                                duration=5000, parent=self)

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
            # 自启只是「开机把托盘跑起来」，跟有没有账号无关：账号可以之后再配
            path = autostart.install()
            InfoBar.success("已开启自启动", f"LaunchAgent：{path}", duration=4000, parent=self)
        self._refresh_autostart_btn()

    def _open_notify_dialog(self):
        """通知频率：发送时间、间隔与手动测试。

        时间框与间隔框每次打开都新建并挂在弹窗下，关窗写回 _notify_time /
        _notify_interval_hours（_save_config 与行说明读的正是这两个值）。页面不放
        这两个控件——无父控件的 LineEdit / ComboBox 会被 Qt 当成独立顶层窗口。"""
        from PyQt5.QtWidgets import QVBoxLayout, QWidget

        from qfluentwidgets import LineEdit, PushButton

        from app.ui.combo import ComboBox

        dialog = self._config_dialog("通知设置", "每天一次按时间点推送；按间隔则从上次推送起算")
        time_edit = LineEdit(dialog)
        time_edit.setPlaceholderText("08:00")
        time_edit.setClearButtonEnabled(False)
        time_edit.setText(self._notify_time)
        dialog.add_field("每天推送的时间点，如 08:00", time_edit)
        interval = ComboBox(dialog)
        for hours in notify_schedule.INTERVAL_CHOICES:
            interval.addItem(f"每 {hours} 小时", userData=hours)
        idx = interval.findData(self._notify_interval_hours)
        interval.setCurrentIndex(idx if idx >= 0 else 0)
        dialog.add_field("按间隔推送时，每隔多久发一次", interval)

        # 手动测试：上方说明 label，按钮横向铺满弹窗
        test_box = QWidget(dialog)
        tl = QVBoxLayout(test_box)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(4)
        test_btn = PushButton(FIF.SEND, "发送测试通知")
        test_btn.clicked.connect(lambda: self._test_notify(test_btn))
        tl.addWidget(test_btn)
        dialog.add(test_box)

        dialog.add_field("系统通知通道", self._notify_channel_widget(dialog))
        dialog.show()

        # 关窗前把弹窗里的值写回持久属性并落盘（deleteLater 是排队执行，
        # finished 信号同步回调时控件仍存活，可安全读取）。
        def _write_back():
            self._notify_time = time_edit.text().strip() or store.DEFAULT_NOTIFY_TIME
            self._notify_interval_hours = (interval.currentData()
                                           or notify_schedule.DEFAULT_INTERVAL_HOURS)
            self._sync_notify_controls()
            self._save_config()

        dialog.finished.connect(_write_back)

    def _export_config_json(self):
        """导出整份设置为 JSON（config.json 完整快照，含账号密码与全部设置）。"""
        from PyQt5.QtWidgets import QFileDialog

        import json

        from app.services.export_data import default_name as export_name

        path, _ = QFileDialog.getSaveFileName(
            self, "导出系统备份", export_name("系统备份", ".json"), "JSON (*.json)")
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
            "备份已导出",
            f"{path}（{n} 个账号，以及名单、工作区、通知、外观与编号规则等全部设置；"
            f"文件含密码，请妥善保管）",
            duration=6000, parent=self)

    def _import_config_json(self):
        """从备份恢复整份设置：先看清要覆盖什么，再替换并即时重载。"""
        from PyQt5.QtWidgets import QFileDialog

        import json

        path, _ = QFileDialog.getOpenFileName(
            self, "恢复系统备份", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError) as e:
            InfoBar.error("读取失败", str(e), duration=6000, parent=self)
            return
        if not isinstance(cfg, dict) or not isinstance(cfg.get("accounts"), list):
            InfoBar.error("不是有效的备份文件",
                          "这个 JSON 里没有 accounts 列表，不像是本程序导出的系统备份。"
                          "请到「系统备份 → 导出备份」重新导出一份再试。",
                          duration=8000, parent=self)
            return

        accounts = cfg.get("accounts") or []
        rosters = cfg.get("rosters") or {}
        dialogs.confirm(
            self.window(), "恢复系统备份",
            f"将用这份备份替换当前全部设置：{len(accounts)} 个账号、{len(rosters)} 份名单，"
            f"以及工作区、通知、明暗模式、窗口材质、首页头图与编号规则。\n"
            f"当前设置会先另存一份备份。",
            yes="恢复", cancel="取消",
            on_result=lambda ok: ok and self._do_import_config(cfg))

    def _open_cloud_backup(self):
        from app.ui.cloud_backup_dialog import CloudBackupDialog

        cfg = store.load() or {}
        custom = list(cfg.get("cloudBackupServers") or [])
        legacy = str(cfg.get("cloudBackupServer") or "")
        if legacy and legacy not in custom:
            custom.append(legacy)   # 1.0.9 只存一个地址，迁进站点列表
        CloudBackupDialog(
            self.window(),
            get_config=store.load,
            on_restore=self._confirm_cloud_restore,
            profiles=cfg.get("cloudProfiles"),
            active_id=str(cfg.get("cloudActiveProfile") or ""),
            custom_servers=custom,
            on_change=self._save_cloud_state,
        ).show()

    def _save_cloud_state(self, patch: dict) -> None:
        """对话框的档案/站点改动落到配置里（合并写，不整份覆盖）。"""
        try:
            store.update(lambda cfg: cfg.update(patch))
        except Exception as e:                 # noqa: BLE001 - 任何写盘失败都要让用户看见
            # 写不进去必须让用户看见：否则设置看似保存了，重启后全丢。
            from qfluentwidgets import InfoBar

            from app.services import app_log

            app_log.error("云端备份设置写盘失败：%s", e)
            InfoBar.error("设置没保存", f"配置写入失败：{e}。请检查磁盘空间与文件权限。",
                          duration=10000, parent=self)

    def _confirm_cloud_restore(self, cfg: dict):
        from app.ui import dialogs

        dialogs.confirm(
            self.window(), "恢复云端备份",
            f"将用云端这份备份替换当前全部设置：{len(cfg.get('accounts') or [])} 个账号、"
            f"{len(cfg.get('rosters') or {})} 份名单，以及工作区、通知、外观与编号规则。\n"
            f"当前设置会先另存一份备份。",
            yes="恢复", cancel="取消",
            on_result=lambda ok: ok and self._do_import_config(cfg))

    def _do_import_config(self, cfg: dict):
        """整份替换配置，然后把页面与窗口状态一起重载。"""
        from datetime import datetime

        import shutil

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        cfg_file = store.CONFIG_FILE
        backup = f"{cfg_file}.before-import-{stamp}.json"
        try:
            shutil.copyfile(cfg_file, backup)
        except OSError:
            backup = ""      # 首次运行还没有配置文件，没有可备份的东西
        try:
            store.save(cfg)
        except (OSError, ValueError) as e:
            InfoBar.error("导入失败", f"写入配置出错：{e}", duration=8000, parent=self)
            return

        # 重载：构造期那套「未载入不许回写」的闸门这里同样要合上，
        # 否则控件初始化过程中的信号会用空值把刚导入的配置写回去
        self._loading = True
        try:
            self._load_config()
        finally:
            self._loading = False
        self._loaded = True
        self._refresh_table()
        self._apply_imported_look()

        window = getattr(self, "window_ref", None)
        if window is not None and hasattr(window, "sync_browser_accounts"):
            window.sync_browser_accounts()
        self.refresh_notices()

        n = len(cfg.get("accounts") or [])
        extra = f"；原配置备份在 {backup}" if backup else ""
        InfoBar.success("备份已恢复", f"{n} 个账号，以及名单、工作区、通知与外观等全部设置已生效{extra}",
                        duration=8000, parent=self)

    def _apply_imported_look(self):
        """把导入的明暗模式、材质、头图立即应用到窗口（这三项不在 _load_config 里）。"""
        from app.setup import apply_theme_mode
        from app.services.materials import preferred

        cfg = store.load()
        mode = cfg.get("themeMode", "auto")
        material = preferred(cfg)
        for combo, value in ((self.theme_combo, mode), (self.material_combo, material)):
            combo.blockSignals(True)
            if combo is self.theme_combo:
                combo.setCurrentIndex(THEME_INDEX.get(value, 0))
            else:
                combo.setCurrentIndex(MATERIAL_ORDER.index(material))
            combo.blockSignals(False)

        self._sync_material_hint()

        apply_theme_mode(mode)
        window = getattr(self, "window_ref", None)
        if window is not None and hasattr(window, "apply_material"):
            window.apply_material(material)
        header = cfg.get("headerImage", "")
        self.header.set_image_path(header)
        self._refresh_banners(header)

