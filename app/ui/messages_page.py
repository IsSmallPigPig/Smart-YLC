"""系统消息页：拉取全部账号的站内信，自动分类（报到申请 / 转接申请 / 其他），
可按类型筛选；点击消息按团员所在支部登录对应账号的网页端。

消息缓存持久化到配置 messagesCache（只写不删）；点卡片用团员手机号定位支部账号，
定位不到时提示原因。拉取走并发线程（≤4），失败账号弹 InfoBar 不影响其他账号。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    PrimaryPushButton,
    StrongBodyLabel,
)

from app.domain.messages import (
    KIND_COLOR,
    KIND_LABEL,
    MessageView,
    build_member_index,
    fetch_all_messages,
    locate_account,
)
from app.services import store
from app.services.api import SiteClient
from app.ui.page import FluentPage, note_label

FILTER_LABELS = ["全部", *KIND_LABEL.values()]  # 全部 / 报到申请 / 转接申请 / 其他消息


class MessageFetchWorker(QThread):
    """并发拉取各账号站内信，逐账号落盘 messagesCache。只读查询。"""

    account_done = Signal(str, int, str, str)  # user, count, status, message
    all_done = Signal()

    MAX_WORKERS = 4

    def __init__(self, accounts: list[dict]):
        super().__init__()
        self.accounts = accounts
        self._stop = False

    def stop(self):
        self._stop = True

    def _fetch_one(self, acc: dict):
        from datetime import datetime, timezone

        user, pwd = acc.get("userName", ""), acc.get("password", "")
        if not user or not pwd:
            return user, 0, "empty", "账号或密码为空"
        try:
            client = SiteClient(user, pwd)
            probe = client.session_account()
            if not probe.ok:
                verdict = client.login()
                kind = str((verdict.data or {}).get("kind", "failed")) if isinstance(verdict.data, dict) else "failed"
                if kind != "ok":
                    return user, 0, kind, verdict.message
            views = fetch_all_messages(client, user)
            # 逐账号落盘，中途停掉不丢已拉取的消息
            cfg = store.load()
            cache = cfg.get("messagesCache", {})
            cache[user] = {
                "fetchedAt": datetime.now(timezone.utc).isoformat(),
                "messages": [v.__dict__ | {"kind": v.kind} for v in views],
            }
            cfg["messagesCache"] = cache
            store.save(cfg)
            return user, len(views), "ok", ""
        except Exception as e:
            return user, 0, "network", str(e)

    def run(self):
        workers = min(self.MAX_WORKERS, max(1, len(self.accounts)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._fetch_one, dict(a)) for a in self.accounts]
            for fut in as_completed(futures):
                if self._stop:
                    break
                try:
                    self.account_done.emit(*fut.result())
                except Exception as e:
                    self.account_done.emit("", 0, "network", f"拉取线程异常：{e}")
        self.all_done.emit()


class MessageOpenWorker(QThread):
    """点击消息：定位支部账号 → 登录 → 携带 cookie 打开网页端。"""

    done = Signal(bool, str)

    def __init__(self, user: str, pwd: str, parent=None):
        super().__init__(parent)
        self.user, self.pwd = user, pwd

    def run(self):
        from app.services.webopen import open_logged_in

        try:
            client = SiteClient(self.user, self.pwd)
            ok, msg = open_logged_in(client)
            self.done.emit(ok, f"{self.user}：{msg}")
        except Exception as e:
            self.done.emit(False, f"{self.user}：{e}")


class MessagesPage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__("page-messages", parent)
        self.window_ref = window
        self.worker: MessageFetchWorker | None = None
        self.open_worker: MessageOpenWorker | None = None
        self._views: list[MessageView] = []
        self._member_index: dict[str, tuple[str, str]] = {}

        lay = self.vBoxLayout

        bar = CardWidget()
        bar_lay = QVBoxLayout(bar)
        bar_lay.setContentsMargins(20, 16, 20, 16)
        row = QHBoxLayout()
        self.filter_combo = ComboBox()
        for label in FILTER_LABELS:
            self.filter_combo.addItem(label)
        self.filter_combo.setCurrentIndex(0)
        self.filter_combo.currentIndexChanged.connect(lambda *_: self._rebuild())
        self.fetch_btn = PrimaryPushButton(FIF.SYNC, "拉取站内信")
        self.fetch_btn.clicked.connect(self._fetch)
        row.addWidget(self.filter_combo, 0)
        row.addSpacing(16)
        row.addWidget(self.fetch_btn, 0)
        row.addStretch(1)
        bar_lay.addLayout(row)
        self.stat_label = note_label("共 — 条 · 报到 — · 转接 — · 其他 —（点击消息卡按团员所在支部打开对应网页端）")
        bar_lay.addWidget(self.stat_label)
        lay.addWidget(bar)

        # 消息卡片列表
        self.list_box = QWidget()
        self.list_lay = QVBoxLayout(self.list_box)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(8)
        self.list_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.add_table(self.list_box, 1)

        self._load_cache()

    def showEvent(self, e):
        super().showEvent(e)
        self._load_cache()

    # ---------- 数据 ----------

    def _load_cache(self):
        """从缓存组装视图（拉取后也会刷新）。"""
        cfg = store.load()
        cache = cfg.get("messagesCache", {})
        views: list[MessageView] = []
        for user, blob in cache.items():
            for d in blob.get("messages", []):
                d = dict(d)
                d.setdefault("account", user)
                # 兼容缓存键与 API 原始键两种写法
                d.setdefault("receive_id", d.get("receiveId", 0))
                d.setdefault("status_str", d.get("statusStr", ""))
                d.setdefault("time_str", d.get("sendTime", d.get("time_str", "")))
                d.setdefault("mobile", d.get("mobile", "") or "")
                views.append(MessageView(**{
                    k: d[k] for k in MessageView.__dataclass_fields__ if k in d
                }))
        views.sort(key=lambda v: v.time_str, reverse=True)
        self._views = views
        self._member_index = build_member_index(
            cfg.get("accounts", []), cfg.get("memberCache", {}),
            self._local_rows(cfg))
        self._rebuild()

    def _local_rows(self, cfg) -> list:
        import os

        from app.services.excel import import_roster

        rows = []
        for path in cfg.get("rosters", {}):
            if not os.path.isfile(path):
                continue
            try:
                rows.extend(import_roster(path, os.path.basename(path)).rows)
            except ValueError:
                continue
        return rows

    def _rebuild(self):
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        kind = list(KIND_LABEL)[self.filter_combo.currentIndex() - 1] if self.filter_combo.currentIndex() else ""
        shown = [v for v in self._views if not kind or v.kind == kind]
        counts = {k: sum(1 for v in self._views if v.kind == k) for k in KIND_LABEL}
        self.stat_label.setText(
            f"共 {len(self._views)} 条 · 报到 {counts['report']} · 转接 {counts['transfer']} · 其他 {counts['other']}"
            + (f" · 当前显示 {len(shown)} 条" if kind else ""))
        if not shown:
            self.list_lay.addWidget(note_label(
                "没有消息。点「拉取站内信」从所有账号获取；拉取结果会保留在本地。"))
            return
        for v in shown:
            self.list_lay.addWidget(self._message_card(v))

    # ---------- UI ----------

    def _message_card(self, v: MessageView) -> QWidget:
        card = CardWidget()
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        v_out = QVBoxLayout(card)
        v_out.setContentsMargins(18, 12, 18, 12)
        v_out.setSpacing(6)

        head = QHBoxLayout()
        chip = BodyLabel(v.kind_label)
        chip.setTextColor(QColor(KIND_COLOR[v.kind]), QColor(KIND_COLOR[v.kind]))
        head.addWidget(chip)
        head.addSpacing(4)
        if v.status == 0:
            unread = BodyLabel("未读")
            unread.setTextColor(QColor("#c62828"), QColor("#ef5350"))
            head.addWidget(unread)
        head.addStretch(1)
        head.addWidget(CaptionLabel(v.time_str))
        head.addSpacing(12)
        head.addWidget(CaptionLabel(v.account))
        v_out.addLayout(head)

        title = StrongBodyLabel(v.title or "（无标题）")
        title.setStyleSheet("font-size: 15px;")
        v_out.addWidget(title)

        body = note_label(v.content or "—")
        body.setWordWrap(True)
        v_out.addWidget(body)
        card.clicked.connect(lambda *_: self._open_message(v))
        return card

    # ---------- 动作 ----------

    def _fetch(self):
        accounts = self.window_ref.settings_page.accounts
        if not accounts:
            InfoBar.warning("未配置账号", "请先到「系统配置」添加团支部账号", duration=3000, parent=self)
            return
        self.fetch_btn.setEnabled(False)
        self.worker = MessageFetchWorker(accounts)
        self.worker.account_done.connect(self._on_fetch_one)
        self.worker.all_done.connect(self._on_fetch_done)
        self.worker.start()

    def _on_fetch_one(self, user, count, status, message):
        if status != "ok":
            InfoBar.warning(
                f"账号 {user or '（空）'} 拉取失败",
                f"{message}（其他账号不受影响）", duration=4000, parent=self)

    def _on_fetch_done(self):
        self.fetch_btn.setEnabled(True)
        self._load_cache()
        InfoBar.success("拉取完成", "站内信已更新并保留在本地", duration=3000, parent=self)

    def _open_message(self, v: MessageView):
        if self.open_worker is not None and self.open_worker.isRunning():
            return
        accounts = self.window_ref.settings_page.accounts
        user = locate_account(v, accounts, self._member_index)
        if not user:
            InfoBar.warning(
                "无法定位支部账号",
                f"消息里没有可识别的团员（手机号 {v.mobile or '无'}），"
                "或该团员不在任何已配置支部的名单里。请先「同步系统数据」或导入团员名单。",
                duration=6000, parent=self)
            return
        acc = next((a for a in accounts if a.get("userName") == user), None)
        if not acc or not acc.get("password"):
            InfoBar.warning("账号不可用", f"团员所在支部账号 {user} 缺少密码", duration=4000, parent=self)
            return
        self.open_worker = MessageOpenWorker(user, acc["password"], self)
        self.open_worker.done.connect(self._on_opened)
        self.open_worker.start()

    def _on_opened(self, ok: bool, msg: str):
        if ok:
            InfoBar.success("打开网页", msg, duration=5000, parent=self)
        else:
            InfoBar.warning("打开网页", msg, duration=8000, parent=self)
