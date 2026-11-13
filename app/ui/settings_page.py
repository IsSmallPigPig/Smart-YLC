"""系统配置页：多团支部账号管理、后台健康检查、名单导入、冲突处理。

配置持久化到 ~/.gd12355-helper/config.json：
  {"activeUser", "rosterPath", "accounts": [{userName, password, remark, health}]}

去重规则（导入/添加时）：
  - 同账号 + 同密码 → 视为重复，跳过
  - 同账号 + 不同密码 → 弹窗询问：覆盖 / 保留原密码
健康检查（后台线程，15 分钟自动 + 手动触发）：
  先只读探测会话，失效才尝试登录（避免误增密码错误次数触发验证码）。
"""

from __future__ import annotations

import os
import re
from datetime import datetime

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget, QTableWidgetItem

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
    SubtitleLabel,
    TableWidget,
)

from app.ui.page import FluentPage
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
                self.account_checked.emit(user, mapping.get(verdict.kind, "failed"), verdict.message)
            except Exception as e:
                self.account_checked.emit(user, "network", str(e))


class SettingsPage(FluentPage):
    roster_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page-settings")
        self.roster = None
        self.accounts: list[dict] = []
        self.active_user = ""
        self._client: SiteClient | None = None
        self._worker: HealthWorker | None = None

        lay = self.vBoxLayout

        # 当前使用账号
        top = CardWidget()
        top_lay = QVBoxLayout(top)
        top_lay.setContentsMargins(20, 16, 20, 16)
        top_lay.addWidget(SubtitleLabel("当前使用的团支部账号"))
        row0 = QHBoxLayout()
        self.active_combo = ComboBox()
        self.active_combo.currentTextChanged.connect(self._on_active_changed)
        self.health_btn = PushButton(FIF.SYNC, "全部测活")
        self.health_btn.clicked.connect(self.start_health_check)
        row0.addWidget(self.active_combo, 1)
        row0.addWidget(self.health_btn)
        top_lay.addLayout(row0)
        lay.addWidget(top)

        # 账号表
        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["账号", "备注", "健康状态", "最近检查"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.add_table(self.table, 2)

        # 添加账号
        add = CardWidget()
        add_lay = QVBoxLayout(add)
        add_lay.setContentsMargins(20, 16, 20, 16)
        add_lay.addWidget(SubtitleLabel("添加 / 导入账号（自动去重）"))
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
        self.import_btn = PushButton(FIF.DOCUMENT, "从文件批量导入（xlsx / txt）")
        self.import_btn.clicked.connect(self._import_accounts)
        self.del_btn = PushButton(FIF.DELETE, "删除选中账号")
        self.del_btn.clicked.connect(self._delete_selected)
        row2.addWidget(self.import_btn)
        row2.addWidget(self.del_btn)
        row2.addStretch(1)
        add_lay.addLayout(row2)
        lay.addWidget(add)

        # 名单卡片
        ros = CardWidget()
        ros_lay = QVBoxLayout(ros)
        ros_lay.setContentsMargins(20, 16, 20, 16)
        ros_lay.addWidget(SubtitleLabel("预设团员名单（xlsx，列：姓名 / 手机号码）"))
        row3 = QHBoxLayout()
        self.roster_edit = LineEdit()
        self.roster_edit.setPlaceholderText("选择名单文件")
        self.roster_btn = PushButton("导入名单")
        self.roster_btn.clicked.connect(self._import_roster)
        self.roster_info = BodyLabel("未导入")
        row3.addWidget(self.roster_edit, 1)
        row3.addWidget(self.roster_btn)
        row3.addWidget(self.roster_info)
        ros_lay.addLayout(row3)
        lay.addWidget(ros)

        # 每日通知 + 自启动卡片
        daily = CardWidget()
        daily_lay = QVBoxLayout(daily)
        daily_lay.setContentsMargins(20, 16, 20, 16)
        daily_lay.addWidget(SubtitleLabel("每日团员情况通知 / 后台自启动"))
        row4 = QHBoxLayout()
        row4.addWidget(BodyLabel("每天"))
        self.notify_time_edit = LineEdit()
        self.notify_time_edit.setFixedWidth(80)
        self.notify_time_edit.setPlaceholderText("08:00")
        self.notify_time_edit.setClearButtonEnabled(False)
        row4.addWidget(self.notify_time_edit)
        row4.addWidget(BodyLabel("通过系统通知推送团员情况（后台模式生效）"))
        row4.addStretch(1)
        self.autostart_btn = PushButton()
        self.autostart_btn.clicked.connect(self._toggle_autostart)
        row4.addWidget(self.autostart_btn)
        daily_lay.addLayout(row4)
        lay.addWidget(daily)

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
        self.roster_edit.setText(cfg.get("rosterPath", ""))
        self.notify_time_edit.setText(store.notify_time(cfg))
        self._refresh_autostart_btn()
        if cfg.get("rosterPath") and os.path.isfile(cfg["rosterPath"]):
            try:
                self._load_roster(cfg["rosterPath"])
            except ValueError as e:
                self.roster_info.setText(f"名单加载失败：{e}")

    def _save_config(self):
        cfg = store.load()
        cfg.update({
            "activeUser": self.active_user,
            "rosterPath": self.roster_edit.text().strip(),
            "accounts": self.accounts,
            "notifyTime": self.notify_time_edit.text().strip() or store.DEFAULT_NOTIFY_TIME,
        })
        store.save(cfg)
        self._client = None

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
            InfoBar.info(self, "已关闭自启动", "后台托盘不再开机运行", duration=3000, parent=self)
        else:
            if not self.accounts:
                InfoBar.warning(self, "先配置账号", "自启动的后台模式每天要检查团员情况，请先在上方添加账号", duration=4000, parent=self)
                return
            path = autostart.install()
            InfoBar.success(self, "已开启自启动", f"LaunchAgent：{path}", duration=4000, parent=self)
        self._refresh_autostart_btn()


    # ---------- 账号增删 / 去重 / 冲突 ----------

    def _resolve_conflict(self, user: str, existing: dict) -> str:
        """同账号不同密码：询问用户。返回 'overwrite' | 'keep'。"""
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
        """返回 'added' | 'dedup' | 'overwritten' | 'kept'（冲突保留）。"""
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
            InfoBar.warning(self, "账号或密码未填", "两栏都填好再点添加", duration=2500, parent=self)
            return
        result = self._upsert_account(user, pwd, self.new_remark.text().strip())
        self.new_user.clear()
        self.new_pwd.clear()
        self.new_remark.clear()
        self._after_accounts_changed()
        if result == "dedup":
            InfoBar.info(self, "已去重", f"账号 {user} 同密码已存在，自动跳过", duration=3000, parent=self)
        elif result == "kept":
            InfoBar.info(self, "已保留", f"账号 {user} 保留了原密码", duration=3000, parent=self)

    def _import_accounts(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择账号文件", "", "表格或文本 (*.xlsx *.xlsm *.txt *.csv)")
        if not path:
            return
        rows = self._read_account_file(path)
        if not rows:
            InfoBar.warning(self, "没有读到账号", "每行应为：账号 密码（备注可选）；xlsx 需有「账号」「密码」列", duration=4000, parent=self)
            return
        stat = {"added": 0, "dedup": 0, "overwritten": 0, "kept": 0}
        for user, pwd, remark in rows:
            stat[self._upsert_account(user, pwd, remark)] += 1
        self._after_accounts_changed()
        msg = f"新增 {stat['added']}，去重跳过 {stat['dedup']}，覆盖 {stat['overwritten']}，冲突保留 {stat['kept']}"
        InfoBar.success(self, "导入完成", msg, duration=5000, parent=self)

    def _read_account_file(self, path: str) -> list[tuple[str, str, str]]:
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
                    parts = re.split(r"[,;\s]+", line.strip())
                    if len(parts) >= 2 and parts[0] and parts[1]:
                        rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
        return rows

    def _delete_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.accounts):
            InfoBar.warning(self, "未选中", "先在表格里点选要删除的账号", duration=2500, parent=self)
            return
        user = self.accounts[row]["userName"]
        box = MessageBox("删除账号", f"确定从列表删除 {user}？（不影响站点数据）", self.window())
        box.yesButton.setText("删除")
        box.noButton.setText("取消")
        box.exec()
        if not box.result():
            return
        self.accounts.pop(row)
        if self.active_user == user:
            self.active_user = self.accounts[0]["userName"] if self.accounts else ""
        self._after_accounts_changed()

    # ---------- 表格 / 当前账号 ----------

    def _refresh_table(self):
        self.table.setRowCount(len(self.accounts))
        for i, acc in enumerate(self.accounts):
            h = acc.get("health") or {}
            status, msg = h.get("status", "unknown"), h.get("message", "")
            self.table.setItem(i, 0, QTableWidgetItem(acc["userName"]))
            self.table.setItem(i, 1, QTableWidgetItem(acc.get("remark", "")))
            label = HEALTH_LABEL.get(status, status) + (f"：{msg}" if msg and status != "ok" else "")
            self.table.setItem(i, 2, QTableWidgetItem(label))
            self.table.item(i, 2).setForeground(QColor(HEALTH_COLOR.get(status, "#9e9e9e")))
            checked = h.get("checkedAt", "")
            self.table.setItem(i, 3, QTableWidgetItem(checked.replace("T", " ")[:19] if checked else "—"))
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
            return  # 已有测活在跑，避免叠加
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

    # ---------- 名单 ----------

    def _import_roster(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择名单", "", "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        self.roster_edit.setText(path)
        try:
            self._load_roster(path)
            self._save_config()
        except Exception as e:
            InfoBar.error(self, "名单导入失败", str(e), duration=5000, parent=self)

    def _load_roster(self, path: str):
        from app.services.excel import import_roster

        self.roster = import_roster(path)
        self.roster_info.setText(f"已导入 {len(self.roster.rows)} 人")
        self.roster_changed.emit(self.roster)

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
        return ""  # 默认全部团支部；后续可扩展选择框
