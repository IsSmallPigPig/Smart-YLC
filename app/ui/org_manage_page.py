"""组织管理页：支部卡片列表 → 支部详情。

头图只属于支部列表；进支部详情后整块收掉，标题直接贴顶（详情不需要再用大图
打招呼，那 196px 全是从统计表上挪走的高度）。返回统一由页顶面包屑「组织管理」、
主窗口标题栏返回按钮和 Esc 承担，详情页不再放孤立的小箭头。
视图切换、成员展开、统计数字与条形图都带过渡。
统计从成员缓存的系统记录离线计算，不发生网络请求。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from PyQt5.QtCore import QSize, Qt, QRectF, QThread, pyqtSignal as Signal
from PyQt5.QtGui import QColor, QFont, QPainter
from PyQt5.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    BreadcrumbBar,
    BreadcrumbItem,
    CardWidget,
    FluentIcon as FIF,
    IconWidget,
    InfoBar,
    PrimaryPushButton,
    PrimaryToolButton,
    StrongBodyLabel,
    ToggleToolButton,
    ToolButton,
    TransparentToolButton,
    isDarkTheme,
    qconfig,
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
    STATUS_FILTERS,
    STATUS_LABEL,
    BranchNode,
    MemberView,
    branch_status,
    build_branches,
    filter_members,
)
from app.domain.account_health import excluded_summary, split_accounts
from app.services import login_gate, store
from app.services.api import SiteClient
from app.ui.anim import animate_expand, fade_in, value_animation
from app.ui.charts import BarRowWidget
from app.ui.combo import ComboBox
from app.ui import palette as P
from app.domain.check_templates import default_identity_template, default_report_template
from app.ui.export_flow import start_export
from app.ui.page import (BatchedBuild, BusyOverlay, CardRow, FluentPage,
                         PageBanner, chevron_icon, note_label, paint_tinted_card,
                         set_tip, section_title, theme_font)

SOURCE_COLOR = P.SOURCE
KIND_COLOR = P.KIND
KIND_TEXT = {"identity": "信息错误", "report": "没有报道", "archive": "没有档案", "fees": "欠缴团费"}


def _rec_from_dict(d: dict) -> MemberRecord:
    fields = {f for f in MemberRecord.__dataclass_fields__}
    return MemberRecord(**{k: v for k, v in d.items() if k in fields})


def _tint(color: str, alpha: int = 26) -> str:
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


def _pill(text: str, color: str) -> BodyLabel:
    """柔和的彩色小标签：深浅主题下都按主题取文字色，不用饱和色块。"""
    c = QColor(color)
    fg = c.lighter(150).name() if isDarkTheme() else c.darker(115).name()
    lbl = BodyLabel(text)
    theme_font(lbl, P.FONT["caption"], int(QFont.Weight.Medium))
    lbl.setStyleSheet(
        f"background-color: {_tint(color, 40 if isDarkTheme() else 24)};"
        f"color: {fg}; border-radius: 9px; padding: 2px 10px;")
    return lbl



class BranchCard(CardWidget):
    """支部卡片：只有异常支部铺 warning 底色，右侧箭头随 hover 渐变。

    正常与纯欠缴的支部保持卡片原样——底色留给真正需要处理的问题，
    满屏都是彩色底反而看不出哪张卡片要管。
    """

    def __init__(self, role: str | None, parent=None):
        super().__init__(parent)
        self.role = role
        self._hover = 0.0
        self.setClickEnabled(True)
        self.setBorderRadius(P.RADIUS["control"])
        self._arrow = FIF.CHEVRON_RIGHT_MED.icon().pixmap(14, 14)

    def _animate_hover(self, target: float):
        value_animation(self, self._hover, target,
                        lambda v: self._set_hover(float(v)), duration=160)

    def _set_hover(self, value: float):
        self._hover = value
        self.update()

    def enterEvent(self, e):
        super().enterEvent(e)
        self._animate_hover(1.0)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._animate_hover(0.0)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.role:
            paint_tinted_card(self, p, self.role, radius=8, hover=self._hover)
        x = self.width() - 26 + 5 * self._hover
        p.setOpacity(0.55 + 0.45 * self._hover)
        p.drawPixmap(int(x), int((self.height() - 14) / 2), self._arrow)
        p.end()


class ClickRow(CardRow):
    """带点击信号的细行：整行可点，底部细分割线代替一张张卡片。"""

    clicked = Signal()

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        self.clicked.emit()


class _CrumbItem(BreadcrumbItem):
    """面包屑节点：按主题文字色绘制。

    官方节点把选中态压成接近正文色、非选中态压得很淡，两档在深色底下都偏灰；
    这里自己画文字与分隔箭头，非选中态留 0.82 不透明度，既分层又不至于看不清。
    其余交互（点击、选中态）沿用父类。"""

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        ink = P.fg("fg2")
        sw = self.spacing * 2
        if not self.isRoot():
            iw = self.font().pixelSize() / 14 * 8
            rect = QRectF((sw - iw) / 2, (self.height() - iw) / 2 + 1, iw, iw)
            p.setOpacity(0.6)
            FIF.CHEVRON_RIGHT_MED.render(p, rect, fill=ink.name())

        p.setOpacity(1.0 if (self.isSelected or self.isHover) else 0.82)
        p.setFont(self.font())
        p.setPen(ink)
        rect = (self.rect() if self.isRoot()
                else QRectF(sw, 0, self.width() - sw, self.height()))
        p.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self.text)


class CrumbBar(BreadcrumbBar):
    """官方面包屑导航：支部详情页顶部单独一行，按内容取宽。

    qfluentwidgets 的 BreadcrumbBar 没重写 sizeHint、也只在 resizeEvent 里重排节点；
    放进横向布局会被压成 0 宽、且首次显示时宽度还没就绪会把首节点收进省略按钮。
    这里补 sizeHint 给偏好宽度，并在显示后强制重排一次。"""

    def addItem(self, routeKey: str, text: str):
        """节点换成按主题取色的一版。"""
        if routeKey in self.itemMap:
            return
        if not getattr(self, "_theme_hooked", False):
            # 绑定方法而非 lambda：面包屑销毁后 qconfig 仍会发信号，
            # lambda 会让它打进悬空对象（PyQt5 里直接 abort）
            qconfig.themeChanged.connect(self._repaint_items)
            self._theme_hooked = True
        item = _CrumbItem(routeKey, text, len(self.items), self)
        item.setFont(self.font())
        item.setSpacing(self.spacing)
        item.clicked.connect(lambda: self.setCurrentItem(routeKey))

        self.itemMap[routeKey] = item
        self.items.append(item)
        self.setFixedHeight(max(i.height() for i in self.items))
        self.setCurrentItem(routeKey)
        self.updateGeometry()

    def _repaint_items(self):
        """切主题后节点自己重绘：字色在 paintEvent 里按当前主题取。"""
        for item in self.items:
            item.update()

    def sizeHint(self):
        w = sum(i.width() for i in self.items) + 4
        h = max((i.height() for i in self.items), default=20)
        return QSize(w, h)

    def showEvent(self, e):
        super().showEvent(e)
        self.updateGeometry()


class MemberSyncWorker(QThread):
    """同步系统数据：用已保存的会话拉取各支部成员，逐账号写 memberCache。

    每轮都取数，不做数据新鲜度跳过（那是刷新登录状态的规则）。
    后台 30 秒静默同步串行执行，只用会话取数不登录，避免顶掉网页端会话；
    手动同步并发 ≤4，会话失效时才重新登录。"""

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
            kind, msg = login_gate.ensure_logged_in(client, allow_login=not self.sequential)
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
    detail_changed = Signal(bool)  # 详情态变化 → 主窗口返回按钮同步可用状态

    def __init__(self, window, parent=None):
        super().__init__("page-org-manage", parent)
        self.window_ref = window
        self.worker = None
        self._progress = None       # 同步期间的右下角浮动进度
        self._progress_done = 0
        self._progress_total = 0
        self._stopped = False
        self._workers: list = []
        self._busy: BusyOverlay | None = None  # 首次加载遮罩，卡片分批建完才收
        self._branches: list[BranchNode] = []
        self._stats_cache: dict = {}
        self._stats = {"local": 0, "system": 0, "both": 0}
        self._detail_code = ""
        # 支部卡片分批建：60 个支部一次性建要 190ms，切页会明显卡一下
        self._feed_builder = BatchedBuild(self, batch=6)
        # 团支部详情里的团员行也分批建：一个支部上百人时一次性建全会把切页卡成空白
        self._detail_builder = BatchedBuild(self, batch=8)
        self._search = ""  # 由主窗口标题栏的搜索框注入
        self._refresh_running = False  # 后台组织树刷新进行中（避免重复起线程）
        self._refresh_callbacks: list = []  # 刷新落地后补跑的动作（如跳转支部详情）
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        lay = self.vBoxLayout
        # 顶部留白让出头图位置（头图通栏贴顶、覆盖在内容之上），其余三边与内容对齐；
        # 支部详情没有头图，顶部边距由 _set_banner 收回到常规页边距
        lay.setContentsMargins(32, PageBanner.HEIGHT + 16, 32, 24)

        # ── 头图通栏贴顶（overlay，不占布局），标题与工具条叠在 banner 上 ──
        self.header = PageBanner("组织管理", "支部与团员管理", self.scrollWidget)
        self.banner = self.header
        from app.services import store
        self.header.set_image_path(store.load().get("headerImage", ""))
        self._place_banner()
        self._banner_on = True

        self.title_lbl = self.header.title_label
        self.stat_label = self.header.desc_label

        # 面包屑导航：只在支部详情出现（头图那时已收起），点「组织管理」回支部列表
        self.crumb_host = QWidget()
        crumb_lay = QHBoxLayout(self.crumb_host)
        crumb_lay.setContentsMargins(2, 0, 0, 0)
        crumb_lay.setSpacing(0)
        self.crumb_bar = CrumbBar(self.crumb_host)
        self.crumb_bar.currentItemChanged.connect(self._on_crumb)
        crumb_lay.addWidget(self.crumb_bar, 0, Qt.AlignmentFlag.AlignVCenter)
        crumb_lay.addStretch(1)
        self.crumb_host.setVisible(False)
        lay.addWidget(self.crumb_host)

        # 显示全部：只有图标，常亮即「看全部团员」，灭掉只看系统名单
        self.sys_only = ToggleToolButton(FIF.TILES)
        self.sys_only.setChecked(True)
        set_tip(self.sys_only, "显示全部团员；关掉只看系统名单")
        self.sys_only.toggled.connect(lambda *_: self._rebuild())

        # 状态筛选：全部 / 正常 / 异常 / 欠缴，与卡片左侧色条同一套判定
        self.status_combo = ComboBox()
        for key in STATUS_FILTERS:
            self.status_combo.addItem(STATUS_LABEL[key], userData=key)
        self.status_combo.setFixedWidth(104)
        set_tip(self.status_combo, "只看正常、异常或欠缴的支部")
        self.status_combo.currentIndexChanged.connect(lambda *_: self._rebuild())

        self.sync_btn = PrimaryPushButton(FIF.SYNC, "同步数据")
        set_tip(self.sync_btn, "同步系统数据：刷新成员、站内信与组织状况")
        self.sync_btn.clicked.connect(self._sync)
        self.export_btn = ToolButton(FIF.SAVE)
        self.export_btn.setFixedSize(32, 32)
        set_tip(self.export_btn, "导出数据")
        self.export_btn.clicked.connect(lambda: start_export(self))
        # 功能按钮统一落在 banner 右下角（不居中）；详情态由 _set_toolbar 收起
        self.header.add_control(self.status_combo)
        self.header.add_control(self.sys_only)
        self.header.add_control(self.export_btn)
        self.header.add_control(self.sync_btn)

        # 列表与详情：切换时新视图淡入并轻微上移
        self.feed_box = QWidget()
        self.feed_lay = QVBoxLayout(self.feed_box)
        self.feed_lay.setContentsMargins(0, 0, 0, 0)
        self.feed_lay.setSpacing(10)
        self.feed_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.feed_box, 1)

        self.detail_box = QWidget()
        self.detail_lay = QVBoxLayout(self.detail_box)
        self.detail_lay.setContentsMargins(0, 0, 0, 0)
        self.detail_lay.setSpacing(12)
        self.detail_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.detail_box.setVisible(False)
        lay.addWidget(self.detail_box, 1)
        lay.addStretch(1)
        self._set_nav(False)
        # 绑定方法而非 lambda：页面销毁后 qconfig 仍会发信号，
        # lambda 会让它打进悬空对象（PyQt5 里直接 abort）
        qconfig.themeChanged.connect(self._on_theme_changed)

    def _on_theme_changed(self, *_):
        """切主题：可见才重建（卡片颜色烘焙在构建时定死），藏着的等下次进入再说。"""
        if self.isVisible():
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
        top = PageBanner.HEIGHT if self._banner_on else 0
        self._busy.setGeometry(0, top, self.viewport().width(),
                               max(0, self.viewport().height() - top))
        self._busy.raise_()

    def _show_busy(self, text: str = "正在加载组织数据…"):
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
        import time

        now = time.monotonic()
        # 已经有树就别每次切页都重算：30 秒同步会自己刷新，切页只负责显示。
        # 原来 2 秒就过期，切回来必然清空重建，卡片一批批长出来，看着就是闪。
        if not self._branches or now - getattr(self, "_refreshed_at", 0) > 300:
            # 只有首次数据还没落地时铺加载页；后台静默刷新不打扰正在看的列表
            if not self._branches:
                self._show_busy()
            self.refresh()
            self._refreshed_at = now
        detail = bool(self._detail_code)
        self._set_nav(detail)
        self.feed_box.setVisible(not detail)
        self.detail_box.setVisible(detail)
        self.detail_changed.emit(detail)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self._detail_code:
            self.back_to_feed()
            return
        super().keyPressEvent(e)

    # ---------- 导航 ----------

    def in_detail(self) -> bool:
        return bool(self._detail_code)

    def go_back(self) -> bool:
        """供主窗口标题栏返回按钮调用：在详情态消费返回，返回是否已处理。"""
        if not self._detail_code:
            return False
        self.back_to_feed()
        return True

    def set_search(self, text: str):
        """标题栏搜索框变化：只改过滤词，重建列表。"""
        text = (text or "").strip()
        if text == self._search:
            return
        self._search = text
        self._rebuild()

    def _set_nav(self, detail: bool):
        self.title_lbl.setVisible(not detail)
        self.crumb_host.setVisible(detail)
        self.crumb_bar.setVisible(detail)
        self._set_banner(not detail)

    def _set_banner(self, on: bool):
        """支部列表用头图，支部详情收掉：顶部边距回到常规页边距，标题直接贴顶。"""
        if on == self._banner_on:
            return
        self._banner_on = on
        self.header.setVisible(on)
        top = PageBanner.HEIGHT + 16 if on else 16
        self.vBoxLayout.setContentsMargins(32, top, 32, 24)
        self._place_busy()

    def _set_toolbar(self, detail: bool):
        """详情里已有大标题和三列统计，工具条不再重复一遍人数。"""
        self.stat_label.setVisible(not detail)
        self.status_combo.setVisible(not detail)
        self.sys_only.setVisible(not detail)
        self.export_btn.setVisible(not detail)
        self.sync_btn.setVisible(not detail)

    def _set_crumb(self, branch_label: str):
        """重建面包屑为「组织管理 › 支部名」。

        用 blockSignals 包住重建：BreadcrumbBar.addItem 会自动选中并触发
        currentItemChanged，否则程序化重建会误触发返回。"""
        self.crumb_bar.blockSignals(True)
        self.crumb_bar.clear()
        self.crumb_bar.addItem("home", "组织管理")
        self.crumb_bar.addItem("branch", branch_label)
        self.crumb_bar.blockSignals(False)

    def _on_crumb(self, route_key: str):
        detail = route_key != "home"
        if not detail:
            self.back_to_feed()
        self._set_toolbar(detail)

    def _stat_text(self, shown_count: int) -> str:
        return (f"本地 {self._stats['local']} 人，系统 {self._stats['system']} 人，"
                f"双方 {self._stats['both']} 人，当前展示 {shown_count} 人")

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

    def refresh(self, on_ready=None):
        """支部树与统计放到后台线程：memberCache 大时逐人跑检查不在界面线程卡顿。

        on_ready 在刷新成功落地后补跑一次（例如从统计页点异常团员要跳到刚算好的
        支部详情）。刷新已在进行就只挂回调、不再起第二个线程（避免双份重活）。"""
        if on_ready is not None:
            self._refresh_callbacks.append(on_ready)
        if self._refresh_running:
            return
        self._refresh_running = True

        from app.ui.page import CallbackWorker

        self._stats_token = getattr(self, "_stats_token", 0) + 1
        token = self._stats_token
        worker = CallbackWorker(self._collect_tree, self)

        def _on_ok(data):
            if token != self._stats_token:
                return
            self._refresh_running = False
            self._apply_refresh(data)
            cbs = self._refresh_callbacks
            self._refresh_callbacks = []
            for cb in cbs:
                cb()

        def _on_fail(msg):
            self._refresh_running = False
            self._refresh_callbacks.clear()
            self._hide_busy()
            if not self._branches and not self.feed_lay.count():
                self.feed_lay.addWidget(self._empty_state("组织数据加载失败"))
            InfoBar.warning("组织树刷新失败", msg, duration=4000, parent=self)

        worker.ok.connect(_on_ok)
        worker.fail.connect(_on_fail)
        self._workers.append(worker)
        worker.start()

    def _collect_tree(self):
        """后台线程：本地名单 + 系统缓存 → 支部树与每支部统计（纯计算）。"""
        local = self._load_local_rows()
        sys_groups = self._load_system_groups()
        sp = getattr(self.window_ref, "settings_page", None)
        rule = sp.org_rule() if sp else None
        branches = build_branches(
            local, sys_groups, store.load().get("secretaries", []), rule=rule)
        stats = {b.code: self._branch_stats(b) for b in branches}
        return branches, stats

    def _apply_refresh(self, data):
        import time

        self._branches, stats = data
        self._stats_cache = stats
        self._stats = {
            "local": sum(1 for b in self._branches for m in b.members if m.source in ("local", "both")),
            "system": sum(1 for b in self._branches for m in b.members if m.source in ("system", "both")),
            "both": sum(1 for b in self._branches for m in b.members if m.source == "both"),
        }
        self._refreshed_at = time.monotonic()
        # 数据没变就不重建：30 秒同步回来一次、备注没改时也清空重建，
        # 卡片会一批批重长出来。详情态不跳过，成员数字可能变了。
        digest = self._feed_digest_of(self._branches, stats)
        if digest == getattr(self, "_feed_digest", None) and not self._detail_code:
            self._hide_busy()
            return
        self._feed_digest = digest
        self._rebuild(on_done=self._hide_busy)

    @staticmethod
    def _feed_digest_of(branches, stats) -> tuple:
        """只比影响支部列表显示的字段，避免把整份成员列表做成字符串。"""
        return tuple(
            (b.code, b.label, len(b.members),
             tuple(sorted((stats.get(b.code) or {}).get("by_kind", {}).items())))
            for b in branches
        )

    def _branch_stats(self, b: BranchNode) -> dict:
        """支部统计：从成员缓存的系统记录离线计算，无网络请求。

        异常只计错误级（职业未填等警告不算）；欠缴 = 未交总额>0 或未交月份>0。
        refresh 已在后台算过的一轮直接命中缓存，不再逐次重算。"""
        cache = getattr(self, "_stats_cache", None) or {}
        cached = cache.get(b.code)
        if cached is not None:
            return cached
        recs = [m.system for m in b.members if m.system is not None]
        by_kind = {"identity": 0, "report": 0, "archive": 0, "fees": 0}
        unpaid_amount = 0.0
        abnormal_ids: set[str] = set()
        if recs:
            results = (
                build_identity_results(recs, default_identity_template())
                + build_report_results(recs, default_report_template())
                + build_fee_results(recs)
            )
            from app.domain.org_stats import report_kind

            for r in results:
                if r.kind == "fees":
                    by_kind["fees"] += 1
                    unpaid_amount += r.amount
                    abnormal_ids.add(r.member.member_id)
                elif r.is_error:
                    by_kind[report_kind(r.member) if r.kind == "report" else r.kind] += 1
                    abnormal_ids.add(r.member.member_id)
        return {
            "total": len(b.members),
            "system": len(recs),
            "abnormal": len(abnormal_ids),
            "by_kind": by_kind,
            "unpaid_amount": unpaid_amount,
        }

    def _clear(self, layout):
        """先隐藏再排队销毁：deleteLater 要等事件循环，旧卡片会和新卡片叠在一起，
        切页时看起来就是两层文字重影、整片发糊。"""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _rebuild(self, on_done=None):
        self._feed_builder.cancel()
        self._clear(self.feed_lay)
        shown = filter_members(self._branches, self._search,
                               not self.sys_only.isChecked())
        status = self.status_combo.currentData()
        if status and status != "all":
            shown = [b for b in shown
                     if branch_status(self._branch_stats(b)) == status]
        shown_count = sum(len(b.members) for b in shown)
        self.stat_label.setText(self._stat_text(shown_count))
        if not shown:
            self.feed_lay.addWidget(self._empty_state("没有匹配的支部"))
            if on_done is not None:
                on_done()
            return
        if self._detail_code:
            b = next((n for n in self._branches if n.code == self._detail_code), None)
            if b is not None:
                self._build_detail(b, animate=False)
            else:
                self._detail_code = ""
                self._set_nav(False)
                self.feed_box.setVisible(True)
                self.detail_box.setVisible(False)
        # 卡片分批建：首批同步（首帧就有内容），其余按帧补齐；全部建完才收加载页
        self._feed_builder.start(shown,
                                 lambda b: self.feed_lay.addWidget(self._feed_card(b)),
                                 on_done=on_done)

    @staticmethod
    def _empty_state(text: str) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(8)
        ic = IconWidget(FIF.SEARCH)
        ic.setFixedSize(28, 28)
        lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
        lbl = note_label(text)
        lay.addWidget(lbl, 0, Qt.AlignmentFlag.AlignHCenter)
        box.setMinimumHeight(160)
        return box

    # ---------- 支部卡片 ----------

    def _feed_card(self, b: BranchNode) -> QWidget:
        st = self._branch_stats(b)
        # 与筛选共用一套互斥判定：只有异常支部铺底色；正常、纯欠缴、无记录
        # 都保持卡片原样。异常是「需要关注」，用 warning 黄而不是 danger 红。
        role = "warning" if branch_status(st) == "abnormal" else None

        card = BranchCard(role)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(card)
        h.setContentsMargins(24, 14, 18, 14)
        h.setSpacing(14)

        text = QVBoxLayout()
        text.setSpacing(3)
        title = theme_font(StrongBodyLabel(b.label), P.FONT["body"], int(QFont.Weight.DemiBold))
        text.addWidget(title)
        meta = b.secretary_text + (f" {b.grade}" if b.grade else "")
        text.addWidget(note_label(meta or "暂无团支书信息"))
        h.addLayout(text, 1)

        if self._search.strip():
            names = [m.name for m in b.members if m.name]
            if names:
                label = "、".join(names[:3]) + (f" 等 {len(names)} 人" if len(names) > 3 else "")
                h.addWidget(_pill(label, P.solid("fg3")), 0, Qt.AlignmentFlag.AlignVCenter)

        h.addLayout(self._metric(str(st["total"]), "团员"), 0)
        h.addLayout(self._metric(str(st["abnormal"]), "异常",
                                 "warning" if st["abnormal"] else "muted"), 0)
        h.addLayout(self._metric(str(st["by_kind"]["fees"]), "欠缴",
                                 "fees" if st["by_kind"]["fees"] else "muted"), 0)
        h.addSpacing(24)  # 给自绘箭头留出位置

        for w in card.findChildren(QWidget):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        card.clicked.connect(lambda *_: self._open_branch(b.code))
        return card

    @staticmethod
    def _metric(value: str, label: str, role: str = "brand") -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(1)
        col.setContentsMargins(12, 0, 12, 0)
        num = theme_font(BodyLabel(value), P.FONT["title"], int(QFont.Weight.DemiBold))
        c = P.fg(role)
        num.setTextColor(c, c)
        col.addWidget(num, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(note_label(label), 0, Qt.AlignmentFlag.AlignHCenter)
        return col

    # ---------- 支部详情 ----------

    def _open_branch(self, code: str, animate: bool = True):
        b = next((n for n in self._branches if n.code == code), None)
        if b is None:
            return
        self._detail_code = code
        self._build_detail(b, animate=animate)
        self._set_nav(True)
        self._set_toolbar(True)
        if animate:
            self.feed_box.setVisible(False)
            self.detail_box.setVisible(True)
            fade_in(self.detail_box, duration=240, dy=0)
        else:
            self.feed_box.setVisible(False)
            self.detail_box.setVisible(True)
        self._scroll_top()
        self.detail_changed.emit(True)

    def reveal_branch(self, name: str, on_missing=None) -> None:
        """按支部名打开详情：组织状况里点异常团员就跳到这里。

        支部名来自账号备注（与错误名单里的字段同源），找不到时退一步做包含匹配，
        备注被改过长也能对上。

        分支树可能还没算好（组织管理页第一次被切到）：不在这里同步算（那会卡住
        界面线程），交给后台 refresh，算完再补开；已算好且没过期就直接开。"""
        import time

        needle = (name or "").strip()
        if not needle:
            if on_missing:
                on_missing()
            return
        fresh = self._branches and (
            time.monotonic() - getattr(self, "_refreshed_at", 0)) <= 2
        if fresh:
            self._open_by_name(needle, on_missing)
        else:
            # 树还没建好或已过期：挂到后台刷新完成后再开，不阻塞这次点击
            self.refresh(on_ready=lambda: self._open_by_name(needle, on_missing))

    def _open_by_name(self, needle: str, on_missing=None):
        node = next((b for b in self._branches if b.label == needle), None)
        if node is None:
            node = next((b for b in self._branches
                         if needle in b.label or (b.label and b.label in needle)), None)
        if node is None:
            if on_missing:
                on_missing()
            return
        self._open_branch(node.code)

    def _build_detail(self, b: BranchNode, animate: bool = True):
        self._detail_builder.cancel()
        self._clear(self.detail_lay)
        self._set_crumb(b.label)
        st = self._branch_stats(b)
        self.stat_label.setText(
            f"{b.label}，团员 {st['total']} 人，系统在册 {st['system']} 人")

        head = QWidget()
        head_lay = QVBoxLayout(head)
        head_lay.setContentsMargins(2, 2, 2, 0)
        head_lay.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        title_row.addWidget(theme_font(StrongBodyLabel(b.label), P.FONT["metric"], int(QFont.Weight.DemiBold)))
        title_row.addStretch(1)
        # 访问支部：高亮主按钮 + 浏览器图标，进入内置浏览器打开该支部账号
        self.visit_btn = PrimaryPushButton(FIF.GLOBE, "访问支部")
        set_tip(self.visit_btn, "在内置浏览器中打开这个支部的账号页面")
        self.visit_btn.clicked.connect(lambda *_: self._visit_branch(b))
        title_row.addWidget(self.visit_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        head_lay.addLayout(title_row)
        sub = b.secretary_text + (f" {b.grade}" if b.grade else "")
        head_lay.addWidget(note_label(" ".join(x for x in (sub, b.code) if x)))
        self.detail_lay.addWidget(head)

        # 概览：三列数字 + 细分隔竖线，数值首次打开时从零滚动
        stat_card = CardWidget()
        stat_card.setBorderRadius(P.RADIUS["control"])
        sv = QVBoxLayout(stat_card)
        sv.setContentsMargins(20, 16, 20, 16)
        sv.setSpacing(12)
        nums = QHBoxLayout()
        nums.setSpacing(0)
        blocks = []
        for i, (num, label, role) in enumerate((
            (st["total"], "团员总数", "brand"),
            (st["abnormal"], "异常团员", "warning"),
            (st["by_kind"]["fees"], "欠缴团费", "fees"),
        )):
            if i:
                line = QWidget()
                line.setFixedWidth(1)
                line.setStyleSheet(
                    f"background-color: {'rgba(255,255,255,26)' if isDarkTheme() else 'rgba(0,0,0,24)'};")
                nums.addWidget(line)
            block = QVBoxLayout()
            block.setSpacing(2)
            n_lbl = theme_font(BodyLabel("0"), P.FONT["metric"], int(QFont.Weight.DemiBold))
            c = P.fg(role)
            n_lbl.setTextColor(c, c)
            block.addWidget(n_lbl, 0, Qt.AlignmentFlag.AlignHCenter)
            block.addWidget(note_label(label), 0, Qt.AlignmentFlag.AlignHCenter)
            nums.addLayout(block, 1)
            blocks.append((n_lbl, num))
        sv.addLayout(nums)

        max_n = max(st["total"], 1)
        bars = []
        for kind in ("identity", "report", "archive", "fees"):
            bar = BarRowWidget(KIND_TEXT[kind], KIND_COLOR[kind])
            bars.append((bar, st["by_kind"][kind]))
            sv.addWidget(bar)
        if st["unpaid_amount"]:
            sv.addWidget(note_label(f"未交团费总额 {st['unpaid_amount']:g} 元"))
        self.detail_lay.addWidget(stat_card)

        if animate:
            for lbl, value in blocks:
                value_animation(lbl, 0, value,
                                lambda v, l=lbl: l.setText(str(int(round(float(v))))),
                                duration=480)
            for bar, value in bars:
                bar.set_value_animated(value, max_n, duration=520)
        else:
            for lbl, value in blocks:
                lbl.setText(str(value))
            for bar, value in bars:
                bar.set_value(value, max_n)

        self.detail_lay.addWidget(section_title(f"团员（{st['total']} 人）"))
        if not b.members:
            self.detail_lay.addWidget(self._empty_state("这个支部暂时没有团员"))
            return
        list_card = CardWidget()
        list_card.setBorderRadius(P.RADIUS["control"])
        lv = QVBoxLayout(list_card)
        lv.setContentsMargins(14, 4, 14, 4)
        lv.setSpacing(0)
        self.detail_lay.addWidget(list_card)
        # 团员行分批建：人多时一次性建上百行会把切页卡成空白，这里先出前几行、其余按帧补齐
        total = len(b.members)

        def _add(item):
            i, m = item
            wrap = self._member_row(m)
            wrap.separator_row.set_separator(i < total - 1)
            lv.addWidget(wrap)

        self._detail_builder.start(list(enumerate(b.members)), _add)

    def _visit_branch(self, b: BranchNode):
        """访问支部：找到与这个支部对应的账号，交给内置浏览器打开。"""
        user = ""
        for a in self.window_ref.settings_page.accounts:
            remark = (a.get("remark") or "").strip()
            name = a.get("userName", "")
            if remark == b.label or (b.code and name.endswith(b.code)):
                user = name
                break
        if not user:
            InfoBar.warning("没有对应账号",
                            f"找不到「{b.label}」对应的团支部账号，先在系统配置添加",
                            duration=4000, parent=self)
            return
        self.window_ref.open_browser(user)

    def back_to_feed(self, *_):
        if not self._detail_code:
            return
        self._detail_builder.cancel()
        self._detail_code = ""
        self._set_nav(False)
        self._set_toolbar(False)
        self.stat_label.setText(self._stat_text(
            sum(len(b.members) for b in filter_members(
                self._branches, self._search, not self.sys_only.isChecked()))))
        self.detail_box.setVisible(False)
        self.feed_box.setVisible(True)
        fade_in(self.feed_box, duration=220, dy=0)
        self._scroll_top()
        self.detail_changed.emit(False)

    def _scroll_top(self):
        self.verticalScrollBar().setValue(0)

    # ---------- 成员行 ----------

    def _member_row(self, m: MemberView) -> QWidget:
        """整行可点展开系统信息；展开走高度与透明度过渡。"""
        row = ClickRow()
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 9, 10, 9)
        h.setSpacing(12)

        name = theme_font(BodyLabel(m.name), P.FONT["caption"], int(QFont.Weight.DemiBold))
        h.addWidget(name)
        h.addWidget(BodyLabel(m.mobile))
        if m.cls:
            h.addWidget(note_label(m.cls))
        h.addStretch(1)
        h.addWidget(_pill(SOURCE_LABEL[m.source], SOURCE_COLOR[m.source]),
                    0, Qt.AlignmentFlag.AlignVCenter)
        toggle = None
        if m.system is not None:
            toggle = TransparentToolButton()
            set_tip(toggle, "展开系统信息")
            toggle.setFixedSize(28, 28)
            # 初始就显示收起态的 chevron，否则没点过的行没有箭头、渲染上「有的有箭头有的没有」
            toggle.setIcon(chevron_icon(False))
            # 必须钉死对齐：不写对齐标志时布局会把按钮纵向拉满整行，
            # 行高被展开的详情面板撑高后，28×28 的按钮就被拉成一根竖胶囊，
            # 看着就是「折叠按钮错位」。横向同理，不能让它吃掉 stretch 的余量。
            h.addWidget(toggle, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(row)
        detail = None
        if m.system is not None:
            detail = self._detail_grid(m.system)
            detail.setVisible(False)
            v.addWidget(detail)

        state = {"open": False}

        def _toggle(*_args, **_kwargs):
            # QAbstractButton.clicked 会带一个 checked 布尔参数，行点击若走同一
            # 槽函数就会把 False 当成详情面板喂进 animate_expand，炸
            # 'bool' has no graphicsEffect。面板和箭头直接取闭包变量，多余实参一律丢弃。
            if detail is None:
                return
            # 状态机驱动而不是读 isVisible：动画进行中再点一次时，
            # 可见性还没变，按可见性判断会连续两次都走同一个方向造成错位
            state["open"] = not state["open"]
            toggle.setIcon(chevron_icon(state["open"]))
            animate_expand(detail, state["open"], duration=190)

        if toggle is not None:
            toggle.clicked.connect(_toggle)
            row.clicked.connect(_toggle)
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            for w in (name, *row.findChildren(QWidget)):
                if w is not toggle:
                    w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        wrap.separator_row = row
        return wrap

    def _detail_grid(self, rec: MemberRecord) -> QWidget:
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(24, 6, 14, 12)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(3)
        for i, (label, key) in enumerate(SYSTEM_FIELDS):
            r, c = divmod(i, 3)
            val = rec.value_of(key)
            if key in ("unpaid_total", "unpaid_months") and not val:
                val = "0"
            lbl = note_label(f"{label}  {val or '未填'}")
            lbl.setWordWrap(False)
            grid.addWidget(lbl, r, c)
        return grid_w

    # ---------- 同步 ----------

    def _sync(self):
        usable, excluded = split_accounts(self.window_ref.settings_page.accounts)
        accounts = usable
        if not accounts:
            hint = f"{len(excluded)} 个账号均被标记为异常" if excluded else "先在系统配置添加团支部账号"
            InfoBar.warning("没有可同步的账号", hint, duration=6000, parent=self)
            return
        if excluded:
            InfoBar.warning("跳过异常账号",
                f"{excluded_summary(excluded)}，本轮不参与同步；测活恢复正常后自动纳入",
                duration=6000, parent=self)
        self.sync_btn.setEnabled(False)
        self.worker = MemberSyncWorker(accounts)
        self.worker.account_done.connect(self._on_sync_one)
        self.worker.all_done.connect(self._on_sync_done)
        self._show_progress(len(accounts))
        self.worker.start()

    def _show_progress(self, count: int):
        """右下角浮动进度：同步期间不挡页面，逐账号回填。"""
        from app.ui.widgets import ProgressToast

        self._progress_done = 0
        self._progress_total = count
        self._progress = ProgressToast("正在同步系统数据", self.window(),
                                       on_close=self._request_stop)
        self._progress.set_progress(0, count)
        self._progress.show()

    def _request_stop(self):
        self._stopped = True
        if self.worker is not None:
            self.worker.stop()

    def _close_progress(self, finished: bool = True):
        toast, self._progress = self._progress, None
        if toast is None:
            return
        toast.finish() if finished else toast.close()

    def _on_sync_one(self, user, branch, count, status, message):
        self._progress_done += 1
        if self._progress is not None:
            self._progress.set_progress(self._progress_done, self._progress_total)
        if status in login_gate.SKIP_KINDS:
            InfoBar.info(f"账号 {user} 跳过刷新", message, duration=4000, parent=self)
        elif status != "ok":
            InfoBar.warning(
                f"账号 {user or '（空）'} 同步失败",
                f"{message}（其他账号不受影响）", duration=4000, parent=self)

    def _on_sync_done(self):
        self.sync_btn.setEnabled(True)
        self._close_progress(finished=not self._stopped)
        self._stopped = False
        # 整份配置写盘放到后台：收尾时界面线程只更新按钮与进度，不被 IO 压住
        from app.ui.page import CallbackWorker

        self._mark_worker = CallbackWorker(mark_synced, self)
        self._mark_worker.start()
        self.refresh()
        InfoBar.success("同步完成", "系统成员缓存已更新，组织树已刷新", duration=3000, parent=self)


def mark_synced():
    """记录最后一次刷新时间（手动/后台都算），后台定时刷新据此判断是否到期。"""
    from datetime import datetime, timezone

    store.update(
        lambda cfg: cfg.__setitem__("lastSyncAt", datetime.now(timezone.utc).isoformat()))
