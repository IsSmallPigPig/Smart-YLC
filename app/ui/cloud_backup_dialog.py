"""云端备份弹层：多份配置档案，4 位密码参与加密，服务端只存密文。

档案分两种：
* 自己新建的——本地存有所有权凭据（owner key），可以上传覆盖；
* 加入的他人档案——没有凭据，只能拉取恢复，服务端也会拒绝越权覆盖。

建档时先向服务端查重：提取码已有档案就拒绝创建，避免两个人撞上同一个码。

弹层不自己写配置——恢复出来的 dict 交回设置页，走与「从文件恢复」同一条
确认与重载路径，避免两条恢复逻辑各自演化。
"""

from __future__ import annotations

from typing import Callable

from PyQt5.QtCore import QThread, pyqtSignal as Signal
from PyQt5.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    InfoBar,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    ToolButton,
)

from app.services import cloud_backup
from app.ui.combo import ComboBox
from app.ui.dialogs import ConfigDialog


class _Worker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # noqa: BLE001 - 网络/解密错误都直接告诉用户
            self.failed.emit(str(exc))


NO_PROFILE_TEXT = "还没有配置，点击新建或导入"


def _profile_id(server: str, code: str) -> str:
    return f"{server}|{code}"


class SiteCombo(ComboBox):
    """站点下拉：默认站点 + 用户新增；末尾一项「＋ 新增站点…」触发输入框。"""

    ADD_ITEM = "__add__"

    def __init__(self, servers: list[str], parent=None):
        super().__init__(parent)
        self._servers = list(servers)
        self._on_add: Callable[[SiteCombo], None] | None = None
        self._last_valid = 0
        self.refresh()
        self.currentIndexChanged.connect(self._index_changed)

    def refresh(self, select: str | None = None) -> None:
        current = select or self.currentText()
        self.blockSignals(True)
        self.clear()
        for server in self._servers:
            self.addItem(server, userData=server)
        self.addItem("＋ 新增站点…", userData=self.ADD_ITEM)
        index = self.findData(current)
        if index < 0:
            index = 0
        self.setCurrentIndex(index)
        self._last_valid = index if self.itemData(index) != self.ADD_ITEM else 0
        self.blockSignals(False)

    def current_server(self) -> str:
        """当前站点；停在「新增站点…」这一项时回退到上一个有效站点。"""
        data = self.currentData()
        if data == self.ADD_ITEM:
            data = self.itemData(self._last_valid)
        return "" if data == self.ADD_ITEM or data is None else str(data)

    def add_server(self, url: str, select: bool = True) -> None:
        server = cloud_backup.normalize_server(url)
        if server not in self._servers:
            self._servers.append(server)
        self.refresh(select=server if select else None)
        self._index_changed(self.currentIndex())

    def set_servers(self, servers: list[str]) -> None:
        self._servers = list(servers) or list(cloud_backup.DEFAULT_SERVERS)
        self.refresh()

    def on_add_requested(self, fn: Callable[[SiteCombo], None]) -> None:
        self._on_add = fn

    def _index_changed(self, index: int) -> None:
        if self.itemData(index) == self.ADD_ITEM:
            # 选中的是触发项：先倒回上一个有效站点，再开输入框
            self.setCurrentIndex(self._last_valid)
            if self._on_add:
                self._on_add(self)
            return
        self._last_valid = index


class _SiteAddDialog(ConfigDialog):
    """新增备份站点：填地址，确认后回调给下拉框。"""

    def __init__(self, parent, on_add: Callable[[str], None]):
        super().__init__("新增备份站点", parent, width=440)
        self.edit = LineEdit(self.widget)
        self.edit.setPlaceholderText("https://backup.example.com")
        self.add_field("服务端地址", self.edit)
        self.add_field(
            "说明",
            BodyLabel("地址需指向部署了同款备份服务的服务端。", self.widget),
        )
        self.cancelButton.setText("取消")
        self.cancelButton.show()
        try:
            self.yesButton.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass

        def submit():
            try:
                server = cloud_backup.normalize_server(self.edit.text())
            except ValueError as exc:
                InfoBar.warning("地址不对", str(exc), duration=4000, parent=self)
                return
            self._worker = _Worker(lambda: cloud_backup.probe(server, "ABC234"), self)

            def ok(_exists):
                on_add(server)
                self.accept()

            def fail(message):
                self.yesButton.setEnabled(True)
                InfoBar.error("这个站点不对", message, duration=6000, parent=self)

            self.yesButton.setEnabled(False)
            self._worker.done.connect(ok)
            self._worker.failed.connect(fail)
            self._worker.start()

        self.yesButton.clicked.connect(submit)


class _NewProfileDialog(ConfigDialog):
    """新建档案：名称 + 站点 + 提取码（可自动生成）+ 4 位密码。

    确定时先向服务端查重（提取码撞车就拒绝），成功后回调把档案交还主弹层。
    """

    def __init__(self, parent, servers: list[str], on_create, existing=None):
        super().__init__("新建云端配置", parent, width=460,
                         note="提取码在这个站点上重复时不会创建；成功后立即上传当前设置。")
        self._on_create = on_create
        self._existing = list(existing or [])
        self._name_touched = False
        self.name_edit = LineEdit(self.widget)
        self.name_edit.setPlaceholderText("默认按提取码起名，可改")
        self.name_edit.textEdited.connect(self._mark_name_touched)
        self.add_field("名称", self.name_edit)

        self.site_combo = SiteCombo(servers, self.widget)
        self.site_combo.on_add_requested(self._add_site)
        self.add_field("备份站点", self.site_combo)

        self.code_edit = LineEdit(self.widget)
        self.code_edit.setText(cloud_backup.generate_code())
        self.code_edit.setPlaceholderText("留空自动生成")
        self.code_edit.setMaxLength(6)
        self.code_edit.textEdited.connect(self._sync_default_name)
        code_box = QWidget(self.widget)
        row = QHBoxLayout(code_box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.code_edit, 1)
        dice = ToolButton(FIF.SYNC, code_box)
        dice.setToolTip("随机生成提取码")
        dice.clicked.connect(self._roll_code)
        row.addWidget(dice)
        self.add_field("提取码（6 位）", code_box)

        self.pwd_edit = PasswordLineEdit(self.widget)
        self.pwd_edit.setPlaceholderText("4 位数字，恢复时要再输一次")
        self.pwd_edit.setMaxLength(4)
        self.add_field("密码（防误用）", self.pwd_edit)

        self.cancelButton.setText("取消")
        self.cancelButton.show()
        try:
            self.yesButton.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.yesButton.clicked.connect(self._submit)
        self._sync_default_name()

    def _roll_code(self):
        self.code_edit.setText(cloud_backup.generate_code())
        self._sync_default_name()

    def _mark_name_touched(self, *_):
        self._name_touched = True

    def _default_name(self, code: str) -> str:
        """名称默认取「同一提取码已有的那份配置」，没有就按提取码起名。"""
        for p in self._existing:
            if p.get("code") == code:
                return str(p.get("name") or "")
        return f"配置 {code}" if code else ""

    def _sync_default_name(self, *_):
        if self._name_touched:
            return
        raw = self.code_edit.text().strip().upper()
        self.name_edit.setText(self._default_name(raw) if len(raw) == 6 else "")

    def _add_site(self, combo):
        _SiteAddDialog(self, lambda url: combo.add_server(url)).show()

    def _inputs(self):
        server = self.site_combo.current_server()
        if not server:
            raise ValueError("请选一个备份站点，或点「＋ 新增站点…」")
        server = cloud_backup.normalize_server(server)
        raw = self.code_edit.text().strip()
        code = cloud_backup.normalize_code(raw) if raw else cloud_backup.generate_code()
        password = cloud_backup.normalize_password(self.pwd_edit.text())
        name = self.name_edit.text().strip()[:40] or self._default_name(code)
        return {"name": name, "server": server, "code": code,
                "ownerKey": cloud_backup.generate_owner_key()}, password

    def _submit(self):
        try:
            profile, password = self._inputs()
        except ValueError as exc:
            InfoBar.warning("还差一点", str(exc), duration=4000, parent=self)
            return
        self.yesButton.setEnabled(False)
        self._worker = _Worker(
            lambda: cloud_backup.probe(profile["server"], profile["code"]), self)

        def ok(exists):
            self.yesButton.setEnabled(True)
            if exists:
                InfoBar.warning("提取码已被占用",
                                f"{profile['code']} 在这个站点上已有配置，换一个提取码",
                                duration=6000, parent=self)
                return
            self.accept()
            self._on_create(profile, password)

        def fail(message):
            self.yesButton.setEnabled(True)
            InfoBar.error("查重失败", message, duration=6000, parent=self)

        self._worker.done.connect(ok)
        self._worker.failed.connect(fail)
        self._worker.start()


class _JoinProfileDialog(ConfigDialog):
    """加入他人档案：只登记提取码，本地不留所有权凭据，只能拉取。"""

    def __init__(self, parent, servers: list[str], on_join, existing=None):
        super().__init__("加入他人配置", parent, width=460,
                         note="只登记提取码；这份配置的所属人才能上传，你只能拉取恢复。")
        self._on_join = on_join
        self._existing = list(existing or [])
        self._name_touched = False
        self.name_edit = LineEdit(self.widget)
        self.name_edit.setPlaceholderText("默认按提取码起名，可改")
        self.name_edit.textEdited.connect(self._mark_name_touched)
        self.add_field("名称", self.name_edit)

        self.site_combo = SiteCombo(servers, self.widget)
        self.site_combo.on_add_requested(self._add_site)
        self.add_field("备份站点", self.site_combo)

        self.code_edit = LineEdit(self.widget)
        self.code_edit.setPlaceholderText("对方的 6 位提取码")
        self.code_edit.setMaxLength(6)
        self.code_edit.textEdited.connect(self._sync_default_name)
        self.add_field("提取码", self.code_edit)

        self.cancelButton.setText("取消")
        self.cancelButton.show()
        try:
            self.yesButton.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.yesButton.clicked.connect(self._submit)

    def _mark_name_touched(self, *_):
        self._name_touched = True

    def _default_name(self, code: str) -> str:
        for p in self._existing:
            if p.get("code") == code:
                return str(p.get("name") or "")
        return f"配置 {code}" if code else ""

    def _sync_default_name(self, *_):
        if self._name_touched:
            return
        raw = self.code_edit.text().strip().upper()
        self.name_edit.setText(self._default_name(raw) if len(raw) == 6 else "")

    def _add_site(self, combo):
        _SiteAddDialog(self, lambda url: combo.add_server(url)).show()

    def _submit(self):
        try:
            server = cloud_backup.normalize_server(self.site_combo.current_server())
            code = cloud_backup.normalize_code(self.code_edit.text())
        except ValueError as exc:
            InfoBar.warning("还差一点", str(exc), duration=4000, parent=self)
            return
        name = self.name_edit.text().strip()[:40] or self._default_name(code)
        self.accept()
        self._on_join({"name": name, "server": server, "code": code,
                       "ownerKey": ""})


class CloudBackupDialog(ConfigDialog):
    def __init__(self, parent, get_config, on_restore,
                 profiles: list[dict] | None = None,
                 active_id: str = "",
                 custom_servers: list[str] | None = None,
                 on_change: Callable[[dict], None] | None = None):
        super().__init__("云端备份", parent, width=520,
                         note="每份配置一个提取码；上传要再验一次 4 位密码，防止误覆盖。")
        self._get_config = get_config
        self._on_restore = on_restore
        self._on_change = on_change or (lambda patch: None)
        self._worker: _Worker | None = None
        self._profiles: list[dict] = [
            p for p in (cloud_backup.valid_profile(raw) for raw in (profiles or [])) if p
        ]
        self._custom_servers = [str(s) for s in (custom_servers or [])]
        self._servers = cloud_backup.site_choices(self._custom_servers)
        self._active_id = active_id
        self._running = False

        # 第一行：档案下拉 + 新建/加入/删除图标
        self.profile_combo = ComboBox(self.widget)
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        row1 = QWidget(self.widget)
        lay1 = QHBoxLayout(row1)
        lay1.setContentsMargins(0, 0, 0, 0)
        lay1.setSpacing(6)
        lay1.addWidget(self.profile_combo, 1)
        self.new_btn = ToolButton(FIF.ADD, row1)
        self.new_btn.setToolTip("新建配置（生成新提取码并查重）")
        self.join_btn = ToolButton(FIF.LINK, row1)
        self.join_btn.setToolTip("加入他人的配置（只能拉取）")
        self.del_btn = ToolButton(FIF.DELETE, row1)
        self.del_btn.setToolTip("删除这份本地档案（不影响云端）")
        for btn in (self.new_btn, self.join_btn, self.del_btn):
            lay1.addWidget(btn)
        self.add(row1)

        # 第二行：站点下拉（独占一行，状态文字另起一行，不挤占输入宽度）
        self.site_combo = SiteCombo(self._servers, self.widget)
        self.site_combo.on_add_requested(self._add_site)
        self.site_combo.currentIndexChanged.connect(self._server_changed)
        self.add(self.site_combo)

        self.status = BodyLabel("", self.widget)
        self.status.setMinimumHeight(22)
        self.status.setWordWrap(True)
        self.add(self.status)

        # 第三行：4 位密码
        self.pwd_edit = PasswordLineEdit(self.widget)
        self.pwd_edit.setPlaceholderText("4 位数字密码")
        self.pwd_edit.setMaxLength(4)
        self.add(self.pwd_edit)

        # 动作：两个按钮定宽居中，中间留 12px。以前各占一半、撑满整行，两个
        # 按钮边框相接，看着像一整条，也分不清哪块能点。
        row3 = QWidget(self.widget)
        lay3 = QHBoxLayout(row3)
        lay3.setContentsMargins(0, 4, 0, 0)
        lay3.setSpacing(12)
        self.upload_btn = PrimaryPushButton(FIF.CLOUD, "上传", row3)
        self.restore_btn = PushButton(FIF.DOWNLOAD, "拉取并恢复", row3)
        for btn in (self.upload_btn, self.restore_btn):
            btn.setFixedWidth(176)
        lay3.addStretch(1)
        lay3.addWidget(self.upload_btn)
        lay3.addWidget(self.restore_btn)
        lay3.addStretch(1)
        self.add(row3)

        self.new_btn.clicked.connect(self._new_profile)
        self.join_btn.clicked.connect(self._join_profile)
        self.del_btn.clicked.connect(self._delete_profile)
        self.upload_btn.clicked.connect(self._upload)
        self.restore_btn.clicked.connect(self._restore)
        self.yesButton.hide()
        self.cancelButton.setText("关闭")
        self.cancelButton.show()

        self._refresh_profiles()

    # ---- 档案状态 ----------------------------------------------------------

    def _profile_text(self, p: dict) -> str:
        return f"{p['name']}（{p['code']}{'，只读' if not p['ownerKey'] else ''}）"

    def _refresh_profiles(self, keep: str | None = None) -> None:
        wanted = keep if keep is not None else self._active_id
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        if not self._profiles:
            # 空下拉框会让人以为坏了：放一条说明当占位，按钮保持禁用
            self.profile_combo.addItem(NO_PROFILE_TEXT, userData="")
            self.profile_combo.setEnabled(False)
            self.profile_combo.blockSignals(False)
            self._active_id = ""
            self._profile_changed(0)
            return
        for p in self._profiles:
            self.profile_combo.addItem(
                self._profile_text(p), userData=_profile_id(p["server"], p["code"]))
        index = self.profile_combo.findData(wanted)
        if index < 0 and self._profiles:
            index = 0
        self.profile_combo.setCurrentIndex(index)
        self.profile_combo.blockSignals(False)
        self._active_id = str(self.profile_combo.currentData() or "")
        self._profile_changed(self.profile_combo.currentIndex())

    def _current(self) -> dict | None:
        for p in self._profiles:
            if _profile_id(p["server"], p["code"]) == self._active_id:
                return p
        return None

    def _profile_changed(self, index: int = 0) -> None:
        data = self.profile_combo.currentData()
        if data:
            self._active_id = str(data)
        profile = self._current()
        mine = bool(profile and profile["ownerKey"])
        if profile:
            self.site_combo.blockSignals(True)
            self.site_combo.refresh(select=profile["server"])
            self.site_combo.blockSignals(False)
            self.status.setText(f"提取码 {profile['code']}"
                                + ("" if mine else "，他人的配置只能拉取"))
        else:
            self.status.setText(NO_PROFILE_TEXT)
        enabled = not self._running
        self.upload_btn.setEnabled(mine and enabled)
        self.restore_btn.setEnabled(bool(profile) and enabled)
        self.del_btn.setEnabled(bool(profile) and enabled)
        self.profile_combo.setEnabled(enabled and bool(self._profiles))
        self._save_state()

    def _server_changed(self, *_):
        """档案存在时换站点 = 换这条配置指向的服务器，重写档案与当前标识。"""
        profile = self._current()
        server = self.site_combo.current_server()
        if not profile or not server or server == profile["server"]:
            return
        old = _profile_id(profile["server"], profile["code"])
        profile["server"] = server
        self._active_id = _profile_id(server, profile["code"])
        self.profile_combo.blockSignals(True)
        index = self.profile_combo.findData(old)
        if index >= 0:
            self.profile_combo.setItemData(index, self._active_id)
        self.profile_combo.blockSignals(False)
        self._save_state()

    def _add_site(self, combo):
        _SiteAddDialog(self, lambda url: self._register_site(combo, url)).show()

    def _register_site(self, combo, url: str) -> None:
        server = cloud_backup.normalize_server(url)
        combo.add_server(server)
        if server not in self._servers:
            self._servers.append(server)
        if combo is not self.site_combo:
            return
        # 主下拉上新增站点：当前档案直接指过去
        profile = self._current()
        if profile:
            self._server_changed()
        self._custom_servers = cloud_backup.add_site(self._custom_servers, server)
        self._save_state()

    def _save_state(self) -> None:
        self._on_change({"cloudProfiles": self._profiles,
                         "cloudActiveProfile": self._active_id,
                         "cloudBackupServers": self._custom_servers})

    def _write_profile(self, profile: dict) -> None:
        pid = _profile_id(profile["server"], profile["code"])
        self._profiles = [
            p for p in self._profiles if _profile_id(p["server"], p["code"]) != pid]
        self._profiles.append(profile)
        self._custom_servers = cloud_backup.add_site(
            self._custom_servers, profile["server"])
        self._servers = cloud_backup.site_choices(self._custom_servers)
        self.site_combo.set_servers(self._servers)
        self._refresh_profiles(keep=pid)

    # ---- 建档 / 加入 / 删除 --------------------------------------------------

    def _new_profile(self):
        _NewProfileDialog(self, self._servers, self._created,
                          existing=self._profiles).show()

    def _created(self, profile: dict, password: str):
        self._write_profile(profile)
        self.pwd_edit.setText(password)
        self._upload()

    def _join_profile(self):
        _JoinProfileDialog(self, self._servers, self._joined,
                           existing=self._profiles).show()

    def _joined(self, profile: dict):
        self._write_profile(profile)

    def _delete_profile(self):
        profile = self._current()
        if not profile:
            return
        self._profiles = [
            p for p in self._profiles
            if _profile_id(p["server"], p["code"]) != self._active_id]
        self._active_id = ""
        self._refresh_profiles()
        InfoBar.info("已删除本地档案",
                     f"「{profile['name']}」不再出现在这里，云端的数据没有动",
                     duration=5000, parent=self)

    # ---- 动作 -------------------------------------------------------------

    def _profile_checked(self) -> dict | None:
        profile = self._current()
        if not profile:
            InfoBar.warning("还没有配置", "先新建一份配置，或加入他人的配置",
                            duration=4000, parent=self)
        return profile

    def _busy(self, running: bool, text: str = "") -> None:
        self._running = running
        if text:
            self.status.setText(text)
        enabled = not running
        profile = self._current()
        self.upload_btn.setEnabled(enabled and bool(profile) and bool(profile["ownerKey"]))
        self.restore_btn.setEnabled(enabled and bool(profile))
        self.del_btn.setEnabled(enabled and bool(profile))
        self.profile_combo.setEnabled(enabled)
        self.new_btn.setEnabled(enabled)
        self.join_btn.setEnabled(enabled)

    def _upload(self):
        profile = self._profile_checked()
        if not profile:
            return
        try:
            password = cloud_backup.normalize_password(self.pwd_edit.text())
        except ValueError as exc:
            InfoBar.warning("还不能上传", str(exc), duration=4000, parent=self)
            return
        if not profile["ownerKey"]:
            InfoBar.warning("这是他人的配置", "只有所属人能上传，你只能拉取恢复",
                            duration=5000, parent=self)
            return
        cfg = self._get_config()
        self._busy(True, f"正在上传 {profile['code']}")

        def job():
            envelope = cloud_backup.encrypt_config(cfg, profile["code"], password)
            cloud_backup.upload(profile["server"], profile["code"], envelope,
                                profile["ownerKey"])
            return profile

        self._run(job, lambda p: self._done(f"已上传「{p['name']}」"))

    def _restore(self):
        profile = self._profile_checked()
        if not profile:
            return
        password = self.pwd_edit.text().strip()
        self._busy(True, f"正在拉取 {profile['code']}")

        def job():
            envelope = cloud_backup.download(profile["server"], profile["code"])
            return cloud_backup.decrypt_config(envelope, profile["code"], password)

        def done(cfg):
            self._busy(False)
            self.accept()
            self._on_restore(cfg)

        self._run(job, done)

    def _done(self, message: str):
        self._busy(False, message)
        InfoBar.success("云端备份", message, duration=6000, parent=self)

    def _run(self, fn, on_done):
        self._worker = _Worker(fn, self)
        self._worker.done.connect(on_done)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _failed(self, message: str):
        self._busy(False, message)
        InfoBar.error("云端备份失败", message, duration=6000, parent=self)
