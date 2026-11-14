"""组织状况仪表盘：上传进度圆环 + 异常分类条形图 + 逐账号明细。

异常分类：信息错误（报到身份）/ 没有报道（报到情况）/ 欠缴团费（团员缴费）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import QHBoxLayout, QTableWidgetItem, QVBoxLayout

from qfluentwidgets import (
    CardWidget,
    FluentIcon as FIF,
    InfoBar,
    PrimaryPushButton,
    StrongBodyLabel,
    TableWidget,
)

from app.domain.check_rules import (
    build_fee_results,
    build_identity_results,
    build_report_results,
)
from app.domain.org_stats import KIND_LABEL, summarize_members
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


class OrgWorker(QThread):
    """并发逐账号：会话探测 → 失效才登录 → 拉取团员 → 三项检查 → 统计。只读查询。

    单账号失败（密码错误/网络异常）只影响它自己一行，不阻塞其他账号。"""

    account_done = Signal(str, int, int, int, dict, str, str)
    # user, total, uploaded, abnormal, by_kind, status, message
    all_done = Signal()

    MAX_WORKERS = 4  # 团员列表是重接口，并发数低于测活

    def __init__(self, accounts: list[dict]):
        super().__init__()
        self.accounts = accounts
        self._stop = False

    def stop(self):
        self._stop = True

    def _check_one(self, acc: dict) -> tuple[str, int, int, int, dict, str, str]:
        user, pwd = acc.get("userName", ""), acc.get("password", "")
        if not user or not pwd:
            return user, 0, 0, 0, {}, "empty", "账号或密码为空"
        try:
            client = SiteClient(user, pwd)
            probe = client.session_account()
            if not probe.ok:
                verdict = client.login()
                kind = str((verdict.data or {}).get("kind", "failed")) if isinstance(verdict.data, dict) else "failed"
                if kind != "ok":
                    return user, 0, 0, 0, {}, kind, verdict.message
            members, _ = client.get_all_members()
            id_tpl, rep_tpl = default_identity_template(), default_report_template()
            results = (
                build_identity_results(members, id_tpl)
                + build_report_results(members, rep_tpl)
                + build_fee_results(members)
            )
            s = summarize_members(members, results)
            return user, s.total, s.uploaded, s.abnormal, dict(s.by_kind), "ok", ""
        except Exception as e:
            return user, 0, 0, 0, {}, "network", str(e)

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
                    self.account_done.emit("", 0, 0, 0, {}, "network", f"统计线程异常：{e}")
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

        lay = self.vBoxLayout

        # 仪表盘卡片：左圆环进度 + 右异常分类条形图
        card = CardWidget()
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(20, 16, 20, 16)
        card_lay.addWidget(StrongBodyLabel("组织状况总览"))
        card_lay.addWidget(note_label(
            "已上传依据「电子志愿书状态」字段；异常团员 = 信息错误 / 没有报道 / 欠缴团费任一项不通过，按人去重"))
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
        bars.addStretch(1)
        dash.addLayout(bars, 1)
        card_lay.addLayout(dash)

        row = QHBoxLayout()
        self.start_btn = PrimaryPushButton(FIF.SYNC, "开始统计")
        self.start_btn.clicked.connect(self._start)
        row.addWidget(self.start_btn)
        row.addStretch(1)
        card_lay.addLayout(row)
        lay.addWidget(card)

        # 明细表：全部只读，列宽按内容自适应，状态与错误信息完整展示
        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["账号", "在册", "已上传", "信息错误", "没有报道", "欠缴团费"])
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in range(6):
            header.setSectionResizeMode(col, header.ResizeMode.ResizeToContents)
        self.add_table(self.table, 1)

    def _set_ratio(self, total: int, uploaded: int):
        self.donut.set_value(uploaded, total)

    def _update_bars(self):
        for kind, bar in self.bars.items():
            bar.set_value(self._by_kind.get(kind, 0), self._members_max)

    def _start(self):
        accounts = self.window_ref.settings_page.accounts
        if not accounts:
            InfoBar.warning("未配置账号", "请先到「系统配置」添加团支部账号", duration=3000, parent=self)
            return
        self.start_btn.setEnabled(False)
        self._total = self._uploaded = 0
        self._by_kind = {"identity": 0, "report": 0, "fees": 0}
        self._members_max = 10
        self.table.setRowCount(0)
        self._set_ratio(0, 0)
        self._update_bars()
        self.worker = OrgWorker(accounts)
        self.worker.account_done.connect(self._on_account)
        self.worker.all_done.connect(lambda: self.start_btn.setEnabled(True))
        self.worker.start()

    def _on_account(self, user, total, uploaded, abnormal, by_kind, status, message):
        self._total += total
        self._uploaded += uploaded
        self._set_ratio(self._total, self._uploaded)
        if total:
            self._members_max = max(self._members_max, total)
        for k, v in (by_kind or {}).items():
            self._by_kind[k] = self._by_kind.get(k, 0) + v
        self._update_bars()

        r = self.table.rowCount()
        self.table.setRowCount(r + 1)
        vals = [
            user or "（空）",
            str(total) if total else "-",
            str(uploaded) if total else "-",
            str(by_kind.get("identity", "-")) if total else "-",
            str(by_kind.get("report", "-")) if total else "-",
            str(by_kind.get("fees", "-")) if total else "-",
        ]
        readonly = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if status != "ok":
            vals[0] = f"{user or '（空）'}　⚠ {STATUS_LABEL.get(status, status)}：{message}"
        for col, v in enumerate(vals):
            item = QTableWidgetItem(v)
            item.setFlags(readonly)
            self.table.setItem(r, col, item)
