"""组织状况仪表盘：上传进度圆环 + 异常分类条形图 + 错误团员名单（可导出）。

异常分类：信息错误（报到身份）/ 没有报道（报到情况）/ 欠缴团费（团员缴费）。
名单只列错误级异常的团员，每行末尾是所属支部（账号备注，无备注回退账号名）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QTableWidgetItem, QVBoxLayout

from qfluentwidgets import (
    CardWidget,
    FluentIcon as FIF,
    InfoBar,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TableWidget,
)

from app.domain.check_rules import (
    build_fee_results,
    build_identity_results,
    build_report_results,
)
from app.domain.org_stats import KIND_LABEL, build_error_rows, summarize_members
from app.domain.transfer import fetch_transfer_applies, is_graduated
from app.services.api import SiteClient
from app.ui.charts import BarRowWidget, ProgressDonut
from app.ui.check_page import default_identity_template, default_report_template
from app.ui.page import FluentPage, note_label

STATUS_LABEL = {
    "ok": "正常",
    "failed": "密码错误",
    "captcha": "需验证码",
    "passwordChange": "需改密",
    "needMemberName": "需姓名校验",
    "network": "网络错误",
    "empty": "未配置",
}

KIND_COLOR = {"identity": "#ef6c00", "report": "#c62828", "fees": "#8e24aa"}
TRANSFER_COLOR = "#1565c0"


class OrgWorker(QThread):
    """并发逐账号：会话探测 → 失效才登录 → 拉取团员 → 三项检查 + 错误团员名单 + 转接统计。只读查询。

    单账号失败（密码错误/网络异常）只影响它自己一行，不阻塞其他账号。"""

    account_done = Signal(str, str, int, int, int, dict, str, str, object, object)
    # user, branch, total, uploaded, abnormal, by_kind, status, message, TransferSummary|None, error_rows
    all_done = Signal()

    MAX_WORKERS = 4  # 团员列表是重接口，并发数低于测活

    def __init__(self, accounts: list[dict]):
        super().__init__()
        self.accounts = accounts
        self._stop = False

    def stop(self):
        self._stop = True

    def _check_one(self, acc: dict):
        user, pwd = acc.get("userName", ""), acc.get("password", "")
        branch = acc.get("remark") or user
        if not user or not pwd:
            return user, branch, 0, 0, 0, {}, "empty", "账号或密码为空", None, []
        try:
            client = SiteClient(user, pwd)
            probe = client.session_account()
            if not probe.ok:
                verdict = client.login()
                kind = str((verdict.data or {}).get("kind", "failed")) if isinstance(verdict.data, dict) else "failed"
                if kind != "ok":
                    return user, branch, 0, 0, 0, {}, kind, verdict.message, None, []
            members, _ = client.get_all_members()
            id_tpl, rep_tpl = default_identity_template(), default_report_template()
            results = (
                build_identity_results(members, id_tpl)
                + build_report_results(members, rep_tpl)
                + build_fee_results(members)
            )
            s = summarize_members(members, results)
            rows = build_error_rows(results, branch)
            # 转接申请统计（作为转出方，按支部过滤）
            oid = members[0].oid if members else ""
            transfer = fetch_transfer_applies(client, oid)
            transfer.total = s.total
            if is_graduated(acc):
                user = f"{user}（毕业应转出）"
            return user, branch, s.total, s.uploaded, s.abnormal, dict(s.by_kind), "ok", "", transfer, rows
        except Exception as e:
            return user, branch, 0, 0, 0, {}, "network", str(e), None, []

    def run(self):
        workers = min(self.MAX_WORKERS, max(1, len(self.accounts)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._check_one, dict(a)) for a in self.accounts]
            for fut in as_completed(futures):
                if self._stop:
                    break
                try:
                    self.account_done.emit(*fut.result())
                except Exception as e:
                    self.account_done.emit("", "", 0, 0, 0, {}, "network", f"统计线程异常：{e}", None, [])
        self.all_done.emit()


class OrgPage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__("page-org", parent)
        self.window_ref = window
        self.worker: OrgWorker | None = None
        self._total = 0
        self._uploaded = 0
        self._by_kind = {"identity": 0, "report": 0, "fees": 0}
        self._members_max = 10
        self._error_rows: list = []
        self._transfer_applied = 0

        lay = self.vBoxLayout

        # 仪表盘卡片：左圆环进度 + 右异常分类条形图（含转接申请占比）
        card = CardWidget()
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(20, 16, 20, 16)
        card_lay.addWidget(StrongBodyLabel("组织状况总览"))
        card_lay.addWidget(note_label(
            "已上传依据「电子志愿书状态」；异常只统计错误级（职业未填等仅警告不计入）；"
            "转接申请 = 已发起组织关系转接的团员 / 在册总数，按人去重"))
        card_lay.addSpacing(4)

        dash = QHBoxLayout()
        self.donut = ProgressDonut()
        dash.addWidget(self.donut)
        dash.addSpacing(20)
        bars = QVBoxLayout()
        bars.setSpacing(4)
        self.bars: dict[str, BarRowWidget] = {}
        for kind in ("identity", "report", "fees"):
            bar = BarRowWidget(KIND_LABEL[kind], KIND_COLOR[kind])
            self.bars[kind] = bar
            bars.addWidget(bar)
        self.transfer_bar = BarRowWidget("转接申请", TRANSFER_COLOR)
        bars.addWidget(self.transfer_bar)
        bars.addStretch(1)
        dash.addLayout(bars, 1)
        card_lay.addLayout(dash)
        card_lay.addWidget(note_label("转接申请：已发起组织关系转接（含待审核/接转成功/被退回）的团员数 / 在册总数"))
        self.transfer_note = note_label("转接明细：—")
        card_lay.addWidget(self.transfer_note)

        row = QHBoxLayout()
        self.start_btn = PrimaryPushButton(FIF.SYNC, "开始统计")
        self.start_btn.clicked.connect(self._start)
        self.export_btn = PushButton(FIF.SAVE, "导出表格")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        row.addWidget(self.start_btn)
        row.addWidget(self.export_btn)
        row.addStretch(1)
        card_lay.addLayout(row)
        lay.addWidget(card)

        # 错误团员名单表：只读，末列为所属支部；账号级失败也占一行便于排查
        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(False)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["姓名", "手机号码", "类别", "问题", "支部"])
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in (0, 1, 2, 4):
            header.setSectionResizeMode(col, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, header.ResizeMode.Stretch)
        self.add_table(self.table, 1)

    def _set_ratio(self, total: int, uploaded: int):
        self.donut.set_value(uploaded, total)

    def _update_bars(self):
        for kind, bar in self.bars.items():
            bar.set_value(self._by_kind.get(kind, 0), self._members_max)
        self.transfer_bar.set_value(self._transfer_applied, self._members_max)

    def _start(self):
        accounts = self.window_ref.settings_page.accounts
        if not accounts:
            InfoBar.warning("未配置账号", "请先到「系统配置」添加团支部账号", duration=3000, parent=self)
            return
        self.start_btn.setEnabled(False)
        self._total = self._uploaded = 0
        self._by_kind = {"identity": 0, "report": 0, "fees": 0}
        self._members_max = 10
        self._transfer_applied = 0
        self._error_rows = []
        self.transfer_note.setText("转接明细：—")
        self.table.setRowCount(0)
        self.export_btn.setEnabled(False)
        self._set_ratio(0, 0)
        self._update_bars()
        self.worker = OrgWorker(accounts)
        self.worker.account_done.connect(self._on_account)
        self.worker.all_done.connect(lambda: self.start_btn.setEnabled(True))
        self.worker.start()

    def _on_account(self, user, branch, total, uploaded, abnormal, by_kind, status, message, transfer, rows):
        self._total += total
        self._uploaded += uploaded
        self._set_ratio(self._total, self._uploaded)
        if total:
            self._members_max = max(self._members_max, total)
        for k, v in (by_kind or {}).items():
            self._by_kind[k] = self._by_kind.get(k, 0) + v
        if transfer:
            self._transfer_applied += transfer.applied
            detail = f"：{'、'.join(transfer.names[:6])}" if transfer.names else ""
            self.transfer_note.setText(
                f"转接明细：已申请 {self._transfer_applied} 人 / 在册 {self._total} 人"
                f"{detail}（接转成功 {transfer.succeeded} · 审核中 {transfer.pending} · 被退回 {transfer.returned}）")
        self._update_bars()

        if status != "ok":
            # 账号级失败：不参与名单，只占一行提示，避免静默丢账号
            self._append_row("—", "—", "—", f"账号 {user or '（空）'} 检查失败："
                             f"{STATUS_LABEL.get(status, status)}：{message}", branch, None)
        for r in rows:
            self._error_rows.append(r)
            self._append_row(r.name, r.mobile, r.kinds, r.problems, r.branch, r.kinds)
        if self._error_rows:
            self.export_btn.setEnabled(True)

    def _append_row(self, name, mobile, kind, problem, branch, kind_for_color):
        r = self.table.rowCount()
        self.table.setRowCount(r + 1)
        vals = [name or "—", mobile or "—", kind or "—", problem or "—", branch or "—"]
        readonly = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        color = QColor(next((KIND_COLOR[k] for k in KIND_COLOR if k in (kind_for_color or "")), None)) \
            if kind_for_color else None
        for col, v in enumerate(vals):
            item = QTableWidgetItem(v)
            item.setFlags(readonly)
            if color is not None and col == 2:
                item.setForeground(color)
            self.table.setItem(r, col, item)

    def _export(self):
        if not self._error_rows:
            InfoBar.warning("没有可导出的名单", "先「开始统计」，有错误团员后才能导出", duration=3000, parent=self)
            return
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self, "导出错误团员名单", "错误团员名单.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        from app.services.excel import save_error_rows

        n = save_error_rows(path, self._error_rows)
        InfoBar.success("已导出", f"{n} 名错误团员 → {path}", duration=5000, parent=self)
