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
from app.services import login_gate, notify_schedule, store
from app.domain.account_health import HEALTH_LABEL, HEALTH_ROLE

# 表格里用的短标签：完整说明（含处理办法）放 tooltip
HEALTH_SHORT = {"captcha": "需验证码", "cooldown": "登录冷却"}
# 这几种状态用「密码 + 验证码」弹窗恢复；其余异常仍交给网页端
CAPTCHA_STATES = {"captcha", "failed", "cooldown"}
from app.services.api import SiteClient
from app.ui.captcha_dialog import CaptchaLoginDialog

THEME_INDEX = {"auto": 0, "light": 1, "dark": 2}
MATERIAL_ORDER = ("none", "acrylic", "mica")   # 下拉框顺序，与 _material_index 一致




class SettingsAccountMixin:
    """系统配置页的账号增删、表格渲染、选择与网页跳转行为。"""

    def _import_accounts(self):
        if getattr(self, "_importing", False):
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择账号文件", "", "表格或文本 (*.xlsx *.xlsm *.txt *.csv)")
        if not path:
            return
        # xlsx 解析放后台线程，大文件不卡界面；用实例标志防止重复导入，
        # 不再依赖某个具体按钮的禁用态（按钮可能来自弹窗内的新建实例）
        self._importing = True
        w = CallbackWorker(lambda: self._read_account_file(path), self)
        w.ok.connect(self._on_account_file_read)
        w.fail.connect(lambda msg: (InfoBar.error("读取失败", msg, duration=5000, parent=self),
                                    setattr(self, "_importing", False)))
        self._workers.append(w)
        w.start()

    def _on_account_file_read(self, rows: list):
        self._importing = False
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

    def _download_account_template(self):
        from PyQt5.QtWidgets import QFileDialog

        from app.services.export_data import default_name

        path, _ = QFileDialog.getSaveFileName(
            self, "保存账号模板",
            default_name("团支部账号模板", ".xlsx"), "Excel (*.xlsx)")
        if not path:
            return
        from app.services.excel import save_account_template

        n = save_account_template(path)
        InfoBar.success("模板已保存", f"{path}，{n} 行示例", duration=5000, parent=self)

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

    def _bind_account_table_menu(self):
        """账号表右键：打开网页 / 测活 / 复制账号 / 删除，全部作用于右键行。

        勾选存在时按勾选批量处理，和表头那排按钮口径一致；右键行只负责
        决定「打开网页」和「复制账号」的目标，不偷偷改动勾选状态。
        """

        def actions(row: int):
            def open_web():
                user = self._user_at_row(row)
                if user:
                    self.open_web_for(user)

            def copy_user():
                user = self._user_at_row(row)
                if user:
                    QApplication.clipboard().setText(user)
                    InfoBar.success("已复制账号", user, duration=2500, parent=self)

            refresh = Action(FIF.HEART, "测活选中账号" if self._checked_rows() else "测活此账号")
            refresh.triggered.connect(
                lambda: self.start_health_check(force=True, users=self._menu_users(row)))
            delete = Action(FIF.DELETE, "删除勾选账号" if self._checked_rows() else "删除此账号")
            delete.triggered.connect(lambda: self._remove_accounts(self._menu_rows(row)))
            return [
                Action(FIF.GLOBE, "打开网页", triggered=open_web),
                Action(FIF.COPY, "复制账号", triggered=copy_user),
                None,
                refresh,
                delete,
            ]

        def select(row: int):
            self.table.setCurrentCell(row, min(1, self.table.columnCount() - 1))

        bind_table_context_menu(self.table, actions, select_row=select)

    def _menu_users(self, row: int) -> list[str]:
        rows = self._checked_rows() or ([row] if row >= 0 else [])
        return [self._user_at_row(i) for i in rows if self._user_at_row(i)]

    def _menu_rows(self, row: int) -> list[int]:
        return self._checked_rows() or ([row] if row >= 0 else [])

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
            # delegate Python override，PyQt5 5.15 下会 shiboken 崩溃
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
                status_item.setForeground(P.fg("warning"))
            self.table.setItem(i, 5, status_item)
            h = acc.get("health") or {}
            status, msg = h.get("status", "unknown"), h.get("message", "")
            full = HEALTH_LABEL.get(status, status) + (f"：{msg}" if msg and status != "ok" else "")
            # 表格里只放短标签，长解释进 tooltip——否则这一列要么被截断要么把后面挤没
            health_item = QTableWidgetItem(HEALTH_SHORT.get(status, full))
            health_item.setFlags(self.READONLY_FLAGS)
            health_item.setForeground(P.fg(HEALTH_ROLE.get(status, "muted")))
            hint = ("点这一格填密码和验证码，登录成功后恢复"
                    if status in CAPTCHA_STATES
                    else "点这一格用内嵌浏览器打开，手动登录一次即可恢复")
            set_tip(health_item, full if status == "ok" else f"{full}\n{hint}")
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

    def open_web_for(self, user: str):
        """在「账号网页」窗口里打开指定账号。"""
        window = getattr(self, "window_ref", None)
        if window is None or not hasattr(window, "open_browser"):
            InfoBar.warning("无法打开", "账号网页窗口尚未就绪", duration=3000, parent=self)
            return
        window.open_browser(user)

    def _open_captcha_for(self, user: str):
        """弹验证码登录：账号行双击或点健康状态时走这里。"""
        acc = next((a for a in self.accounts if a.get("userName") == user), None)
        if acc is None:
            return
        health = acc.get("health") or {}
        reason = str(health.get("message") or "").strip()
        if health.get("status") in ("captcha", "cooldown"):
            reason = "需要输入图片验证码"
        label = user + (f"（{acc.get('remark')}）" if acc.get("remark") else "")
        dialog = CaptchaLoginDialog(
            SiteClient(user, str(acc.get("password") or "")), label, reason, self,
            on_success=lambda password, client, u=user: self._captcha_login_done(
                u, password, client),
            on_browser=lambda u=user: self.open_web_for(u))
        self._captcha_dialog = dialog
        dialog.finished.connect(lambda *_: setattr(self, "_captcha_dialog", None))
        dialog.show()

    def _captcha_login_done(self, user: str, password: str, client):
        """验证码登录成功：写回新密码、会话与健康状态。"""
        acc = next((a for a in self.accounts if a.get("userName") == user), None)
        if acc is None:
            return
        acc["password"] = password
        acc["health"] = {
            "status": "ok", "message": "手动登录成功",
            "checkedAt": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            login_gate.record_manual_login(client)
            login_gate.clear_browser_sessions([user])
        except (OSError, RuntimeError) as exc:
            InfoBar.warning("登录成功，会话没保存", str(exc), duration=5000, parent=self)
        self._client = None
        try:
            self._save_config()
        except (OSError, RuntimeError) as exc:
            InfoBar.warning("账号已登录，配置没写回", str(exc), duration=5000, parent=self)
        self._refresh_table()
        InfoBar.success("登录成功", f"{user} 已恢复登录状态", duration=4000, parent=self)

    def _user_at_row(self, row: int) -> str:
        if 0 <= row < len(self.accounts):
            return str(self.accounts[row].get("userName") or "")
        return ""

    def _on_cell_clicked(self, row: int, col: int):
        """健康状态不是正常时，点这一格恢复登录。"""
        if col != 6:
            return
        user = self._user_at_row(row)
        acc = next((a for a in self.accounts if a.get("userName") == user), None)
        if not user or acc is None:
            return
        status = (acc.get("health") or {}).get("status", "unknown")
        if status == "ok":
            return
        if status in CAPTCHA_STATES:
            self._open_captcha_for(user)
        else:
            self.open_web_for(user)

    def _on_cell_double_clicked(self, row: int, col: int):
        """双击账号行：验证码类异常直接弹窗，其余打开内嵌浏览器。"""
        user = self._user_at_row(row)
        if not user:
            return
        acc = next((a for a in self.accounts if a.get("userName") == user), None)
        status = ((acc or {}).get("health") or {}).get("status", "unknown")
        if status in CAPTCHA_STATES:
            self._open_captcha_for(user)
        else:
            self.open_web_for(user)

    def _open_web(self):
        """在「账号网页」窗口里打开勾选账号（未勾选则当前账号）。

        每个账号一份独立的浏览器存储，标签之间不会互相顶掉登录态；
        打开前先走登录接口拿到会话，再把 Cookie 注入对应标签页。
        """
        if not self.accounts:
            InfoBar.warning("没有账号", "还没有添加团支部账号", duration=2500, parent=self)
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

