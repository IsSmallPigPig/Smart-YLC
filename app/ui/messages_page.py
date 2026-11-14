"""系统消息页：自动拉取全部账号的站内信，按 报到申请 / 转接申请 / 其他消息 分组展示。

默认展示未读，可切「已读 / 全部」；已读消息整卡变暗（不改字体颜色）；点开一条
即在本地标记已读（持久化到 messagesRead），支持一键全部标为已读。
分组大标题；卡片主标题是团员姓名（手机号在系统名单/本地名单里能查到时），
原站内信标题（去掉【系统通知】中括号）作为彩色底白字小标签放在大标题右侧；
未读消息另加「未读」标签（卡片不加底色）。

消息缓存持久化到配置 messagesCache（只写不删）；拉取走并发线程（≤4），
失败账号弹 InfoBar 不影响其他账号；启动时缓存为空才补拉一次，之后由主窗口
30 秒定时链同步系统数据。
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    isDarkTheme,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)

from app.domain.messages import (
    KIND_LABEL,
    MessageView,
    build_member_name_index,
    clean_title,
    fetch_all_messages,
)
from app.domain.account_health import excluded_note, split_accounts
from app.services import login_gate, store
from app.services.api import SiteClient
from app.ui.page import FluentPage, note_label, section_title, theme_font

FILTER_LABELS = ["全部", *KIND_LABEL.values()]  # 全部 / 报到申请 / 转接申请 / 其他消息
SECTION_PAGE = 60  # 每个分组一次渲染的卡片数，其余走「显示更多」


class MessageFetchWorker(QThread):
    """同步系统数据：并发拉取各账号站内信，逐账号落盘 messagesCache。

    每轮都取数，不做数据新鲜度跳过（那是刷新登录状态的规则）。
    force=True（30 秒快刷）：只用已保存会话取数，密码已错、需验证码、
    冷却中或会话失效的账号直接跳过，不登录，避免顶掉网页端会话。
    缓存里已有消息时，本次拉到的新 receive_id 会发出 new_ready 信号
    （首次拉取的账号不算新消息）。"""

    account_done = Signal(str, int, str, str)  # user, count, status, message
    new_ready = Signal(list)  # 新消息摘要 [{account, title, content, time}]
    all_done = Signal()

    MAX_WORKERS = 4
    # 快刷时已知不可用的账号状态：不再尝试（密码错误的账号反复登录会被锁定）
    FAST_SKIP_HEALTH = {"failed", "captcha", "cooldown"}

    def __init__(self, accounts: list[dict], force: bool = False):
        super().__init__()
        self.accounts = accounts
        self.force = force
        self._new: list[dict] = []
        self._stop = False

    def stop(self):
        self._stop = True

    def _fetch_one(self, acc: dict):
        user, pwd = acc.get("userName", ""), acc.get("password", "")
        if not user or not pwd:
            return user, 0, "empty", "账号或密码为空"
        # 快刷时密码错误/冷却中的账号直接跳过，不发起网络请求
        if self.force and (acc.get("health") or {}).get("status") in self.FAST_SKIP_HEALTH:
            return user, 0, "cooldown", "该账号不可用（密码错误/冷却中），快刷跳过"
        try:
            client = SiteClient(user, pwd)
            kind, msg = login_gate.ensure_logged_in(client, allow_login=not self.force)
            if kind != "ok":
                return user, 0, kind, msg
            views = fetch_all_messages(client, user)

            def _write_cache(cfg: dict, user=user, views=views):
                # 合并而非整段覆盖：线上通知始终是「未读」，重复拉取按 receive_id
                # 去重，已读标记（messagesRead）也在本地，旧消息不会反复涌入列表
                cache = cfg.get("messagesCache", {})
                old = {
                    m.get("receive_id") or m.get("receiveId")
                    for m in cache.get(user, {}).get("messages", [])
                }
                had_cache = bool(cache.get(user, {}).get("messages"))
                merged = {
                    m.get("receive_id"): m
                    for m in cache.get(user, {}).get("messages", [])
                    if m.get("receive_id")
                }
                fresh_rows = []
                for v in views:
                    row = v.__dict__ | {"kind": v.kind}
                    merged[v.receive_id] = row
                    # 已有缓存时，缓存里没有的 receive_id 才算新消息
                    if had_cache and v.receive_id not in old:
                        fresh_rows.append({
                            "account": user,
                            "title": v.title,
                            "content": (v.content or "")[:120],
                            "time": v.time_str,
                        })
                cache[user] = {
                    "fetchedAt": datetime.now(timezone.utc).isoformat(),
                    "messages": list(merged.values()),
                }
                cfg["messagesCache"] = cache
                self._new.extend(fresh_rows)

            # 逐账号落盘（store.update 持锁整段读写），中途停掉不丢已拉取的消息
            store.update(_write_cache)
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
        if self._new and not self._stop:
            self.new_ready.emit(self._new)
        self.all_done.emit()


class MessageOpenWorker(QThread):
    """点击消息：按消息所属支部账号登录 → 携带 cookie 打开网页端。"""

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
        self._name_index: dict[str, str] = {}
        self._shown: dict[str, int] = {}  # 每个分组已渲染条数（防一次建上千卡片卡死 UI）
        self._silent = False  # 自动拉取时不弹成功提示

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
        self.read_combo = ComboBox()
        self.read_combo.addItems(["未读", "已读", "全部"])
        self.read_combo.setCurrentIndex(0)  # 默认只看未读
        self.read_combo.currentIndexChanged.connect(lambda *_: self._rebuild())
        self.fetch_btn = PrimaryPushButton(FIF.SYNC, "拉取站内信")
        self.fetch_btn.clicked.connect(lambda: self._fetch(silent=False))
        self.mark_read_btn = PushButton(FIF.CHECKBOX, "全部标为已读")
        self.mark_read_btn.clicked.connect(self._mark_all_read)
        row.addWidget(self.filter_combo, 0)
        row.addSpacing(12)
        row.addWidget(self.read_combo, 0)
        row.addSpacing(16)
        row.addWidget(self.fetch_btn, 0)
        row.addWidget(self.mark_read_btn, 0)
        row.addStretch(1)
        bar_lay.addLayout(row)
        self.stat_label = note_label("")
        bar_lay.addWidget(self.stat_label)
        lay.addWidget(bar)

        # 分组消息列表
        self.list_box = QWidget()
        self.list_lay = QVBoxLayout(self.list_box)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(8)
        self.list_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.add_table(self.list_box, 1)

    def showEvent(self, e):
        super().showEvent(e)
        self._load_cache()
        self.auto_fetch()

    # ---------- 数据 ----------

    def _cache_digest(self) -> str:
        """消息列表依赖的配置指纹：消息缓存 + 成员缓存（姓名索引）+ 已读标记。
        三者都没变时 _load_cache 直接跳过，30 秒快刷不再全量重建卡片。"""
        cfg = store.load()
        raw = json.dumps([cfg.get("messagesCache", {}), cfg.get("messagesRead", {}),
                          cfg.get("memberCache", {})], sort_keys=True, ensure_ascii=False)
        return hashlib.md5(raw.encode()).hexdigest()

    def _load_cache(self):
        """从缓存组装视图（拉取后也会刷新）；按 (账号, receive_id) 去重，
        兼容旧缓存里已被重复写入的同一条消息。"""
        digest = self._cache_digest()
        if digest == getattr(self, "_last_digest", None) and self._views:
            return
        self._last_digest = digest
        cfg = store.load()
        cache = cfg.get("messagesCache", {})
        views: list[MessageView] = []
        seen: set[tuple] = set()
        for user, blob in cache.items():
            for d in blob.get("messages", []):
                d = dict(d)
                d.setdefault("account", user)
                # 兼容缓存键与 API 原始键两种写法
                d.setdefault("receive_id", d.get("receiveId", 0))
                key = (user, d.get("receive_id"))
                if key in seen:
                    continue
                seen.add(key)
                d.setdefault("status_str", d.get("statusStr", ""))
                d.setdefault("time_str", d.get("sendTime", d.get("time_str", "")))
                d.setdefault("mobile", d.get("mobile", "") or "")
                views.append(MessageView(**{
                    k: d[k] for k in MessageView.__dataclass_fields__ if k in d
                }))
        views.sort(key=lambda v: v.time_str, reverse=True)
        self._views = views
        self._name_index = build_member_name_index(
            cfg.get("memberCache", {}), self._local_rows())
        self._rebuild()

    def _local_rows(self) -> list:
        """本地名单直接复用「系统配置」已解析的各学期名单（不在 GUI 线程重复读 xlsx）。"""
        rows = []
        for roster in self.window_ref.settings_page.rosters.values():
            rows.extend(roster.rows)
        return rows

    def auto_fetch(self):
        """启动时只在缓存为空时补拉一次；之后的数据同步交给 30 秒定时链。"""
        if not store.load().get("messagesCache"):
            self._fetch(silent=True)

    # ---------- 已读 / 未读 ----------

    @staticmethod
    def _read_key(v: MessageView) -> str:
        return f"{v.account}:{v.receive_id}"

    def _read_marks(self) -> dict:
        """已读标记表。重建开始时整份载入缓存，避免逐条消息重复解析配置。"""
        if getattr(self, "_read_marks_cache", None) is None:
            self._read_marks_cache = store.load().get("messagesRead") or {}
        return self._read_marks_cache

    def _is_unread(self, v: MessageView) -> bool:
        """站点状态未读，且没有被本地标记为已读。"""
        return v.status == 0 and self._read_key(v) not in self._read_marks()

    def _set_read(self, v: MessageView):
        store.update(lambda cfg: (cfg.setdefault("messagesRead", {})
                                  ).__setitem__(self._read_key(v), 1))
        if getattr(self, "_read_marks_cache", None) is not None:
            self._read_marks_cache[self._read_key(v)] = 1

    def _mark_all_read(self):
        unread = [v for v in self._views if self._is_unread(v)]
        if not unread:
            InfoBar.info("没有未读消息", "全部消息都已读过", duration=3000, parent=self)
            return

        def _write(cfg: dict):
            marks = cfg.setdefault("messagesRead", {})
            for v in unread:
                marks[self._read_key(v)] = 1

        store.update(_write)
        self._read_marks_cache = None
        self._rebuild()
        InfoBar.success("已全部标为已读", f"共 {len(unread)} 条", duration=3000, parent=self)

    def _rebuild(self):
        self._read_marks_cache = None  # 本轮已读表重新载入
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        idx = self.filter_combo.currentIndex()
        read_idx = self.read_combo.currentIndex()  # 0 未读 1 已读 2 全部
        if read_idx == 0:
            base = [v for v in self._views if self._is_unread(v)]
        elif read_idx == 1:
            base = [v for v in self._views if not self._is_unread(v)]
        else:
            base = self._views
        unread_total = sum(1 for v in self._views if self._is_unread(v))
        counts = {k: sum(1 for v in base if v.kind == k) for k in KIND_LABEL}
        self.stat_label.setText(
            f"共 {len(self._views)} 条，未读 {unread_total}")
        if not base:
            hint = ("没有未读消息。" if read_idx == 0 else
                    "已读里还没有消息。" if read_idx == 1 else
                    "没有消息。")
            self.list_lay.addWidget(note_label(hint))
            return
        kinds = list(KIND_LABEL) if idx == 0 else [list(KIND_LABEL)[idx - 1]]
        for k in kinds:
            group = [v for v in base if v.kind == k]
            if not group and idx != 0:
                self.list_lay.addWidget(note_label("没有消息"))
                continue
            self.list_lay.addWidget(self._section_header(k, len(group)))
            shown = self._shown.setdefault(k, SECTION_PAGE)
            for v in group[:shown]:
                self.list_lay.addWidget(self._message_card(v, unread=self._is_unread(v)))
            if len(group) > shown:
                more = PushButton(f"显示更多（还有 {len(group) - shown} 条）")
                more.clicked.connect(lambda *_: self._show_more(k))
                self.list_lay.addWidget(more)

    def _show_more(self, kind: str):
        self._shown[kind] = self._shown.get(kind, SECTION_PAGE) + SECTION_PAGE
        self._rebuild()

    # ---------- UI ----------

    def _section_header(self, kind: str, count: int) -> QWidget:
        return section_title(f"{KIND_LABEL[kind]} {count} 条")

    def _message_card(self, v: MessageView, unread: bool) -> QWidget:
        card = CardWidget()
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        if not unread:
            # 已读：整卡变暗，不改字体颜色
            from PySide6.QtWidgets import QGraphicsOpacityEffect

            effect = QGraphicsOpacityEffect(card)
            effect.setOpacity(0.45)
            card.setGraphicsEffect(effect)

        v_out = QVBoxLayout(card)
        v_out.setContentsMargins(18, 12, 18, 12)
        v_out.setSpacing(6)

        head = QHBoxLayout()
        name = self._name_index.get(v.mobile, "")
        tag_title = clean_title(v.title)
        # 主标题：团员姓名 → 原标题（去【系统通知】括号）→ 类别名；
        # 站内信标题可能整串都是中括号（剥完为空），此时按类别展示
        title_text = name or tag_title or KIND_LABEL[v.kind]
        title = theme_font(StrongBodyLabel(title_text), 15, int(QFont.Weight.DemiBold))
        head.addWidget(title)
        # 右侧小标签：彩色底 + 白字（不改字体颜色）
        if name and tag_title:
            head.addSpacing(8)
            tag = CaptionLabel(tag_title)
            tag.setStyleSheet(
                f"background-color: {'#4b5563' if isDarkTheme() else '#6b7280'};"
                "color: white; border-radius: 4px; padding: 1px 8px;")
            head.addWidget(tag)
        if unread:
            head.addSpacing(6)
            unread_tag = CaptionLabel("未读")
            unread_tag.setStyleSheet(
                "background-color: #0067f4; color: white; border-radius: 4px; padding: 1px 8px;")
            head.addWidget(unread_tag)
        head.addStretch(1)
        head.addWidget(CaptionLabel(v.time_str))
        head.addSpacing(12)
        head.addWidget(CaptionLabel(v.account))
        v_out.addLayout(head)

        body = note_label(v.content or "")
        body.setWordWrap(True)
        v_out.addWidget(body)
        card.clicked.connect(lambda *_: self._open_message(v))
        return card

    # ---------- 动作 ----------

    def _fetch(self, silent: bool):
        usable, excluded = split_accounts(self.window_ref.settings_page.accounts)
        accounts = usable
        if not accounts:
            hint = f"其余账号均被标记为异常（{excluded_note(excluded)}）" if excluded else "先在系统配置添加团支部账号"
            InfoBar.warning("没有可拉取的账号", hint, duration=6000, parent=self)
            return
        if excluded and not silent:
            InfoBar.warning("跳过异常账号",
                f"{excluded_note(excluded)} 不拉取站内信；测活恢复正常后自动纳入",
                duration=6000, parent=self)
        if self.worker is not None and self.worker.isRunning():
            return
        self._silent = silent
        self.fetch_btn.setEnabled(False)
        self.worker = MessageFetchWorker(accounts)
        self.worker.account_done.connect(self._on_fetch_one)
        self.worker.all_done.connect(self._on_fetch_done)
        self.worker.start()

    def _on_fetch_one(self, user, count, status, message):
        if status in login_gate.SKIP_KINDS:
            InfoBar.info(f"账号 {user} 跳过拉取", message, duration=4000, parent=self)
        elif status != "ok":
            InfoBar.warning(
                f"账号 {user or '（空）'} 拉取失败",
                f"{message}（其他账号不受影响）", duration=4000, parent=self)

    def _on_fetch_done(self):
        self.fetch_btn.setEnabled(True)
        self._load_cache()
        if not self._silent:
            InfoBar.success("拉取完成", "站内信已更新并保留在本地", duration=3000, parent=self)

    def _open_message(self, v: MessageView):
        self._set_read(v)  # 点开即在本地标记已读，卡片移出未读
        self._rebuild()
        if self.open_worker is not None and self.open_worker.isRunning():
            return
        # 消息从哪个账号的收件箱拉来，就打开哪个账号（支部即账号，无需再定位团员）
        user = v.account
        accounts = self.window_ref.settings_page.accounts
        acc = next((a for a in accounts if a.get("userName") == user), None)
        if not acc or not acc.get("password"):
            InfoBar.warning("账号不可用", f"账号 {user or '未标注'} 不在系统配置或缺少密码",
                            duration=4000, parent=self)
            return
        self.open_worker = MessageOpenWorker(user, acc["password"], self)
        self.open_worker.done.connect(self._on_opened)
        self.open_worker.start()

    def _on_opened(self, ok: bool, msg: str):
        if ok:
            InfoBar.success("打开网页", msg, duration=5000, parent=self)
        else:
            InfoBar.warning("打开网页", msg, duration=8000, parent=self)
