"""组织管理页：信息流式支部卡片。

默认每张卡片 = 一个支部（名称 + 团支书 + 团员/异常/欠缴统计），点开卡片进入
支部详情：大标题支部名、小标题团支书、统计图表（异常分类条形图 + 欠缴团费），
下方是团员列表（系统成员可展开全部字段）。统计从成员缓存的系统记录用检查
规则离线计算，不发生网络请求。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon as FIF,
    InfoBar,
    PrimaryPushButton,
    SearchLineEdit,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    TransparentToolButton,
)

from app.domain.check_rules import (
    MemberRecord,
    build_fee_results,
    build_identity_results,
    build_report_results,
)
from app.domain.org_manage import (
    SOURCE_LABEL,
    SYSTEM_FIELDS,
    BranchNode,
    MemberView,
    build_branches,
    filter_members,
)
from app.services import login_gate, store
from app.services.api import SiteClient
from app.ui.charts import BarRowWidget
from app.ui.check_page import default_identity_template, default_report_template
from app.ui.page import FluentPage, note_label, section_title, theme_font

SOURCE_COLOR = {"local": "#6b7280", "system": "#1565c0", "both": "#2e7d32"}
KIND_COLOR = {"identity": "#ef6c00", "report": "#c62828", "fees": "#8e24aa"}
KIND_TEXT = {"identity": "信息错误", "report": "没有报道", "fees": "欠缴团费"}


def _rec_from_dict(d: dict) -> MemberRecord:
    fields = {f for f in MemberRecord.__dataclass_fields__}
    return MemberRecord(**{k: v for k, v in d.items() if k in fields})


class MemberSyncWorker(QThread):
    """登录各支部账号拉取成员，逐账号写入配置 memberCache。只读查询。

    concurrent=True（手动同步）时并发 ≤4 拉取；sequential=True（后台静默刷新）
    时逐账号串行、账号间留间隔，降低触发风控的概率。"""

    account_done = Signal(str, str, int, str, str)  # user, branch, count, status, message
    all_done = Signal()

    MAX_WORKERS = 4
    BG_GAP_SECONDS = 6  # 后台串行刷新的账号间隔

    def __init__(self, accounts: list[dict], sequential: bool = False):
        super().__init__()
        self.accounts = accounts
        self.sequential = sequential
        self._stop = False

    def stop(self):
        self._stop = True

    def _fetch_one(self, acc: dict):
        from datetime import datetime, timezone

        user, pwd = acc.get("userName", ""), acc.get("password", "")
        branch = acc.get("remark") or user
        if not user or not pwd:
            return user, branch, 0, "empty", "账号或密码为空"
        # 状态正常且 4 小时内刷新过 → 无需刷新（不发探测也不登录）
        skip = login_gate.fresh_skip(acc, "memberCache")
        if skip:
            return user, branch, 0, "fresh", skip
        try:
            client = SiteClient(user, pwd)
            kind, msg = login_gate.ensure_logged_in(client)
            if kind != "ok":
                return user, branch, 0, kind, msg
            members, _ = client.get_all_members()

            def _write_cache(cfg: dict, user=user, branch=branch, members=members):
                cache = cfg.get("memberCache", {})
                cache[user] = {
                    "branch": branch,
                    "fetchedAt": datetime.now(timezone.utc).isoformat(),
                    "members": [asdict(m) for m in members],
                }
                cfg["memberCache"] = cache

            # 逐账号落盘（store.update 持锁整段读写），中途停掉也不丢已拉取的数据
            store.update(_write_cache)
            return user, branch, len(members), "ok", ""
        except Exception as e:
            return user, branch, 0, "network", str(e)

    def run(self):
        import time

        if self.sequential:
            for i, acc in enumerate(self.accounts):
                if self._stop:
                    break
                acc = dict(acc)
                # 新鲜跳过的账号不发网络请求，直接返回、不吃账号间隔
                #（30 秒快刷全靠这批账号秒回，间隔只留在真正拉取的账号之间）
                if login_gate.fresh_skip(acc, "memberCache"):
                    self.account_done.emit(*self._fetch_one(acc))
                    continue
                if i:
                    time.sleep(self.BG_GAP_SECONDS)
                try:
                    self.account_done.emit(*self._fetch_one(acc))
                except Exception as e:
                    self.account_done.emit("", "", 0, "network", f"同步线程异常：{e}")
        else:
            workers = min(self.MAX_WORKERS, max(1, len(self.accounts)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(self._fetch_one, dict(a)) for a in self.accounts]
                for fut in as_completed(futures):
                    if self._stop:
                        break
                    try:
                        self.account_done.emit(*fut.result())
                    except Exception as e:
                        self.account_done.emit("", "", 0, "network", f"同步线程异常：{e}")
        self.all_done.emit()


class OrgManagePage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__("page-org-manage", parent)
        self.window_ref = window
        self.worker = None
        self._branches: list[BranchNode] = []
        self._stats = {"local": 0, "system": 0, "both": 0}

        lay = self.vBoxLayout

        # 顶部工具卡片
        self.bar = CardWidget()
        bar_lay = QVBoxLayout(self.bar)
        bar_lay.setContentsMargins(20, 16, 20, 16)
        row = QHBoxLayout()
        self.search_edit = SearchLineEdit()
        self.search_edit.setPlaceholderText("搜索团员姓名或手机号")
        self.search_edit.setFixedWidth(260)
        self.search_edit.textChanged.connect(self._rebuild)
        self.sys_only = SwitchButton("仅显示系统名单")
        self.sys_only.setOffText("全部")
        self.sys_only.setChecked(False)
        self.sys_only.checkedChanged.connect(lambda *_: self._rebuild())
        self.sync_btn = PrimaryPushButton(FIF.SYNC, "同步系统数据")
        self.sync_btn.clicked.connect(self._sync)
        row.addWidget(self.search_edit, 0)
        row.addSpacing(16)
        row.addWidget(self.sys_only, 0)
        row.addSpacing(16)
        row.addWidget(self.sync_btn, 0)
        row.addStretch(1)
        bar_lay.addLayout(row)
        self.stat_label = note_label("本地 — 人 · 系统 — 人 · 双方 — 人（默认显示本地名单，同步后可交叉查看）")
        bar_lay.addWidget(self.stat_label)
        lay.addWidget(self.bar)

        # 信息流容器（支部卡片列表）与支部详情视图，二选一显示
        self.feed_box = QWidget()
        self.feed_lay = QVBoxLayout(self.feed_box)
        self.feed_lay.setContentsMargins(0, 0, 0, 0)
        self.feed_lay.setSpacing(12)
        self.feed_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.vBoxLayout.addWidget(self.feed_box, 1)

        self.detail_box = QWidget()
        self.detail_lay = QVBoxLayout(self.detail_box)
        self.detail_lay.setContentsMargins(0, 0, 0, 0)
        self.detail_lay.setSpacing(12)
        self.detail_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.detail_box.setVisible(False)
        self.vBoxLayout.addWidget(self.detail_box, 1)
        self.vBoxLayout.addStretch(1)
        self._detail_code = ""  # 当前打开的支部编号（同步完成后刷新详情用）

    def showEvent(self, e):  # 每次进入页面重新读名单/缓存
        super().showEvent(e)
        self.refresh()

    # ---------- 数据组装 ----------

    def _load_local_rows(self) -> list[MemberView]:
        """本地名单直接复用「系统配置」已解析的各学期名单（不在 GUI 线程重复读 xlsx）。"""
        rows: list[MemberView] = []
        seen: set[str] = set()
        for roster in self.window_ref.settings_page.rosters.values():
            for r in roster.rows:
                k = f"{r.name}::{r.mobile}"
                if k in seen:
                    continue
                seen.add(k)
                rows.append(MemberView(r.name, r.mobile, cls=(r.extra or {}).get("class", "")))
        return rows

    def _load_system_groups(self) -> list[tuple[str, list[MemberRecord]]]:
        cfg = store.load()
        accounts = {a.get("userName", ""): a for a in self.window_ref.settings_page.accounts}
        groups: list[tuple[str, list[MemberRecord]]] = []
        for user, blob in (cfg.get("memberCache") or {}).items():
            branch = (accounts.get(user) or {}).get("remark") or user or blob.get("branch", "")
            groups.append((branch, [_rec_from_dict(d) for d in blob.get("members", [])]))
        return groups

    def refresh(self):
        local = self._load_local_rows()
        sys_groups = self._load_system_groups()
        self._branches = build_branches(local, sys_groups, store.load().get("secretaries", []))
        self._stats = {
            "local": sum(1 for b in self._branches for m in b.members if m.source in ("local", "both")),
            "system": sum(1 for b in self._branches for m in b.members if m.source in ("system", "both")),
            "both": sum(1 for b in self._branches for m in b.members if m.source == "both"),
        }
        self._rebuild()

    def _branch_stats(self, b: BranchNode) -> dict:
        """支部统计：从成员缓存的系统记录离线计算，无网络请求。

        异常只计错误级（职业未填等警告不算）；欠缴 = 未交总额>0 或未交月份>0。"""
        recs = [m.system for m in b.members if m.system is not None]
        by_kind = {"identity": 0, "report": 0, "fees": 0}
        unpaid_amount = 0.0
        abnormal_ids: set[str] = set()
        if recs:
            results = (
                build_identity_results(recs, default_identity_template())
                + build_report_results(recs, default_report_template())
                + build_fee_results(recs)
            )
            for r in results:
                if r.kind == "fees":
                    by_kind["fees"] += 1
                    unpaid_amount += r.amount
                    abnormal_ids.add(r.member.member_id)
                elif r.is_error:
                    by_kind[r.kind] += 1
                    abnormal_ids.add(r.member.member_id)
        return {
            "total": len(b.members),
            "system": len(recs),
            "abnormal": len(abnormal_ids),
            "by_kind": by_kind,
            "unpaid_amount": unpaid_amount,
        }

    def _rebuild(self):
        while self.feed_lay.count():
            item = self.feed_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        shown = filter_members(self._branches, self.search_edit.text(), self.sys_only.isChecked())
        shown_count = sum(len(b.members) for b in shown)
        self.stat_label.setText(
            f"本地 {self._stats['local']} 人 · 系统 {self._stats['system']} 人 · 双方 {self._stats['both']} 人"
            f" · 当前展示 {shown_count} 人")
        if not shown:
            self.feed_lay.addWidget(note_label(
                "没有匹配的支部。先在「系统配置」导入团员名单；要看系统数据请点「同步系统数据」。"))
            return
        for b in shown:
            self.feed_lay.addWidget(self._feed_card(b))
        # 详情开着时同步完成 → 原地刷新当前支部详情
        if self.detail_box.isVisible() and self._detail_code:
            self._open_branch(self._detail_code)

    # ---------- 信息流支部卡片 ----------

    def _feed_card(self, b: BranchNode) -> QWidget:
        st = self._branch_stats(b)
        card = CardWidget()
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(6)

        head = QHBoxLayout()
        title = theme_font(StrongBodyLabel(b.label), 16, int(QFont.Weight.DemiBold))
        head.addWidget(title)
        head.addStretch(1)
        # 搜索过滤时在支部名旁显示匹配到的团员名，一眼看出是谁触发了这张卡片
        if self.search_edit.text().strip():
            names = [m.name for m in b.members if m.name]
            if names:
                text = "、".join(names[:3]) + (f" 等 {len(names)} 人" if len(names) > 3 else "")
                head.addWidget(self._chip(text, "#6b7280"))
        total = StrongBodyLabel(f"{st['total']} 人")
        head.addWidget(total)
        v.addLayout(head)

        meta = b.secretary_text + (f"　·　{b.grade}" if b.grade else "")
        if st["system"] < st["total"]:
            meta += f"　·　系统在册 {st['system']}"
        meta_lbl = note_label(meta)
        v.addWidget(meta_lbl)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        if st["abnormal"]:
            chips.addWidget(self._chip(f"异常 {st['abnormal']}", "#c62828"))
        if st["by_kind"]["fees"]:
            chips.addWidget(self._chip(f"欠缴 {st['by_kind']['fees']}", "#8e24aa"))
        if not st["abnormal"] and st["system"]:
            chips.addWidget(self._chip("无异常", "#2e7d32"))
        if st["system"] == 0:
            chips.addWidget(self._chip("未同步系统数据", "#6b7280"))
        chips.addStretch(1)
        chevron = BodyLabel("›")
        theme_font(chevron, 20)
        chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        chips.addWidget(chevron)
        v.addLayout(chips)

        # 点击卡片任意位置进入详情
        for w in card.findChildren(QWidget):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        card.clicked.connect(lambda *_: self._open_branch(b.code))
        return card

    @staticmethod
    def _chip(text: str, color: str) -> BodyLabel:
        chip = BodyLabel(text)
        chip.setStyleSheet(
            f"background-color: {color}; color: white; border-radius: 4px; padding: 1px 10px;")
        return chip

    # ---------- 支部详情 ----------

    def _open_branch(self, code: str):
        b = next((n for n in self._branches if n.code == code), None)
        if b is None:
            return
        self._detail_code = code
        while self.detail_lay.count():
            item = self.detail_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        st = self._branch_stats(b)

        back_row = QHBoxLayout()
        back = TransparentToolButton(FIF.LEFT_ARROW)
        back.setToolTip("返回支部列表")
        back.clicked.connect(self._back_to_feed)
        back_row.addWidget(back)
        back_row.addStretch(1)
        self.detail_lay.addLayout(back_row)

        self.detail_lay.addWidget(SubtitleLabel(b.label))
        self.detail_lay.addWidget(note_label(
            b.secretary_text + (f"　·　{b.grade}" if b.grade else "") + f"　·　{b.code}"))

        # 统计卡：三个大数字 + 异常分类条形图
        self.detail_lay.addWidget(section_title("支部概览"))
        stat_card = CardWidget()
        sv = QVBoxLayout(stat_card)
        sv.setContentsMargins(20, 16, 20, 16)
        sv.setSpacing(8)
        nums = QHBoxLayout()
        nums.setSpacing(12)
        for num, label, color in (
            (st["total"], "团员总数", "#0078d4"),
            (st["abnormal"], "异常团员", "#c62828"),
            (st["by_kind"]["fees"], "欠缴团费（人）", "#8e24aa"),
        ):
            block = QVBoxLayout()
            block.setSpacing(2)
            n_lbl = theme_font(BodyLabel(str(num)), 26, int(QFont.Weight.DemiBold))
            n_lbl.setTextColor(QColor(color), QColor(color))
            block.addWidget(n_lbl)
            block.addWidget(note_label(label))
            nums.addLayout(block, 1)
        sv.addLayout(nums)
        sv.addSpacing(6)
        max_n = max(st["total"], 1)
        for kind in ("identity", "report", "fees"):
            bar = BarRowWidget(KIND_TEXT[kind], KIND_COLOR[kind])
            bar.set_value(st["by_kind"][kind], max_n)
            sv.addWidget(bar)
        if st["unpaid_amount"]:
            sv.addWidget(note_label(f"未交团费总额 {st['unpaid_amount']:g} 元"))
        self.detail_lay.addWidget(stat_card)

        members = section_title(f"团员（{st['total']} 人）")
        self.detail_lay.addWidget(members)
        if not b.members:
            self.detail_lay.addWidget(note_label("这个支部暂时没有团员。"))
        for m in b.members:
            self.detail_lay.addWidget(self._member_row(m))

        self.feed_box.setVisible(False)
        self.bar.setVisible(False)
        self.detail_box.setVisible(True)
        self._scroll_top()

    def _back_to_feed(self, *_):
        if not self.detail_box.isVisible():
            return
        self._detail_code = ""
        self.detail_box.setVisible(False)
        self.feed_box.setVisible(True)
        self.bar.setVisible(True)
        self._scroll_top()

    def _scroll_top(self):
        self.verticalScrollBar().setValue(0)

    def _member_row(self, m: MemberView) -> QWidget:
        """成员行：姓名 + 手机号 + 来源；点击行内任意位置展开/收起全部字段。"""
        card = CardWidget()
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(10)

        name = theme_font(BodyLabel(m.name), 13, int(QFont.Weight.DemiBold))
        h.addWidget(name)
        mob = BodyLabel(m.mobile)
        h.addWidget(mob)
        cls_lbl = None
        if m.cls:
            cls_lbl = note_label(m.cls)
            h.addWidget(cls_lbl)
        h.addStretch(1)
        chip = BodyLabel(SOURCE_LABEL[m.source])
        chip.setTextColor(SOURCE_COLOR[m.source], SOURCE_COLOR[m.source])
        h.addWidget(chip)
        detail = None
        toggle = None
        if m.system is not None:
            toggle = TransparentToolButton(FIF.DOWN)
            toggle.setToolTip("展开/收起系统信息")
            h.addWidget(toggle)

        # 子控件默认会吃掉鼠标事件，导致点姓名/手机号不触发展开 → 全部设为鼠标穿透
        for w in (name, mob, chip, cls_lbl):
            if w is not None:
                w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(card)
        if m.system is not None:
            detail = self._detail_grid(m.system)
            detail.setVisible(False)
            v.addWidget(detail)

            def _toggle(checked=False, d=detail, t=toggle):
                d.setVisible(not d.isVisible())
                t.setIcon(FIF.UP if d.isVisible() else FIF.DOWN)

            toggle.clicked.connect(_toggle)
            card.clicked.connect(_toggle)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
        return wrap

    def _detail_grid(self, rec: MemberRecord) -> QWidget:
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(24, 4, 14, 10)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(2)
        for i, (label, key) in enumerate(SYSTEM_FIELDS):
            r, c = divmod(i, 3)
            val = rec.value_of(key)
            if key in ("unpaid_total", "unpaid_months") and not val:
                val = "0"
            lbl = note_label(f"{label}　{val or '—'}")
            lbl.setWordWrap(False)
            grid.addWidget(lbl, r, c)
        return grid_w

    # ---------- 同步 ----------

    def _sync(self):
        accounts = self.window_ref.settings_page.accounts
        if not accounts:
            InfoBar.warning("未配置账号", "请先到「系统配置」添加团支部账号", duration=3000, parent=self)
            return
        self.sync_btn.setEnabled(False)
        self.worker = MemberSyncWorker(accounts)
        self.worker.account_done.connect(self._on_sync_one)
        self.worker.all_done.connect(self._on_sync_done)
        self.worker.start()

    def _on_sync_one(self, user, branch, count, status, message):
        if status in login_gate.SKIP_KINDS:
            InfoBar.info(f"账号 {user} 跳过刷新", message, duration=4000, parent=self)
        elif status != "ok":
            InfoBar.warning(
                f"账号 {user or '（空）'} 同步失败",
                f"{message}（其他账号不受影响）", duration=4000, parent=self)

    def _on_sync_done(self):
        self.sync_btn.setEnabled(True)
        mark_synced()
        self.refresh()
        InfoBar.success("同步完成", "系统成员缓存已更新，组织树已刷新", duration=3000, parent=self)


def mark_synced():
    """记录最后一次刷新时间（手动/后台都算），后台定时刷新据此判断是否到期。"""
    from datetime import datetime, timezone

    store.update(
        lambda cfg: cfg.__setitem__("lastSyncAt", datetime.now(timezone.utc).isoformat()))
