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
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QTableWidgetItem, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
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
    """逐账号测活：先只读探测，失效才尝试登录；结果逐个回报。"""

    account_checked = Signal(str, str, str)  # userName, status, message

    def __init__(self, accounts: list[dict]):
        super().__init__()
        self.accounts = accounts

    def run(self):
        for acc in self.accounts:
            user, pwd = acc.get("userName", ""), acc.get("password", "")
            if not user or not pwd:
                self.account_checked.emit(user, "unknown", "账号或密码为空")
                continue
            try:
                client = SiteClient(user, pwd)
                probe = client.session_account()
                if probe.ok:
                    self.account_checked.emit(user, "ok", probe.message or "会话有效")
                    continue
                verdict = client.login()
                mapping = {
                    "ok": "ok",
                    "failed": "failed",
                    "captcha": "captcha",
                    "passwordChange": "passwordChange",
                    "needMemberName": "needMemberName",
                }
                kind = str((verdict.data or {}).get("kind", "failed")) if isinstance(verdict.data, dict) else "failed"
                self.account_checked.emit(user, mapping.get(kind, "failed"), verdict.message)
            except Exception as e:
                self.account_checked.emit(user, "network", str(e))


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
        self.rosters: dict[str, Roster] = {}  # 学期 → 名单
        self._roster_paths: dict[str, str] = {}  # 学期 → xlsx 路径（持久化）
        self.roster: Roster | None = None  # 全部学期合并（供检查/上传用）
        self.accounts: list[dict] = []
        self.active_user = ""
        self._client: SiteClient | None = None
        self._worker: HealthWorker | None = None
        self._ws_worker: WorkspaceWorker | None = None
        self._ws_known: set[str] = set()

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

        # 账号表：勾选框 + 账号 + 备注 + 健康状态 + 最近检查 + 操作
        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["", "账号", "备注", "健康状态", "最近检查", "操作"])
        self.table.setColumnWidth(0, 32)
        self.table.setColumnWidth(5, 56)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(4, header.ResizeMode.Stretch)
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
        self.add_btn = PrimaryPushButton(FIF.ADD, "添加")
        self.add_btn.clicked.connect(self._add_manual)
        row1.addWidget(self.new_user, 1)
        row1.addWidget(self.new_pwd, 1)
        row1.addWidget(self.new_remark, 1)
        row1.addWidget(self.add_btn)
        add_lay.addLayout(row1)
        row2 = QHBoxLayout()
        self.import_btn = PushButton(FIF.DOCUMENT, "批量导入")
        self.import_btn.clicked.connect(self._import_accounts)
        self.del_btn = PushButton(FIF.DELETE, "删除勾选账号")
        self.del_btn.clicked.connect(self._delete_checked)
        row2.addWidget(self.import_btn)
        row2.addWidget(self.del_btn)
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
        ros_lay.addWidget(note_label("xlsx 列：姓名 / 手机号码 / 班级（团支部编号，如 12407）；可分学期导入，批量上传按班级自动分流到对应账号"))
        ros_lay.addSpacing(4)
        row3 = QHBoxLayout()
        self.semester_combo = ComboBox()
        self.semester_combo.addItems([guess_semester()])
        self.semester_combo.setCurrentText(guess_semester())
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
        self._refresh_roster()

        # 工作区
        ws_dir = cfg.get("workspaceDir", "")
        self.ws_edit.setText(ws_dir)
        if ws_dir and os.path.isdir(ws_dir):
            self._setup_watcher(ws_dir)

    def _save_config(self):
        cfg = store.load()
        cfg.update({
            "activeUser": self.active_user,
            "accounts": self.accounts,
            "rosters": dict(self._roster_paths),
            "workspaceDir": self.ws_edit.text().strip(),
            "notifyTime": self.notify_time_edit.text().strip() or store.DEFAULT_NOTIFY_TIME,
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
        box.noButton.setText("保留原密码")
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
        result = self._upsert_account(user, pwd, self.new_remark.text().strip())
        self.new_user.clear()
        self.new_pwd.clear()
        self.new_remark.clear()
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
            InfoBar.warning("没有读到账号", "每行应为：账号 密码（备注可选）；xlsx 需有「账号」「密码」列", duration=4000, parent=self)
            return
        stat = {"added": 0, "dedup": 0, "overwritten": 0, "kept": 0}
        for user, pwd, remark in rows:
            stat[self._upsert_account(user, pwd, remark)] += 1
        self._after_accounts_changed()
        msg = f"新增 {stat['added']}，去重跳过 {stat['dedup']}，覆盖 {stat['overwritten']}，冲突保留 {stat['kept']}"
        InfoBar.success("导入完成", msg, duration=5000, parent=self)

    def _read_account_file(self, path: str) -> list[tuple[str, str, str]]:
        import re as _re

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
                u = next((i for i, h in enumerate(header) if h in ("账号", "用户名", "userName", "account")), 0)
                p = next((i for i, h in enumerate(header) if h in ("密码", "password")), 1)
                r = next((i for i, h in enumerate(header) if h in ("备注", "说明", "remark")), None)
                data = data[1:]
            else:
                u, p, r = 0, 1, 2
            for raw in data:
                if not raw:
                    continue

                def cell(i, raw=raw):
                    return str(raw[i]).strip() if i is not None and i < len(raw) and raw[i] is not None else ""

                if cell(u) and cell(p):
                    rows.append((cell(u), cell(p), cell(r)))
        else:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    parts = _re.split(r"[,;\s]+", line.strip())
                    if len(parts) >= 2 and parts[0] and parts[1]:
                        rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
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
        box.noButton.setText("取消")
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

    def _delete_row(self, row: int):
        self._remove_accounts([row])

    def _delete_checked(self):
        rows = [i for i in range(self.table.rowCount())
                if self.table.item(i, 0) and self.table.item(i, 0).checkState() == Qt.CheckState.Checked]
        self._remove_accounts(rows)

    # ---------- 表格 / 当前账号 ----------

    def _refresh_table(self):
        self.table.setRowCount(len(self.accounts))
        for i, acc in enumerate(self.accounts):
            # 勾选框列
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, chk)
            self.table.setItem(i, 1, QTableWidgetItem(acc["userName"]))
            self.table.setItem(i, 2, QTableWidgetItem(acc.get("remark", "")))
            h = acc.get("health") or {}
            status, msg = h.get("status", "unknown"), h.get("message", "")
            label = HEALTH_LABEL.get(status, status) + (f"：{msg}" if msg and status != "ok" else "")
            self.table.setItem(i, 3, QTableWidgetItem(label))
            self.table.item(i, 3).setForeground(QColor(HEALTH_COLOR.get(status, "#9e9e9e")))
            checked = h.get("checkedAt", "")
            self.table.setItem(i, 4, QTableWidgetItem(checked.replace("T", " ")[:19] if checked else "—"))
            # 行内删除按钮
            btn = ToolButton(FIF.DELETE)
            btn.setToolTip(f"删除 {acc['userName']}")
            btn.clicked.connect(lambda _=False, r=i: self._delete_row(r))
            self.table.setCellWidget(i, 5, btn)
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
            InfoBar.warning("学期为空", "先在学期框填写如 2026秋", duration=2500, parent=self)
            return
        try:
            self.rosters[sem] = self._parse_roster(path)
        except Exception as e:
            InfoBar.error("名单导入失败", str(e), duration=5000, parent=self)
            return
        self._roster_paths[sem] = path
        self.roster_edit.setText(path)
        self._refresh_roster()
        self._save_config()
        InfoBar.success(f"已导入 {sem}", f"{len(self.rosters[sem].rows)} 名团员", duration=3000, parent=self)

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
