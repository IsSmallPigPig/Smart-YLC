"""批量检查页：报到身份 / 报到情况 / 团员缴费 三项核对 + 异常导出 xlsx。"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTableWidgetItem
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

KIND_LABEL = {"identity": "报到身份", "report": "报到情况", "fees": "团员缴费"}


def default_identity_template() -> CheckTemplate:
    return CheckTemplate("identity", "报到身份", [
        TemplateField("real_name_status", "实名认证", "已认证", "contains"),
        TemplateField("ebook_status", "电子志愿书", "已上传", "contains"),
        TemplateField("ecard_photo_status", "团员证照片", "已上传", "contains"),
    ])


def default_report_template() -> CheckTemplate:
    return CheckTemplate("report", "报到情况", [
        TemplateField("report_data_status", "报到资料状态", "已审核", "contains"),
        TemplateField("report_apply_at", "报到申请时间", "", "notEmpty"),
        TemplateField("report_audit_at", "报到审核时间", "", "notEmpty"),
    ])


class CheckWorker(QThread):
    progress = Signal(int, int, str)
    done = Signal(object, object)  # results, cross
    failed = Signal(str)

    def __init__(self, client, scope_oid: str, roster):
        super().__init__()
        self.client = client
        self.scope_oid = scope_oid
        self.roster = roster

    def run(self):
        try:
            members, diags = self.client.get_all_members(
                self.scope_oid,
                progress=lambda done, total: self.progress.emit(done, total, "拉取团员列表"),
            )
        except Exception as e:
            self.failed.emit(str(e))
            return
        results: list[CheckResult] = []
        results += build_identity_results(members, default_identity_template())
        results += build_report_results(members, default_report_template())
        results += build_fee_results(members)
        scope_oids = [self.scope_oid] if self.scope_oid else []
        cross = cross_check_roster(members, self.roster, "specified" if scope_oids else "all", scope_oids)
        self.done.emit((results, diags), cross)


class CheckPage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__('page-check', parent)
        self.window_ref = window
        self.results: list[CheckResult] = []
        lay = self.vBoxLayout

        row = QHBoxLayout()
        self.kind_combo = ComboBox()
        self.kind_combo.addItems(["报到身份", "报到情况", "团员缴费", "全部"])
        self.scan_btn = PrimaryPushButton(FIF.SEARCH, "开始检查")
        self.scan_btn.clicked.connect(self._start)
        self.export_btn = PushButton(FIF.SAVE, "导出异常清单 xlsx")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        row.addWidget(self.kind_combo, 1)
        row.addWidget(self.scan_btn)
        row.addWidget(self.export_btn)
        lay.addLayout(row)

        self.stat = BodyLabel("登录账号来自「系统配置」；拉取的是只读查询接口，不会修改任何数据")
        lay.addWidget(self.stat)

        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["检查项", "姓名", "手机号", "团支部", "异常点", "判定依据"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.add_table(self.table, 1)

    def _start(self):
        client = self.window_ref.settings_page.client()
        if client is None:
            InfoBar.error("未配置账号", "请先到「系统配置」填写团支部账号", duration=3000, parent=self)
            return
        verdict = client.session_account()
        if not verdict.ok and not client.logged_in:
            verdict = client.login()
            if not verdict.ok:
                InfoBar.error("登录失败", verdict.message, duration=4000, parent=self)
                return
        self.scan_btn.setEnabled(False)
        self.stat.setText("正在拉取团员列表…")
        scope_oid = self.window_ref.settings_page.scope_oid()
        self.worker = CheckWorker(client, scope_oid, self.window_ref.settings_page.roster)
        self.worker.progress.connect(
            lambda done, total, tag: self.stat.setText(f"{tag} {done}/{total or '…'}"))
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_failed(self, msg: str):
        self.scan_btn.setEnabled(True)
        self.stat.setText(f"检查失败：{msg}")
        InfoBar.error("检查失败", msg, duration=5000, parent=self)

    def _on_done(self, payload, cross):
        self.scan_btn.setEnabled(True)
        results, diags = payload
        kind_idx = self.kind_combo.currentIndex()
        if kind_idx < 3:
            key = ("identity", "report", "fees")[kind_idx]
            results = [r for r in results if r.kind == key]
        self.results = results
        self.table.setRowCount(len(results))
        for i, r in enumerate(results):
            m = r.member
            point = "；".join(d.reason for d in r.diffs) or (r.basis if r.kind == "fees" else "")
            self.table.setItem(i, 0, QTableWidgetItem(KIND_LABEL[r.kind]))
            self.table.setItem(i, 1, QTableWidgetItem(m.name))
            self.table.setItem(i, 2, QTableWidgetItem(m.mobile))
            self.table.setItem(i, 3, QTableWidgetItem(m.org_name or m.oid or "-"))
            self.table.setItem(i, 4, QTableWidgetItem(point))
            self.table.setItem(i, 5, QTableWidgetItem(r.basis or "与模板不一致"))
            self.table.item(i, 0).setForeground(QColor("#c62828"))
        self.export_btn.setEnabled(bool(results))
        self.stat.setText(
            f"{cross.message}；异常 {len(results)} 条"
            + (f"（另有 {len(diags)} 条解析诊断）" if diags else "")
        )
        InfoBar.success("检查完成", f"异常 {len(results)} 条，{cross.message}", duration=4000, parent=self)

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出异常清单", "异常清单.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "异常清单"
        ws.append(["检查项", "姓名", "手机号", "团支部", "异常点", "判定依据"])
        for r in self.results:
            m = r.member
            point = "；".join(d.reason for d in r.diffs) or (r.basis if r.kind == "fees" else "")
            ws.append([KIND_LABEL[r.kind], m.name, m.mobile, m.org_name or m.oid, point, r.basis or "与模板不一致"])
        wb.save(path)
        InfoBar.success("导出完成", path, duration=4000, parent=self)
