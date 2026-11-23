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

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal as Signal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    InfoBar,
    PrimaryPushButton,
    PushButton,
    TransparentToolButton,
    SegmentedWidget,
    StrongBodyLabel,
)

from app.domain.messages import (
    KIND_LABEL,
    MessageView,
    build_member_name_index,
    clean_title,
    fetch_all_messages,
    guess_member_name,
)
from app.domain.account_health import excluded_summary, split_accounts
from app.ui import palette as P
from app.services import login_gate, store
from app.services.api import SiteClient
from app.ui.page import (
    BusyOverlay,
    FluentPage,
    set_tip,
    PageBanner,
    note_label,
    theme_font,
    triangle_icon,
)
from app.ui.widgets import PillLabel

KINDS = list(KIND_LABEL)
PAGE_SIZE = 12  # 每页卡片数：正好铺满一屏，翻页条落在首屏底部看得到
RENDER_BATCH = 6  # 一页内再分两批建卡，首屏立刻可见


class MessageLoadWorker(QThread):
    """后台读配置并组装消息数据。

    配置里消息、成员、已读三份缓存加起来可达数 MB，JSON 解析放在 GUI 线程里
    会让切页明显停顿；这里只在后台读出原始字典，视图对象仍由界面线程构造。
    """

    loaded = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            cfg = store.load()
        except Exception as e:  # 配置损坏时不让线程悄悄死掉
            self.failed.emit(str(e))
            return
        cache = cfg.get("messagesCache") or {}
        read = cfg.get("messagesRead") or {}
        members = cfg.get("memberCache") or {}
        raw = json.dumps([cache, read, members], sort_keys=True, ensure_ascii=False)
        self.loaded.emit({
            "digest": hashlib.md5(raw.encode()).hexdigest(),
            "cache": cache,
            "read": read,
            "memberCache": members,
        })


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


class MessagesPage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__("page-messages", parent)
        self.window_ref = window
        self.worker: MessageFetchWorker | None = None
        self._views: list[MessageView] = []
        self._name_index: dict[str, str] = {}
        self._search = ""  # 标题栏搜索框注入的关键词
        self._filtered: list = []  # 当前筛选结果（整份，不分页）
        self._page = 0
        self._render_token = 0  # 翻页/筛选变化时丢弃上一轮未完成的续渲染
        self._loader: MessageLoadWorker | None = None
        self._silent = False  # 自动拉取时不弹成功提示
        self._busy: BusyOverlay | None = None  # 首次加载遮罩，列表建完才收
        self._initial_loaded = False

        lay = self.vBoxLayout
        # 顶部留白让出头图位置（头图通栏贴顶、覆盖在内容之上），其余三边与内容对齐
        lay.setContentsMargins(32, PageBanner.HEIGHT + 16, 32, 24)

        # 头图通栏贴顶（overlay，不占布局），标题与操作叠在 banner 上；stat 行作副标题
        self.header = PageBanner("系统消息", "站内信与系统通知", self.scrollWidget)
        self.banner = self.header
        from app.services import store
        self.header.set_image_path(store.load().get("headerImage", ""))
        self._place_banner()
        self.stat_label = self.header.desc_label

        self.kind_seg = SegmentedWidget()
        for key in KINDS:
            self.kind_seg.addItem(key, KIND_LABEL[key])
        self.kind_seg.setCurrentItem(KINDS[0])
        self.kind_seg.currentItemChanged.connect(lambda *_: self._rebuild())
        self.kind_seg.setFixedHeight(34)

        self.read_seg = SegmentedWidget()
        self.read_seg.addItem("unread", "未读")
        self.read_seg.addItem("read", "已读")
        self.read_seg.addItem("all", "全部")
        self.read_seg.setCurrentItem("unread")
        self.read_seg.currentItemChanged.connect(lambda *_: self._rebuild())
        self.read_seg.setFixedHeight(34)

        self.fetch_btn = PrimaryPushButton(FIF.SYNC, "拉取消息")
        self.fetch_btn.clicked.connect(lambda: self._fetch(silent=False))
        self.mark_read_btn = PushButton(FIF.CHECKBOX, "全部已读")
        self.mark_read_btn.clicked.connect(self._mark_all_read)
        self.header.add_control(self.fetch_btn)
        self.header.add_control(self.mark_read_btn)

        self.stat_label.setWordWrap(False)

        # 已知问题公告（banner 下方、页签之上）：不重复弹窗，问题都收在这里；
        # 空清单时整块隐藏，正常情况不出现。
        from app.ui.notice_panel import NoticePanel

        self._notice_center = getattr(window, "notice_center", None)
        self.notice_panel = NoticePanel(self._on_notice_action, self)
        lay.addWidget(self.notice_panel)
        if self._notice_center is not None:
            self._notice_center.changed.connect(self._sync_notices)
        self._sync_notices()

        tools = QWidget()
        tools_lay = QHBoxLayout(tools)
        tools_lay.setContentsMargins(0, 2, 0, 8)
        tools_lay.setSpacing(10)
        tools_lay.addWidget(self.kind_seg, 0)
        tools_lay.addStretch(1)
        tools_lay.addWidget(self.read_seg, 0)
        lay.addWidget(tools)

        self.list_box = QWidget()
        self.list_lay = QVBoxLayout(self.list_box)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(6)
        self.list_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.list_box.setMinimumHeight(180)
        lay.addWidget(self.list_box)

        # 翻页放在列表底下：左右实心三角 + 当前页，居中一行
        self.prev_btn = TransparentToolButton(triangle_icon("left"))
        set_tip(self.prev_btn, "上一页")
        self.prev_btn.clicked.connect(lambda *_: self._flip(-1))
        self.next_btn = TransparentToolButton(triangle_icon("right"))
        set_tip(self.next_btn, "下一页")
        self.next_btn.clicked.connect(lambda *_: self._flip(1))
        self.page_label = note_label("")
        pager = QWidget()
        pager_lay = QHBoxLayout(pager)
        pager_lay.setContentsMargins(0, 10, 0, 4)
        pager_lay.setSpacing(6)
        pager_lay.addStretch(1)
        pager_lay.addWidget(self.prev_btn, 0)
        pager_lay.addWidget(self.page_label, 0)
        pager_lay.addWidget(self.next_btn, 0)
        pager_lay.addStretch(1)
        lay.addWidget(pager)
        self._sync_pager()

    def set_search(self, text: str):
        """标题栏搜索框变化：按姓名、标题、正文过滤当前页签。"""
        text = (text or "").strip()
        if text == self._search:
            return
        self._search = text
        self._rebuild()

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

    def _show_busy(self, text: str = "正在加载系统消息…"):
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

    def _finish_initial_load(self):
        """首份数据已渲染。空缓存还要等自动拉取结束，避免先露出空态再冒出消息。"""
        if self.worker is not None and self.worker.isRunning():
            if self._busy is not None:
                self._busy.set_text("正在拉取系统消息…")
            return
        self._initial_loaded = True
        self._hide_busy()

    def _sync_notices(self):
        """清单变化：面板按 key 增量刷新（不清空重建，避免闪烁）。

        这一页是消息总览，不过滤归属；各处理页自己的面板只列属于它的那几类。"""
        center = getattr(self, "_notice_center", None)
        if center is not None and getattr(self, "notice_panel", None) is not None:
            self.notice_panel.set_notices(center.snapshot())

    def _on_notice_action(self, action: str):
        """公告里的处理入口 → 交给主窗口跳转定位。"""
        handler = getattr(self.window_ref, "handle_notice_action", None)
        if handler is not None:
            handler(action)

    def showEvent(self, e):
        super().showEvent(e)
        self._place_banner()
        self._place_busy()
        # 首帧用加载页盖住尚未组装的列表；之后再切回来不重复铺，免得整页闪一下
        if not self._initial_loaded:
            self._show_busy()
        self._load_cache()
        self.auto_fetch()

    # ---------- 数据 ----------

    def _load_cache(self):
        """后台读缓存；正在加载时重复调用直接返回，不叠线程。"""
        if self._loader is not None and self._loader.isRunning():
            return
        self._loader = MessageLoadWorker(self)
        self._loader.loaded.connect(self._on_cache_loaded)
        self._loader.failed.connect(self._on_cache_failed)
        self._loader.start()

    def _on_cache_failed(self, message: str):
        self._hide_busy()
        if not self.list_lay.count():
            self.list_lay.addWidget(note_label("消息加载失败"))
        InfoBar.error("消息缓存读取失败", message, duration=8000, parent=self)

    def _on_cache_loaded(self, payload: dict):
        """缓存没变就跳过；变了才重组视图并重置到第一页。"""
        first_load = not self._initial_loaded
        if payload["digest"] == getattr(self, "_last_digest", None) and self._views:
            if first_load:
                self._finish_initial_load()
            return
        self._last_digest = payload["digest"]
        self._read_marks_cache = payload["read"]
        views: list[MessageView] = []
        seen: set[tuple] = set()
        for user, blob in payload["cache"].items():
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
            payload["memberCache"], self._local_rows())
        # 30 秒快刷每次都会重写 messagesCache：数据没变也重建一遍列表，
        # 卡片一批批重长出来，看着就是闪。内容摘要一样就只更新计数，不动卡片。
        digest = self._content_digest(views)
        if digest == getattr(self, "_content_digest_last", None) and self.list_lay.count():
            if first_load:
                self._finish_initial_load()
            return
        self._content_digest_last = digest
        self._rebuild(on_done=self._finish_initial_load if first_load else None)

    @staticmethod
    def _content_digest(views: list) -> str:
        """只看影响列表显示的三项：账号、消息号、时间。"""
        return "|".join(f"{v.account}:{v.receive_id}:{v.time_str}" for v in views[:120])

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

    def _rebuild(self, on_done=None):
        """按页签、已读与搜索词重新筛选，回到第一页并分批建卡。"""
        self._read_marks_cache = None
        kind = self.kind_seg.currentRouteKey() or KINDS[0]
        read_mode = self.read_seg.currentRouteKey() or "unread"
        if read_mode == "unread":
            base = [v for v in self._views if self._is_unread(v)]
        elif read_mode == "read":
            base = [v for v in self._views if not self._is_unread(v)]
        else:
            base = self._views

        if self._search:
            needle = self._search.lower()

            def _hit(v: MessageView) -> bool:
                name = guess_member_name(v.mobile, v.content, self._name_index)
                return (needle in (v.content or "").lower()
                        or needle in (v.title or "").lower()
                        or needle in name.lower())

            base = [v for v in base if _hit(v)]

        unread_total = sum(1 for v in self._views if self._is_unread(v))
        group = [v for v in base if v.kind == kind]
        self._filtered = group
        self._title_w = self._measure_title_width(group)
        self._page = 0  # 换页签、换已读状态或改搜索词都从头看
        self.header.set_desc(
            f"共 {len(self._views)} 条，未读 {unread_total}，"
            f"{KIND_LABEL[kind]} {len(group)} 条")
        if not group:
            self._clear_list()
            hint = ("没有未读消息。" if read_mode == "unread" else
                    "这类消息都还没读过。" if read_mode == "read" else "没有消息。")
            self.list_lay.addWidget(note_label(hint))
            self._sync_pager()
            if on_done is not None:
                on_done()
            return
        self._render_page(on_done)

    def _measure_title_width(self, views: list) -> int:
        """姓名/类别列宽：按本组最长标题算，别把「志愿书退回修改」裁掉。

        列宽固定是为了让每行的胶囊标签从同一条竖线开始；按组算一次，翻页时
        不跳。上限 180：再长也不该由这一列撑开，宁可让它自己省略。
        """
        probe = theme_font(StrongBodyLabel(""), P.FONT["body"], int(QFont.Weight.DemiBold))
        metrics = probe.fontMetrics()
        need = 84
        for view in views:
            name = guess_member_name(view.mobile, view.content, self._name_index)
            text = name or clean_title(view.title) or KIND_LABEL[view.kind]
            need = max(need, metrics.horizontalAdvance(text) + 2)
        probe.deleteLater()
        return min(need, 180)

    def _clear_list(self):
        self._render_token += 1  # 作废上一轮可能还挂着的续渲染
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 先隐藏再 deleteLater：销毁要等下一轮事件循环，旧卡片会和新
                # 卡片叠一帧，文字就糊成两层（组织管理切页踩过同一个坑）。
                widget.hide()
                widget.deleteLater()

    def _render_page(self, on_done=None):
        """只建当前页的 20 张卡，并分两批塞进布局，单批不超过 10 张。"""
        self._clear_list()
        start = self._page * PAGE_SIZE
        self._queue = self._filtered[start:start + PAGE_SIZE]
        self._queue_index = 0
        token = self._render_token
        self._drain(token, on_done)

    def _drain(self, token: int, on_done=None):
        if token != self._render_token:
            return
        batch = self._queue[self._queue_index:self._queue_index + RENDER_BATCH]
        for view in batch:
            self.list_lay.addWidget(
                self._message_card(view, unread=self._is_unread(view)))
        self._queue_index += len(batch)
        if self._queue_index < len(self._queue):
            QTimer.singleShot(0, lambda: self._drain(token, on_done))
            return
        self._sync_pager()
        if on_done is not None:
            on_done()

    def _sync_pager(self):
        """翻页控件在列表底下，页数用「第 2 / 5 页」这样的简写。"""
        pages = max(1, (len(self._filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
        if not getattr(self, "_filtered", None):
            self.prev_btn.setVisible(False)
            self.next_btn.setVisible(False)
            self.page_label.setVisible(False)
            return
        self.prev_btn.setVisible(pages > 1)
        self.next_btn.setVisible(pages > 1)
        self.page_label.setVisible(pages > 1)
        self.prev_btn.setEnabled(self._page > 0)
        self.next_btn.setEnabled(self._page < pages - 1)
        self.page_label.setText(f"第 {self._page + 1} / {pages} 页")

    def _flip(self, delta: int):
        pages = max(1, (len(self._filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(self._page + delta, pages - 1))
        if page == self._page:
            return
        self._page = page
        self._render_page()
        self.verticalScrollBar().setValue(0)
        # 翻页只换一屏卡片：淡入表示"内容换了"，不做位移以免整块跳动
        from app.ui.anim import fade_in

        fade_in(self.list_box, duration=160, dy=0)

    def apply_theme(self):
        """主题变化：只让按主题取色的卡片重设一次颜色，不重建整页控件。"""
        for i in range(self.list_lay.count()):
            card = self.list_lay.itemAt(i).widget()
            hook = getattr(card, "apply_theme", None)
            if hook is not None:
                hook()
        # 翻页三角是按主题手绘的，换肤后要重新画
        self.prev_btn.setIcon(triangle_icon("left"))
        self.next_btn.setIcon(triangle_icon("right"))

    # ---------- UI ----------

    def _message_card(self, v: MessageView, unread: bool) -> QWidget:
        """一条消息：姓名 + 原标题标签 + 未读标签 … 时间、账号固定宽度右对齐。

        卡片圆角放大到 12，整行铺满内容宽度（与首页/组织页共用 FluentPage
        的 32px 页边距），小间距分隔，恢复以前那种成组卡片的观感。"""
        card = CardWidget()
        card.setBorderRadius(P.RADIUS["panel"])
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(18, 10, 18, 10)
        outer.setSpacing(3)

        head = QHBoxLayout()
        head.setSpacing(8)
        name = guess_member_name(v.mobile, v.content, self._name_index)
        tag_title = clean_title(v.title)
        title_text = name or tag_title or KIND_LABEL[v.kind]
        title = theme_font(StrongBodyLabel(title_text), P.FONT["body"], int(QFont.Weight.DemiBold))
        # 姓名、类别、未读各占一列固定宽度，否则每行标签起点都不一样，看着参差不齐
        title.setFixedWidth(getattr(self, "_title_w", 84))
        head.addWidget(title, 0)

        def _tint_title(t=title, dim=not unread):
            """已读卡片的标题压暗；主题换了要重设一次，颜色是按主题取的。"""
            if dim:
                t.setTextColor(*P.text_colors("fg3"))

        _tint_title()

        if name and tag_title:
            tag = PillLabel(tag_title, "gray")
            tag.set_fixed_width(132)
            head.addWidget(tag, 0)

        if unread:
            flag = PillLabel("未读", "blue")
            flag.set_fixed_width(52)
            head.addWidget(flag, 0)

        head.addStretch(1)
        # 时间、账号各占一列固定宽度并左对齐：所有行从同一条竖线开始，
        # 右对齐会因数字宽窄差异导致左边缘参差不齐
        when = CaptionLabel(v.time_str)
        when.setFixedWidth(148)
        when.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(when, 0)
        account = CaptionLabel(v.account)
        account.setFixedWidth(92)
        account.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(account, 0)
        outer.addLayout(head)

        body = note_label(v.content or "")
        body.setWordWrap(True)
        outer.addWidget(body)
        card.clicked.connect(lambda *_: self._open_message(v))
        card.apply_theme = _tint_title  # 主题切换时由列表统一回调
        return card

    # ---------- 动作 ----------

    def _fetch(self, silent: bool):
        usable, excluded = split_accounts(self.window_ref.settings_page.accounts)
        accounts = usable
        if not accounts:
            hint = f"{len(excluded)} 个账号均被标记为异常" if excluded else "先在系统配置添加团支部账号"
            InfoBar.warning("没有可拉取的账号", hint, duration=6000, parent=self)
            return
        if excluded and not silent:
            InfoBar.warning("跳过异常账号",
                f"{excluded_summary(excluded)}，本轮不拉取站内信；测活恢复正常后自动纳入",
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
        """点开消息：标为已读，并在内嵌浏览器里打开该支部的标签页（不拉起外部浏览器）。"""
        self._set_read(v)
        self._rebuild()
        # 消息从哪个账号的收件箱拉来，就打开哪个账号（支部即账号，无需再定位团员）
        user = v.account
        acc = next((a for a in self.window_ref.settings_page.accounts
                    if a.get("userName") == user), None)
        if not acc or not acc.get("password"):
            InfoBar.warning("账号不可用", f"账号 {user or '未标注'} 不在系统配置或缺少密码",
                            duration=4000, parent=self)
            return
        self.window_ref.open_browser(user)
