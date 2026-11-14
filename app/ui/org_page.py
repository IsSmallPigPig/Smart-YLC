"""组织状况仪表盘：上传进度圆环 + 异常分类条形图 + 错误团员名单（可导出）。

异常分类：信息错误（报到身份）/ 没有报道（报到情况）/ 欠缴团费（团员缴费）。
名单只列错误级异常的团员，每行末尾是所属支部（账号备注，无备注回退账号名）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon as FIF,
    InfoBar,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    CaptionLabel,
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
from app.ui.page import CallbackWorker, FluentPage, note_label, theme_font, section_title

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
        from app.services import login_gate

        user, pwd = acc.get("userName", ""), acc.get("password", "")
        branch = acc.get("remark") or user
        if not user or not pwd:
            return user, branch, 0, 0, 0, {}, "empty", "账号或密码为空", None, []
        try:
            client = SiteClient(user, pwd)
            kind, msg = login_gate.ensure_logged_in(client)
            if kind != "ok":
                return user, branch, 0, 0, 0, {}, kind, msg, None, []
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
        self._snapshot = None  # 刷新前的展示快照（整轮失败时回滚）
        self._first_result = True
        self._ok_accounts = 0

        lay = self.vBoxLayout

        # 仪表盘卡片：左圆环进度 + 右异常分类条形图（含转接申请占比）
        card = CardWidget()
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(20, 16, 20, 16)
        card_lay.addWidget(section_title("组织状况总览"))
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

        # 错误团员卡片列表：每张卡片 = 姓名/支部 + 类别 + 问题说明；账号级失败单独成卡
        self.error_box = QWidget()
        self.error_lay = QVBoxLayout(self.error_box)
        self.error_lay.setContentsMargins(0, 0, 0, 0)
        self.error_lay.setSpacing(8)
        self.error_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.error_title = section_title("错误团员（0）")
        self.add_table(self.error_box, 1)
        self.error_lay.addWidget(self.error_title)

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
        # 保留现有展示：归零累加器但不重绘，新账号结果陆续抵达时逐个替换；
        # 整轮一无所获（全失败/全跳过）时回滚快照，组织状况数据不丢
        self._snapshot = (
            self._total, self._uploaded, dict(self._by_kind), self._members_max,
            self._transfer_applied, self.transfer_note.text(), list(self._error_rows))
        self._total = self._uploaded = 0
        self._by_kind = {"identity": 0, "report": 0, "fees": 0}
        self._members_max = 10
        self._transfer_applied = 0
        self._error_rows = []
        self._first_result = True
        self._ok_accounts = 0
        self.worker = OrgWorker(accounts)
        self.worker.account_done.connect(self._on_account)
        self.worker.all_done.connect(self._on_run_done)
        self.worker.start()

    def _on_run_done(self):
        self.start_btn.setEnabled(True)
        if self._ok_accounts == 0:
            # 这一轮没有拿到任何账号数据：恢复刷新前的组织状况
            (self._total, self._uploaded, byk, self._members_max,
             self._transfer_applied, note, rows) = self._snapshot
            self._by_kind = byk
            self._error_rows = rows
            self.transfer_note.setText(note)
            self._set_ratio(self._total, self._uploaded)
            self._update_bars()
            self._clear_cards()
            for r in rows:
                self._append_card(r.name, r.mobile, [k for k in KIND_COLOR if KIND_LABEL[k] in r.kinds],
                                  r.problems, r.branch, r.kinds)
            self.error_title.setText(f"错误团员（{len(rows)}）")

    def refresh_offline(self):
        """从成员缓存（memberCache）离线重算三项统计，无网络请求。

        成员同步拿到新数据后由 30 秒快刷调用；转接统计保留最近一次在线结果，
        现有展示直接被新数据整体替换（缓存为空则不动）。"""
        from app.services import store

        cache = store.load().get("memberCache") or {}
        if not cache:
            return
        from app.domain.org_manage import record_from_dict

        total = uploaded = 0
        by_kind = {"identity": 0, "report": 0, "fees": 0}
        members_max = 10
        error_rows: list = []
        id_tpl, rep_tpl = default_identity_template(), default_report_template()
        for user, blob in cache.items():
            members = [record_from_dict(d) for d in blob.get("members", [])]
            branch = blob.get("branch") or user
            results = (
                build_identity_results(members, id_tpl)
                + build_report_results(members, rep_tpl)
                + build_fee_results(members)
            )
            s = summarize_members(members, results)
            total += s.total
            uploaded += s.uploaded
            members_max = max(members_max, s.total)
            for k, v in dict(s.by_kind).items():
                by_kind[k] = by_kind.get(k, 0) + v
            error_rows.extend(build_error_rows(results, branch))
        self._total, self._uploaded = total, uploaded
        self._by_kind = by_kind
        self._members_max = members_max
        self._error_rows = error_rows
        self._set_ratio(total, uploaded)
        self._update_bars()
        self._clear_cards()
        for r in error_rows:
            self._append_card(r.name, r.mobile, [k for k in KIND_COLOR if KIND_LABEL[k] in r.kinds],
                              r.problems, r.branch, r.kinds)
        self.error_title.setText(f"错误团员（{len(error_rows)}）")
        if error_rows:
            self.export_btn.setEnabled(True)

    def _on_account(self, user, branch, total, uploaded, abnormal, by_kind, status, message, transfer, rows):
        if self._first_result:
            # 新数据开始抵达：旧卡片让位（本轮一无所获时 _on_run_done 会回滚）
            self._first_result = False
            self._clear_cards()
        self._ok_accounts += status == "ok"
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
            # 账号级失败：不参与名单，单独一张卡片说明，避免静默丢账号
            self._append_card("—", "—", [], f"账号 {user or '（空）'} 检查失败："
                              f"{STATUS_LABEL.get(status, status)}：{message}", branch, None)
        for r in rows:
            self._error_rows.append(r)
            self._append_card(r.name, r.mobile, [k for k in KIND_COLOR if KIND_LABEL[k] in r.kinds],
                              r.problems, r.branch, r.kinds)
        self.error_title.setText(f"错误团员（{len(self._error_rows)}）")
        if self._error_rows:
            self.export_btn.setEnabled(True)

    def _clear_cards(self):
        while self.error_lay.count() > 1:  # 保留标题
            item = self.error_lay.takeAt(self.error_lay.count() - 1)
            if item.widget():
                item.widget().deleteLater()
        self.error_title.setText("错误团员（0）")

    def _append_card(self, name, mobile, kinds: list, problem, branch, kinds_text):
        """一张错误团员卡片：左侧姓名+手机号，右侧支部；下方类别与问题。"""
        card = CardWidget()
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 12, 18, 12)
        v.setSpacing(6)

        head = QHBoxLayout()
        title = theme_font(StrongBodyLabel(name or "—"), 15, int(QFont.Weight.DemiBold))
        head.addWidget(title)
        if mobile and mobile != "—":
            head.addSpacing(8)
            head.addWidget(CaptionLabel(mobile))
        head.addStretch(1)
        if kinds:
            for k in kinds:
                chip = BodyLabel(KIND_LABEL[k])
                # 小底色圆角标签承载白字，不改字体颜色本身
                chip.setStyleSheet(
                    f"background-color: {KIND_COLOR[k]}; color: white;"
                    "border-radius: 4px; padding: 1px 8px;")
                head.addWidget(chip)
        head.addSpacing(12)
        branch_lbl = CaptionLabel(branch or "—")
        branch_lbl.setTextColor(QColor("#4b5563"), QColor("#9ca3af"))
        head.addWidget(branch_lbl)
        v.addLayout(head)

        body = BodyLabel(problem or "—")
        body.setWordWrap(True)
        if kinds is None:  # 账号级失败
            body.setTextColor(QColor("#c62828"), QColor("#ef5350"))
        v.addWidget(body)

        self.error_lay.addWidget(card)

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

        rows = list(self._error_rows)
        self.export_btn.setEnabled(False)

        def _ok(n: int):
            self.export_btn.setEnabled(True)
            self._workers = [w for w in getattr(self, "_workers", []) if w is not worker]
            InfoBar.success("已导出", f"{n} 名错误团员 → {path}", duration=5000, parent=self)

        def _fail(msg: str):
            self.export_btn.setEnabled(True)
            InfoBar.error("导出失败", msg, duration=5000, parent=self)

        worker = CallbackWorker(lambda: save_error_rows(path, rows), self)
        worker.ok.connect(_ok)
        worker.fail.connect(_fail)
        self._workers = getattr(self, "_workers", []) + [worker]
        worker.start()
