"""批量检查页：报到身份 / 报到情况 / 团员缴费 三项核对 + 异常导出 xlsx。

范围选择：
- 名单：下拉选学期（对照该学期名单交叉校验）或全部名单（合并）
- 支部：下拉选单个团支部账号，或全部支部（并发逐账号检查）
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QTableWidgetItem
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    PrimaryPushButton,
    PushButton,
    TableWidget,
)

from app.ui.page import FluentPage
from app.domain.check_rules import (
    CheckResult,
    CheckTemplate,
    TemplateField,
    build_fee_results,
    build_identity_results,
    build_report_results,
    cross_check_roster,
)
from app.services.api import SiteClient

KIND_LABEL = {"identity": "报到身份", "report": "报到情况", "fees": "团员缴费"}
ALL_ROSTER = "全部名单（合并各学期）"
ALL_ORG = "全部支部"
READONLY_FLAGS = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


def default_identity_template() -> CheckTemplate:
    # 判定词对照站点真实枚举：isRealName→通过。
    # 站点语义：团员证照片未上传不算异常（站点自身显示「无异常」），志愿书状态只用于上传进度。
    # 职业检查在 check_rules._occupation_diff（未填→警告，非学生→错误）；
    # 这里只放收入一致性：收入应为「职业为学生无收入」，不一致（含为空）→ 错误。
    return CheckTemplate("identity", "报到身份", [
        TemplateField("real_name_status", "实名认证", "通过", "contains", True, "error"),
        TemplateField("income", "收入", "学生无收入", "contains", True, "error"),
    ])


def default_report_template() -> CheckTemplate:
    return CheckTemplate("report", "报到情况", [
        TemplateField("report_data_status", "报到资料状态", "审核通过", "contains", True, "error"),
    ])


class CheckWorker(QThread):
    """并发逐账号检查：会话探测 → 失效才登录 → 拉取团员 → 三项核对。

    单账号失败（密码/网络）只影响它自己，其余账号照常出结果；
    全部完成后用合并的团员列表对照名单做交叉校验。"""

    account_done = Signal(str, list, str)  # userName, results, error
    done = Signal(object, object, object)  # all_results, diags, cross
    MAX_WORKERS = 4

    def __init__(self, accounts: list[dict], roster):
        super().__init__()
        self.accounts = accounts
        self.roster = roster
        self._stop = False

    def stop(self):
        self._stop = True

    def _check_one(self, acc: dict) -> tuple[str, list, list, list, str]:
        """返回 (user, members, results, diags, error)。"""
        user, pwd = acc.get("userName", ""), acc.get("password", "")
        if not user or not pwd:
            return user, [], [], [], "账号或密码为空"
        try:
            client = SiteClient(user, pwd)
            probe = client.session_account()
            if not probe.ok and not client.logged_in:
                verdict = client.login()
                if not verdict.ok:
                    return user, [], [], [], verdict.message
            members, diags = client.get_all_members()
            results: list[CheckResult] = []
            results += build_identity_results(members, default_identity_template())
            results += build_report_results(members, default_report_template())
            results += build_fee_results(members)
            return user, members, results, diags, ""
        except Exception as e:
            return user, [], [], [], str(e)

    def run(self):
        all_results: list[CheckResult] = []
        all_diags: list = []
        all_members: list = []
        with ThreadPoolExecutor(max_workers=min(self.MAX_WORKERS, max(1, len(self.accounts)))) as pool:
            futures = [pool.submit(self._check_one, dict(a)) for a in self.accounts]
            for fut in as_completed(futures):
                if self._stop:
                    break
                try:
                    user, members, results, diags, err = fut.result()
                except Exception as e:
                    self.account_done.emit("", [], f"检查线程异常：{e}")
                    continue
                self.account_done.emit(user, results, err)
                if err:
                    continue
                all_members.extend(members)
                all_results.extend(results)
                all_diags.extend(diags or [])
        if self._stop:
            return
        cross = cross_check_roster(all_members, self.roster, "all", []) if all_members else None
        self.done.emit(all_results, all_diags, cross)


class CheckPage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__('page-check', parent)
        self.window_ref = window
        self.results: list[CheckResult] = []
        self._all_results: list[CheckResult] = []  # 当前检查项的全量结果（未按级别过滤）
        self._errors: list[tuple[str, str]] = []  # (user, error)，账号失败行
        lay = self.vBoxLayout

        # 范围选择：名单（学期）+ 支部
        row0 = QHBoxLayout()
        row0.addWidget(BodyLabel("名单"))
        self.roster_combo = ComboBox()
        row0.addWidget(self.roster_combo, 1)
        row0.addSpacing(12)
        row0.addWidget(BodyLabel("支部"))
        self.org_combo = ComboBox()
        row0.addWidget(self.org_combo, 1)
        row0.addSpacing(12)
        row0.addWidget(BodyLabel("显示"))
        self.level_combo = ComboBox()
        self.level_combo.addItems(["全部", "仅错误", "仅警告"])
        self.level_combo.currentIndexChanged.connect(self._apply_level_filter)
        row0.addWidget(self.level_combo)
        lay.addLayout(row0)

        row = QHBoxLayout()
        self.kind_combo = ComboBox()
        self.kind_combo.addItems(["报到身份", "报到情况", "团员缴费", "全部"])
        self.scan_btn = PrimaryPushButton(FIF.SEARCH, "开始检查")
        self.scan_btn.clicked.connect(self._start)
        self.export_btn = PushButton(FIF.SAVE, "导出异常清单 xlsx")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        self.clear_btn = PushButton(FIF.DELETE, "清除结果")
        self.clear_btn.clicked.connect(self._clear_results)
        row.addWidget(self.kind_combo, 1)
        row.addWidget(self.scan_btn)
        row.addWidget(self.export_btn)
        row.addWidget(self.clear_btn)
        lay.addLayout(row)

        self.stat = BodyLabel("名单选学期即对照该学期名单；支部选单个团支部或全部；只读查询，不修改数据")
        lay.addWidget(self.stat)

        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["检查项", "姓名", "手机号", "团支部", "异常点", "判定依据"])
        self.add_table(self.table, 1)

    # ---------- 范围下拉 ----------

    def _refresh_scope(self):
        """开始检查前按系统配置刷新两个下拉，尽量保留当前选择。"""
        sp = self.window_ref.settings_page
        cur_roster, cur_org = self.roster_combo.currentText(), self.org_combo.currentText()

        sems = sorted(sp.rosters)
        self.roster_combo.blockSignals(True)
        self.roster_combo.clear()
        self.roster_combo.addItems([ALL_ROSTER] + sems)
        if cur_roster in ([ALL_ROSTER] + sems):
            self.roster_combo.setCurrentText(cur_roster)
        self.roster_combo.blockSignals(False)

        labels = [f"{a['userName']}" + (f"（{a.get('remark')}）" if a.get("remark") else "")
                  for a in sp.accounts]
        self.org_combo.blockSignals(True)
        self.org_combo.clear()
        self.org_combo.addItems([ALL_ORG] + labels)
        if cur_org in ([ALL_ORG] + labels):
            self.org_combo.setCurrentText(cur_org)
        self.org_combo.blockSignals(False)

    def _selected_accounts(self) -> list[dict]:
        sp = self.window_ref.settings_page
        org = self.org_combo.currentText()
        if org == ALL_ORG or not org:
            return [dict(a) for a in sp.accounts]
        user = org.split("（")[0]
        return [dict(a) for a in sp.accounts if a["userName"] == user]

    def _selected_roster(self):
        sp = self.window_ref.settings_page
        sem = self.roster_combo.currentText()
        if sem == ALL_ROSTER or not sem:
            return sp.roster  # 合并名单
        return sp.rosters.get(sem)  # 该学期名单；未导入则 None

    # ---------- 检查流程 ----------

    def _start(self):
        self._refresh_scope()
        accounts = self._selected_accounts()
        if not accounts:
            InfoBar.warning("没有可检查的支部", "请先到「系统配置」添加团支部账号", duration=3000, parent=self)
            return
        self.scan_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self._errors: list[tuple[str, str]] = []
        self.table.setRowCount(0)
        self.results = []
        scope = self.org_combo.currentText()
        self.stat.setText(f"正在检查 {scope}（{len(accounts)} 个账号，并发进行）…")
        self.worker = CheckWorker(accounts, self._selected_roster())
        self.worker.account_done.connect(self._on_account_done)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _append_error_row(self, user: str, msg: str):
        r = self.table.rowCount()
        self.table.setRowCount(r + 1)
        row = [f"{user or '（空）'}　⚠ {msg}", "-", "-", "-", "-", "-"]
        for col, v in enumerate(row):
            item = QTableWidgetItem(v)
            item.setFlags(READONLY_FLAGS)
            if col == 0:
                item.setForeground(QColor("#c62828"))
            self.table.setItem(r, col, item)

    def _on_account_done(self, user: str, results: list, error: str):
        if error:
            self._errors.append((user, error))
            self._append_error_row(user, error)

    def _on_done(self, results, diags, cross):
        self.scan_btn.setEnabled(True)
        kind_idx = self.kind_combo.currentIndex()
        if kind_idx < 3:
            key = ("identity", "report", "fees")[kind_idx]
            results = [r for r in results if r.kind == key]
        self._all_results = results
        self._apply_level_filter()
        cross_msg = cross.message if cross else "无名单对照"
        n_err = sum(1 for r in results if r.is_error)
        n_warn = len(results) - n_err
        warn_msg = f"；另有 {n_warn} 条警告" if n_warn else ""
        self.stat.setText(
            f"{cross_msg}；错误 {n_err} 条{warn_msg}"
            + (f"（另有 {len(diags)} 条解析诊断）" if diags else "")
        )
        InfoBar.success("检查完成", f"错误 {n_err} 条{warn_msg}，{cross_msg}", duration=4000, parent=self)

    def _apply_level_filter(self, *_):
        """按级别下拉重建表格（账号错误行始终保留）。"""
        results = self._all_results
        idx = self.level_combo.currentIndex()
        if idx == 1:
            results = [r for r in results if r.is_error]
        elif idx == 2:
            results = [r for r in results if not r.is_error]
        self.results = results
        self.table.setRowCount(0)
        for user, err in self._errors:
            self._append_error_row(user, err)
        self._fill_results(results)
        self.export_btn.setEnabled(bool(results))

    def _fill_results(self, results):
        # 必须先扩行：QTableWidget 对超出 rowCount 的 setItem 会静默丢弃
        self.table.setRowCount(len(results))
        for i, r in enumerate(results):
            m = r.member
            point = "；".join(d.reason for d in r.diffs) or (r.basis if r.kind == "fees" else "")
            level = "错误" if r.is_error else "警告"
            row = [KIND_LABEL[r.kind], m.name, m.mobile, m.org_name or m.oid or "-", f"[{level}] {point}", r.basis or "与模板不一致"]
            for col, v in enumerate(row):
                item = QTableWidgetItem(v)
                item.setFlags(READONLY_FLAGS)
                if col == 4:
                    item.setForeground(QColor("#c62828" if r.is_error else "#ef6c00"))
                self.table.setItem(i, col, item)

    def _clear_results(self):
        self.table.setRowCount(0)
        self.results = []
        self._all_results = []
        self._errors = []
        self.export_btn.setEnabled(False)
        self.stat.setText("名单选学期即对照该学期名单；支部选单个团支部或全部；只读查询，不修改数据")

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出异常清单", "异常清单.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "异常清单"
        ws.append(["检查项", "姓名", "手机号", "团支部", "级别", "异常点", "判定依据"])
        for r in self.results:
            m = r.member
            point = "；".join(d.reason for d in r.diffs) or (r.basis if r.kind == "fees" else "")
            level = "错误" if r.is_error else "警告"
            ws.append([KIND_LABEL[r.kind], m.name, m.mobile, m.org_name or m.oid, level, point, r.basis or "与模板不一致"])
        wb.save(path)
        InfoBar.success("导出完成", path, duration=4000, parent=self)
