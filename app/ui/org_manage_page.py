"""组织管理页：年级-支部-团员 树状卡片（非表格）。

默认显示本地名单（系统配置导入的团员名单 xlsx）；「仅显示系统名单」开关过滤出
智慧团建上已有的团员；「同步系统数据」登录各支部账号拉取成员并缓存到配置。
支部卡片展示团支书（系统配置上传的团支书名单）；系统成员展开展示全部信息。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon as FIF,
    InfoBar,
    PrimaryPushButton,
    SearchLineEdit,
    StrongBodyLabel,
    SwitchButton,
    TransparentToolButton,
)

from app.domain.check_rules import MemberRecord
from app.domain.org_manage import (
    SOURCE_LABEL,
    SYSTEM_FIELDS,
    BranchNode,
    MemberView,
    build_branches,
    filter_members,
)
from app.services import store
from app.services.excel import import_roster
from app.services.api import SiteClient
from app.ui.page import FluentPage, note_label

SOURCE_COLOR = {"local": "#6b7280", "system": "#1565c0", "both": "#2e7d32"}


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
        try:
            client = SiteClient(user, pwd)
            probe = client.session_account()
            if not probe.ok:
                verdict = client.login()
                kind = str((verdict.data or {}).get("kind", "failed")) if isinstance(verdict.data, dict) else "failed"
                if kind != "ok":
                    return user, branch, 0, kind, verdict.message
            members, _ = client.get_all_members()
            # 逐账号落盘，中途停掉也不丢已拉取的数据
            cfg = store.load()
            cache = cfg.get("memberCache", {})
            cache[user] = {
                "branch": branch,
                "fetchedAt": datetime.now(timezone.utc).isoformat(),
                "members": [asdict(m) for m in members],
            }
            cfg["memberCache"] = cache
            store.save(cfg)
            return user, branch, len(members), "ok", ""
        except Exception as e:
            return user, branch, 0, "network", str(e)

    def run(self):
        import time

        if self.sequential:
            for i, acc in enumerate(self.accounts):
                if self._stop:
                    break
                if i:
                    time.sleep(self.BG_GAP_SECONDS)
                try:
                    self.account_done.emit(*self._fetch_one(dict(acc)))
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
        bar = CardWidget()
        bar_lay = QVBoxLayout(bar)
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
        lay.addWidget(bar)

        # 树状容器
        self.tree_box = QWidget()
        self.tree_lay = QVBoxLayout(self.tree_box)
        self.tree_lay.setContentsMargins(0, 0, 0, 0)
        self.tree_lay.setSpacing(10)
        self.tree_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.vBoxLayout.addWidget(self.tree_box, 1)
        self.vBoxLayout.addStretch(1)

    def showEvent(self, e):  # 每次进入页面重新读名单/缓存
        super().showEvent(e)
        self.refresh()

    # ---------- 数据组装 ----------

    def _load_local_rows(self) -> list[MemberView]:
        cfg = store.load()
        rows: list[MemberView] = []
        seen: set[str] = set()
        for path in cfg.get("rosters", {}):
            if not os.path.isfile(path):
                continue
            try:
                roster = import_roster(path, os.path.basename(path))
            except ValueError:
                continue  # 名单文件本身的问题在系统配置导入时报过，这里不重复弹
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

    def _rebuild(self):
        while self.tree_lay.count():
            item = self.tree_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        shown = filter_members(self._branches, self.search_edit.text(), self.sys_only.isChecked())
        shown_count = sum(len(b.members) for b in shown)
        self.stat_label.setText(
            f"本地 {self._stats['local']} 人 · 系统 {self._stats['system']} 人 · 双方 {self._stats['both']} 人"
            f" · 当前展示 {shown_count} 人")
        if not shown:
            empty = note_label("没有匹配的团员。先在「系统配置」导入团员名单；要看系统数据请点「同步系统数据」。")
            self.tree_lay.addWidget(empty)
            return
        # 年级分组展示
        grades: dict[str, list[BranchNode]] = {}
        for b in shown:
            key = f"{b.dept}·{b.grade}" if b.grade else b.dept
            grades.setdefault(key, []).append(b)
        for grade_key, branch_list in grades.items():
            self.tree_lay.addWidget(self._grade_section(grade_key, branch_list))

    def _grade_section(self, grade_key: str, branch_list: list[BranchNode]) -> QWidget:
        total = sum(len(b.members) for b in branch_list)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(8)
        head = StrongBodyLabel(f"{grade_key}　{len(branch_list)} 个支部 · {total} 名团员")
        head.setStyleSheet("font-size: 16px; font-weight: 600;")
        v.addWidget(head)
        for b in branch_list:
            v.addWidget(self._branch_card(b))
        return w

    def _branch_card(self, b: BranchNode) -> QWidget:
        card = CardWidget()
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(8)

        head = QHBoxLayout()
        title = StrongBodyLabel(b.label)
        title.setStyleSheet("font-size: 15px;")
        head.addWidget(title)
        if b.code != b.label:
            head.addWidget(BodyLabel(f"（{b.code}）"))
        head.addStretch(1)
        head.addWidget(BodyLabel(f"{len(b.members)} 人"))
        v.addLayout(head)

        sec = note_label(b.secretary_text + ("　·　" + b.grade if b.grade else ""))
        v.addWidget(sec)

        for m in b.members:
            v.addWidget(self._member_row(m))
        return card

    def _member_row(self, m: MemberView) -> QWidget:
        """成员行：姓名 + 手机号 + 来源；系统成员可展开查看全部字段。"""
        card = CardWidget()
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(10)

        name = BodyLabel(m.name)
        name.setStyleSheet("font-weight: 600;")
        h.addWidget(name)
        h.addWidget(BodyLabel(m.mobile))
        if m.cls:
            h.addWidget(note_label(m.cls))
        h.addStretch(1)
        chip = BodyLabel(SOURCE_LABEL[m.source])
        chip.setTextColor(SOURCE_COLOR[m.source], SOURCE_COLOR[m.source])
        h.addWidget(chip)
        if m.system is not None:
            toggle = TransparentToolButton(FIF.CHEVRON_DOWN_MED)
            toggle.setToolTip("展开系统信息")
            h.addWidget(toggle)

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
                t.setIcon(FIF.CHEVRON_UP_MED if d.isVisible() else FIF.CHEVRON_DOWN_MED)

            toggle.clicked.connect(_toggle)
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
        if status != "ok":
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

    cfg = store.load()
    cfg["lastSyncAt"] = datetime.now(timezone.utc).isoformat()
    store.save(cfg)
