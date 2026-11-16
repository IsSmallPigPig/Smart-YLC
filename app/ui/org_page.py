"""组织状况仪表盘：上传进度圆环 + 异常分类条形图 + 异常团员名单（可导出）。

异常分类：信息错误（报到身份）/ 没有报道（报到情况）/ 欠缴团费（团员缴费）。
名单只列错误级异常的团员，每行末尾是所属支部（账号备注，无备注回退账号名）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon as FIF,
    InfoBar,
    PrimaryPushButton,
    StrongBodyLabel,
    CaptionLabel,
)

from app.domain.check_rules import (
    build_fee_results,
    build_identity_results,
    build_report_results,
)
from app.domain.org_stats import KIND_LABEL, ErrorMemberRow, build_error_rows, summarize_members
from app.domain.account_health import excluded_summary, split_accounts
from app.domain.transfer import fetch_transfer_applies, is_graduated
from app.services import store
from app.services.api import SiteClient
from app.ui.charts import BarRowWidget, ProgressDonut
from app.ui import palette as P
from app.ui.widgets import PillLabel
from app.domain.check_templates import default_identity_template, default_report_template
from app.ui.page import (BusyOverlay, CallbackWorker, FluentPage, PageBanner,
                         PageHeader, note_label, paint_tinted_card, set_tip,
                         section_title, theme_font)

STATUS_LABEL = {
    "ok": "正常",
    "failed": "密码错误",
    "captcha": "需验证码",
    "passwordChange": "需改密",
    "needMemberName": "需姓名校验",
    "network": "网络错误",
    "empty": "未配置",
}

KIND_COLOR = P.KIND
TRANSFER_COLOR = P.TRANSFER


class OrgWorker(QThread):
    """并发逐账号：会话探测 → 失效才登录 → 拉取团员 → 三项检查 + 异常团员名单 + 转接统计。只读查询。

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


class TintedCard(CardWidget):
    """通用提示卡：整块 warning 黄底，账号级失败等没有跳转目标的错误用。"""

    def __init__(self, role: str = "warning", parent=None):
        super().__init__(parent)
        self.role = role

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_tinted_card(self, p, self.role, radius=8)
        p.end()


class MemberCard(CardWidget):
    """异常团员卡片：warning 黄底 + 点一下跳到组织管理里所在支部。"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_tinted_card(self, p, "warning", radius=8)
        p.end()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class OrgPage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__("page-org", parent)
        self.window_ref = window
        self.worker: OrgWorker | None = None
        self._progress = None      # 统计期间的右下角浮动进度
        self._progress_done = 0
        self._progress_total = 0
        self._stopped = False
        self._total = 0
        self._uploaded = 0
        self._by_kind = {"identity": 0, "report": 0, "archive": 0, "fees": 0}
        self._error_rows: list = []
        self._transfer_applied = 0
        self._snapshot = None  # 刷新前的展示快照（整轮失败时回滚）
        self._first_result = True
        self._ok_accounts = 0
        self._busy: BusyOverlay | None = None  # 手动统计期间盖住逐条增长的错误卡片
        QTimer.singleShot(0, self._restore_persisted)  # 重启后恢复上次的组织状况

        lay = self.vBoxLayout
        # 顶部留白让出头图位置（头图通栏贴顶、覆盖在内容之上），其余三边与内容对齐
        lay.setContentsMargins(32, PageBanner.HEIGHT + 16, 32, 24)

        # 头图通栏贴顶（overlay，不占布局），标题与操作控件叠在 banner 上
        self.header = PageBanner("组织状况", "团员信息核对与异常统计", self.scrollWidget)
        self.banner = self.header
        from app.services import store
        self.header.set_image_path(store.load().get("headerImage", ""))
        self._place_banner()

        self.start_btn = PrimaryPushButton(FIF.SYNC, "开始统计")
        self.start_btn.clicked.connect(self._start)
        # 统计每小时在后台跑一次；按钮只保留手动立即统计
        self.header.add_control(self.start_btn)
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(60 * 60 * 1000)
        self._auto_timer.timeout.connect(self._auto_start)
        self._auto_timer.start()

        card = CardWidget()
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(20, 16, 20, 16)

        dash = QHBoxLayout()
        self.donut = ProgressDonut()
        dash.addWidget(self.donut)
        dash.addSpacing(20)
        bars = QVBoxLayout()
        bars.setSpacing(4)
        self.bars: dict[str, BarRowWidget] = {}
        for kind in ("identity", "report", "archive", "fees"):
            bar = BarRowWidget(KIND_LABEL[kind], KIND_COLOR[kind])
            self.bars[kind] = bar
            bars.addWidget(bar)
        self.transfer_bar = BarRowWidget("转接申请", TRANSFER_COLOR)
        bars.addWidget(self.transfer_bar)
        bars.addStretch(1)
        dash.addLayout(bars, 1)
        card_lay.addLayout(dash)
        self.transfer_note = note_label("")
        card_lay.addWidget(self.transfer_note)

        lay.addWidget(card)

        # 异常团员卡片列表：每张卡片 = 姓名/支部 + 类别 + 问题说明；账号级失败单独成卡
        self.error_box = QWidget()
        self.error_lay = QVBoxLayout(self.error_box)
        self.error_lay.setContentsMargins(0, 0, 0, 0)
        self.error_lay.setSpacing(8)
        self.error_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.error_title = section_title("异常团员 0")
        self.add_table(self.error_box, 1)
        self.error_lay.addWidget(self.error_title)

    def _set_ratio(self, total: int, uploaded: int):
        self.donut.set_value(uploaded, total)

    def _update_bars(self):
        # 分母 = 累计获取到的团员总数（与圆环一致）；全部失败时 total=0，set_value 内部抬到 1
        for kind, bar in self.bars.items():
            bar.set_value(self._by_kind.get(kind, 0), self._total)
        self.transfer_bar.set_value(self._transfer_applied, self._total)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_banner()
        self._place_busy()

    def _place_banner(self):
        """头图几何：铺满页面宽度、贴顶，跟随窗口宽度变化。"""
        if getattr(self, "header", None) is not None:
            self.header.setGeometry(0, 0, self.scrollWidget.width(), PageBanner.HEIGHT)

    def _place_busy(self):
        if self._busy is None:
            return
        top = PageBanner.HEIGHT
        self._busy.setGeometry(0, top, self.viewport().width(),
                               max(0, self.viewport().height() - top))
        self._busy.raise_()

    def _show_busy(self, text: str = "正在统计组织状况…"):
        if self._busy is None:
            self._busy = BusyOverlay(self, text)
        self._busy.set_text(text)
        self._place_busy()
        self._busy.ring.start()
        self._busy.show()
        self._busy.raise_()

    def _hide_busy(self):
        if self._busy is None:
            return
        self._busy.ring.stop()
        self._busy.hide()

    def showEvent(self, e):
        super().showEvent(e)
        self._place_banner()
        self._place_busy()
        # 打开页面时图表做一次入场动画：条形与圆环从零长到当前值（与支部详情一致）
        max_n = max(self._total, 1)
        for kind, bar in self.bars.items():
            bar.set_value_animated(self._by_kind.get(kind, 0), max_n)
        self.transfer_bar.set_value_animated(self._transfer_applied, max_n)
        self.donut.set_value_animated(self._uploaded, self._total)

    def _auto_start(self):
        """每小时后台统计一次：不弹进度条，也不因账号为空打扰用户。"""
        if self.worker is not None and self.worker.isRunning():
            return
        self._start(silent=True)

    def _start(self, silent: bool = False):
        if self.worker is not None and self.worker.isRunning():
            return
        usable, excluded = split_accounts(self.window_ref.settings_page.accounts)
        accounts = usable
        if not accounts:
            if silent:
                return
            hint = f"{len(excluded)} 个账号均被标记为异常" if excluded else "先在系统配置添加团支部账号"
            InfoBar.warning("没有可统计的账号", hint, duration=6000, parent=self)
            return
        if excluded and not silent:
            InfoBar.warning("跳过异常账号",
                f"{excluded_summary(excluded)}，本轮不参与统计；测活恢复正常后自动纳入",
                duration=6000, parent=self)
        if not silent:
            self.start_btn.setEnabled(False)
        # 保留现有展示：归零累加器但不重绘，新账号结果陆续抵达时逐个替换；
        # 整轮一无所获（全失败/全跳过）时回滚快照，组织状况数据不丢
        self._snapshot = (
            self._total, self._uploaded, dict(self._by_kind),
            self._transfer_applied, self.transfer_note.text(), list(self._error_rows))
        self._total = self._uploaded = 0
        self._by_kind = {"identity": 0, "report": 0, "archive": 0, "fees": 0}
        self._transfer_applied = 0
        self._error_rows = []
        self._first_result = True
        self._ok_accounts = 0
        self.worker = OrgWorker(accounts)
        self.worker.account_done.connect(self._on_account)
        self.worker.all_done.connect(self._on_run_done)
        if not silent:
            self._show_progress(len(accounts))
            # 结果逐账号抵达，异常卡片一张张插进列表会把页面高度撑得不停变；
            # 盖住内容区，等整轮落地再一次性露出最终布局。
            self._show_busy()
        self.worker.start()

    def _show_progress(self, count: int):
        """统计期间在窗口右下角挂一条浮动进度：不挡页面，也不锁交互。

        点它的关闭按钮只是让 worker 停下；控件统一在 _on_run_done 里收掉。"""
        from app.ui.widgets import ProgressToast

        self._progress_done = 0
        self._progress_total = count
        self._progress = ProgressToast("正在统计组织状况", self.window(),
                                       on_close=self._request_stop)
        self._progress.set_progress(0, count)
        self._progress.show()

    def _request_stop(self):
        """点了浮条上的关闭：让 worker 停下，收尾仍走 _on_run_done。"""
        self._stopped = True
        if self.worker is not None:
            self.worker.stop()

    def _close_progress(self, finished: bool = False):
        toast, self._progress = self._progress, None
        if toast is None:
            return
        if finished:
            toast.finish()      # 补满 100% 后自行淡出，不在这里销毁
        else:
            toast.close()

    def _on_run_done(self):
        self.start_btn.setEnabled(True)
        self._close_progress(finished=not self._stopped)
        self._stopped = False
        if self._ok_accounts == 0:
            # 这一轮没有拿到任何账号数据：恢复刷新前的组织状况
            (self._total, self._uploaded, byk,
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
            self.error_title.setText(f"异常团员 {len(rows)}")
        # 卡片落地后下一帧再收遮罩：让布局先把最终高度算完，避免露出中间态
        QTimer.singleShot(0, self._hide_busy)

    def refresh_offline(self):
        """从成员缓存（memberCache）离线重算三项统计，无网络请求。

        成员同步拿到新数据后由 30 秒快刷调用；转接统计保留最近一次在线结果，
        现有展示直接被新数据整体替换（缓存为空则不动）。检查逐人跑，量大时
        放到后台线程，不卡界面动画。"""
        cache = store.load().get("memberCache") or {}
        if not cache:
            return
        from app.ui.page import CallbackWorker

        self._offline_token = getattr(self, "_offline_token", 0) + 1
        token = self._offline_token

        worker = CallbackWorker(self._offline_snapshot, self)
        worker.ok.connect(lambda data: token == self._offline_token
                          and self._apply_offline(data))
        self._offline_workers = getattr(self, "_offline_workers", [])
        self._offline_workers.append(worker)
        worker.start()

    def _offline_snapshot(self):
        """后台线程：本轮有缓存的支部重算一遍，再按整份 orgStatsCache 汇总。

        以前只汇总「本轮 memberCache 里的支部」，后台 30 秒同步一轮只拿到几个
        账号时，其余支部会从页面上消失——看着就是组织状况的数据丢了。"""
        from app.domain.org_manage import record_from_dict

        cfg = store.load()
        members_cache = cfg.get("memberCache") or {}
        # 先按整份缓存汇总（含本轮没同步到的支部），再逐个覆盖本轮重算出来的
        stats = {k: dict(v) for k, v in (cfg.get("orgStatsCache") or {}).items()}
        id_tpl, rep_tpl = default_identity_template(), default_report_template()
        for user, blob in members_cache.items():
            members = [record_from_dict(d) for d in blob.get("members", [])]
            branch = blob.get("branch") or user
            results = (
                build_identity_results(members, id_tpl)
                + build_report_results(members, rep_tpl)
                + build_fee_results(members)
            )
            s = summarize_members(members, results)
            rows = build_error_rows(results, branch)
            stats[user] = {
                "branch": branch,
                "total": s.total,
                "uploaded": s.uploaded,
                "by_kind": dict(s.by_kind),
                "transfer": (stats.get(user) or {}).get("transfer"),
                "error_rows": [vars(r) for r in rows],
                "updatedAt": datetime.now().isoformat(timespec="seconds"),
            }
            # 逐个支部照样落盘（transfer 传 None = 保留上次在线结果）
            self._persist_account(user, branch, s.total, s.uploaded,
                                  dict(s.by_kind), None, rows)

        agg = self._aggregate(stats)
        return agg["total"], agg["uploaded"], agg["by_kind"], agg["rows"]

    def _apply_offline(self, data):
        total, uploaded, by_kind, error_rows = data
        self._total, self._uploaded = total, uploaded
        self._by_kind = by_kind
        self._error_rows = error_rows
        self._set_ratio(total, uploaded)
        self._update_bars()
        self._clear_cards()
        for r in error_rows:
            self._append_card(r.name, r.mobile, [k for k in KIND_COLOR if KIND_LABEL[k] in r.kinds],
                              r.problems, r.branch, r.kinds)
        self.error_title.setText(f"异常团员 {len(error_rows)}")

    def _on_account(self, user, branch, total, uploaded, abnormal, by_kind, status, message, transfer, rows):
        self._progress_done += 1
        if self._progress is not None:
            self._progress.set_progress(self._progress_done, self._progress_total)
        if self._first_result:
            # 新数据开始抵达：旧卡片让位（本轮一无所获时 _on_run_done 会回滚）
            self._first_result = False
            self._clear_cards()
        self._ok_accounts += status == "ok"
        self._total += total
        self._uploaded += uploaded
        self._set_ratio(self._total, self._uploaded)
        for k, v in (by_kind or {}).items():
            self._by_kind[k] = self._by_kind.get(k, 0) + v
        if transfer:
            self._transfer_applied += transfer.applied
            self.transfer_note.setText(
                f"转接申请 {self._transfer_applied} 人，成功 {transfer.succeeded}，"
                f"审核中 {transfer.pending}，退回 {transfer.returned}")
        self._update_bars()

        if status != "ok":
            # 账号级失败：不参与名单，单独一张卡片说明，避免静默丢账号
            self._append_card("—", "—", [], f"账号 {user or '未标注'} 检查失败："
                              f"{STATUS_LABEL.get(status, status)}：{message}", branch, None)
        for r in rows:
            self._error_rows.append(r)
            self._append_card(r.name, r.mobile, [k for k in KIND_COLOR if KIND_LABEL[k] in r.kinds],
                              r.problems, r.branch, r.kinds)
        self.error_title.setText(f"异常团员 {len(self._error_rows)}")
        if status == "ok":
            self._persist_account(user, branch, total, uploaded, dict(by_kind or {}), transfer, rows)

    # ---------- 组织状况持久化（orgStatsCache） ----------

    GRAD_SUFFIX = "（毕业应转出）"

    @staticmethod
    def _transfer_dict(t) -> dict:
        return {"applied": t.applied, "total": t.total, "succeeded": t.succeeded,
                "pending": t.pending, "returned": t.returned, "names": list(t.names)}

    def _aggregate(self, cache: dict) -> dict:
        """把整份 orgStatsCache 汇总成页面要的数字与名单。

        「恢复上次结果」和「后台离线重算」共用这一份逻辑，避免两处口径不一致
        （重算只覆盖本次同步到的支部，其余支部的数字照样在总量里）。"""
        total = uploaded = 0
        by_kind = {"identity": 0, "report": 0, "archive": 0, "fees": 0}
        rows: list[ErrorMemberRow] = []
        applied = succeeded = pending = returned = 0
        names: list[str] = []
        updated = ""
        for entry in (cache or {}).values():
            total += int(entry.get("total") or 0)
            uploaded += int(entry.get("uploaded") or 0)
            for k, v in (entry.get("by_kind") or {}).items():
                if k in by_kind:
                    by_kind[k] += int(v or 0)
            rows.extend(ErrorMemberRow(**r) for r in entry.get("error_rows") or [])
            t = entry.get("transfer") or {}
            applied += int(t.get("applied") or 0)
            succeeded += int(t.get("succeeded") or 0)
            pending += int(t.get("pending") or 0)
            returned += int(t.get("returned") or 0)
            names.extend(t.get("names") or [])
            updated = max(updated, str(entry.get("updatedAt") or ""))
        return {"total": total, "uploaded": uploaded, "by_kind": by_kind, "rows": rows,
                "applied": applied, "succeeded": succeeded, "pending": pending,
                "returned": returned, "names": names, "updated": updated}

    def _persist_account(self, user, branch, total, uploaded, by_kind, transfer, rows):
        """本支部统计成功 → 写入配置 orgStatsCache；重启后从缓存恢复，不被清除。

        transfer 为 None（离线重算）时保留上次在线结果；已删除账号的旧条目
        随本次写盘一并清理，避免僵尸支部常驻。"""

        valid = {a.get("userName", "") for a in self.window_ref.settings_page.accounts}
        key = user.replace(self.GRAD_SUFFIX, "")

        def _write(cfg: dict):
            cache = cfg.setdefault("orgStatsCache", {})
            # valid 为空只可能是账号还没载入；这时清理会把整份统计抹掉
            if valid:
                for k in [k for k in cache if k not in valid]:
                    del cache[k]
            existing = cache.get(key) or {}
            cache[key] = {
                "branch": branch,
                "total": total,
                "uploaded": uploaded,
                "by_kind": {k: by_kind.get(k, 0) for k in ("identity", "report", "archive", "fees")},
                "transfer": self._transfer_dict(transfer) if transfer else existing.get("transfer"),
                "error_rows": [vars(r) for r in rows],
                "updatedAt": datetime.now().isoformat(timespec="seconds"),
            }

        store.update(_write)

    def _restore_persisted(self):
        """启动时从 orgStatsCache 恢复上次的组织状况（在线统计成功/离线重算
        都会更新缓存，重启不清除；正在统计时不覆盖）。"""
        if self.worker is not None and self.worker.isRunning():
            return

        cache = store.load().get("orgStatsCache") or {}
        if not cache:
            return
        agg = self._aggregate(cache)
        self._total, self._uploaded = agg["total"], agg["uploaded"]
        self._by_kind = agg["by_kind"]
        self._error_rows = agg["rows"]
        self._transfer_applied = agg["applied"]
        self._set_ratio(agg["total"], agg["uploaded"])
        self._update_bars()
        self._clear_cards()
        for r in agg["rows"]:
            self._append_card(r.name, r.mobile, [k for k in KIND_COLOR if KIND_LABEL[k] in r.kinds],
                              r.problems, r.branch, r.kinds)
        self.error_title.setText(f"异常团员 {len(agg['rows'])}")
        # 缓存里是 ISO 时间串，直接显示会带 T 和秒；截图/阅读都不好看
        updated = agg["updated"]
        stamp = f"，更新于 {updated.replace('T', ' ')[:16]}" if updated else ""
        self.transfer_note.setText(
            f"转接申请 {agg['applied']} 人，成功 {agg['succeeded']}，"
            f"审核中 {agg['pending']}，退回 {agg['returned']}{stamp}")

    def _clear_cards(self):
        while self.error_lay.count() > 1:  # 保留标题
            item = self.error_lay.takeAt(self.error_lay.count() - 1)
            if item.widget():
                item.widget().deleteLater()
        self.error_title.setText("异常团员 0")

    def _append_card(self, name, mobile, kinds: list, problem, branch, kinds_text):
        """一张异常团员卡片：左侧姓名+手机号，右侧支部；下方类别与问题。"""
        # kinds 为 None 是账号级失败，没有可跳转的支部，保持普通卡片
        if kinds is None or not branch:
            card = TintedCard("danger" if kinds is None else "warning")
        else:
            card = MemberCard()
            set_tip(card, f"点击查看「{branch}」的团员名单")
            card.clicked.connect(lambda b=branch: self._goto_branch(b))
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 9, 18, 9)
        v.setSpacing(5)

        head = QHBoxLayout()
        title = theme_font(StrongBodyLabel(name or ""), 15, int(QFont.Weight.DemiBold))
        head.addWidget(title)
        if mobile and mobile != "—":
            head.addSpacing(8)
            head.addWidget(CaptionLabel(mobile))
        head.addStretch(1)
        if kinds:
            for k in kinds:
                # 与上传 / 消息页同款标签：纯色底白字、4px 圆角，跨页一致
                chip = PillLabel(KIND_LABEL[k], k)
                head.addWidget(chip)
        head.addSpacing(12)
        branch_lbl = CaptionLabel(branch or "")
        branch_lbl.setTextColor(*P.text_colors("fg2"))
        head.addWidget(branch_lbl)
        v.addLayout(head)

        body = BodyLabel(problem or "")
        body.setWordWrap(True)
        if kinds is None:  # 账号级失败
            body.setTextColor(*P.text_colors("danger"))
        v.addWidget(body)

        self.error_lay.addWidget(card)

    def _goto_branch(self, branch: str):
        """跳到组织管理，并打开这个支部的详情。

        先切页（立刻出画面），再把「按支部名打开详情」推迟到下一轮事件循环——
        组织管理页首显会异步算分支树，这一步不阻塞点击，树算好后自动补开。
        找不到支部时由 reveal_branch 的 on_missing 兜底提示。"""
        from PySide6.QtCore import QTimer

        window = getattr(self, "window_ref", None)
        page = getattr(window, "org_manage_page", None)
        if page is None:
            return
        window.switchTo(page)
        QTimer.singleShot(0, lambda: page.reveal_branch(
            branch,
            on_missing=lambda: InfoBar.warning(
                "没有找到支部", f"组织管理里没有「{branch}」这个支部",
                duration=4000, parent=self)))
