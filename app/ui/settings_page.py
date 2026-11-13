"""系统配置页：团支部账号、名单导入、压缩阈值；配置持久化到 config.json。"""

from __future__ import annotations

import json
import os

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    InfoBar,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
)

from app.constants import CONFIG_FILE, API_BASE_URL
from app.services.api import SiteClient

ACCOUNTS_KEY = "accounts"  # [{name, password, remark}]


class SettingsPage(QWidget):
    roster_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('page-settings')
        self.roster = None
        self._client: SiteClient | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 28, 36, 28)
        lay.setSpacing(14)

        # 账号卡片
        acc = CardWidget()
        acc_lay = QVBoxLayout(acc)
        acc_lay.setContentsMargins(20, 16, 20, 16)
        acc_lay.addWidget(SubtitleLabel("团支部账号"))
        row = QHBoxLayout()
        self.user_edit = LineEdit()
        self.user_edit.setPlaceholderText("账号（如 ssfz12407）")
        self.pass_edit = PasswordLineEdit()
        self.pass_edit.setPlaceholderText("密码")
        row.addWidget(self.user_edit, 1)
        row.addWidget(self.pass_edit, 1)
        acc_lay.addLayout(row)
        row2 = QHBoxLayout()
        self.save_btn = PrimaryPushButton("保存账号")
        self.save_btn.clicked.connect(self._save_config)
        self.test_btn = PushButton("测试登录")
        self.test_btn.clicked.connect(self._test_login)
        row2.addWidget(self.save_btn)
        row2.addWidget(self.test_btn)
        row2.addStretch(1)
        acc_lay.addLayout(row2)
        lay.addWidget(acc)

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

        lay.addStretch(1)
        self._load_config()

    # ---------- 配置持久化 ----------

    def _load_config(self):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            return
        self.user_edit.setText(cfg.get("userName", ""))
        self.pass_edit.setText(cfg.get("password", ""))
        self.roster_edit.setText(cfg.get("rosterPath", ""))
        if cfg.get("rosterPath") and os.path.isfile(cfg["rosterPath"]):
            try:
                self._load_roster(cfg["rosterPath"])
            except ValueError as e:
                self.roster_info.setText(f"名单加载失败：{e}")

    def _save_config(self, *_):
        cfg = {
            "userName": self.user_edit.text().strip(),
            "password": self.pass_edit.text(),
            "rosterPath": self.roster_edit.text().strip(),
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        # 密码明文保存在本机配置（对齐原 Electron 版 vault 方案的本地文件形态），路径：
        InfoBar.success(self, "已保存", CONFIG_FILE, duration=3000, parent=self)
        self._client = None  # 使缓存客户端失效，下次用新配置

    def _test_login(self):
        client = self.client()
        if client is None:
            InfoBar.warning(self, "账号未填", "请先填写账号和密码", duration=2500, parent=self)
            return
        verdict = client.login()
        self._client = client if verdict.ok else None
        if verdict.ok:
            InfoBar.success(self, "登录成功", verdict.message, duration=3000, parent=self)
        else:
            InfoBar.error(self, "登录失败", verdict.message, duration=4000, parent=self)

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
        user = self.user_edit.text().strip()
        pwd = self.pass_edit.text()
        if not user or not pwd:
            return None
        if self._client and self._client.user_name == user and self._client.password == pwd:
            return self._client
        self._client = SiteClient(user, pwd)
        return self._client

    def scope_oid(self) -> str:
        return ""  # 默认全部团支部；后续可扩展选择框
