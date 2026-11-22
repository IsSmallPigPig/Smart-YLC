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
from app.ui.widgets import tint as _tint
from app.ui.page import (BatchedBuild, BusyOverlay, CardRow, FluentPage,
                         PageBanner, chevron_icon, note_label, paint_tinted_card,
                         set_tip, section_title, theme_font)


SOURCE_COLOR = P.SOURCE
KIND_COLOR = P.KIND
KIND_TEXT = {"identity": "信息错误", "report": "没有报道", "archive": "没有档案", "fees": "欠缴团费"}


def _rec_from_dict(d: dict) -> MemberRecord:
    fields = {f for f in MemberRecord.__dataclass_fields__}
    return MemberRecord(**{k: v for k, v in d.items() if k in fields})


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

    def setItemText(self, routeKey: str, text: str):
        """改已有节点的文字（宽度由 BreadcrumbItem.setText 自动重算）。

        「异常团员」子页会按进入的类别把面包屑改成「欠缴团费 / 没有档案」，
        复用同一个 errors 节点，避免反复 addItem 造成重复。
        """
        item = self.itemMap.get(routeKey)
        if item is not None and item.text != text:
            item.setText(text)
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

        # 从页面模块读：测试 patch org_manage_page.SiteClient 注入假客户端，
        # 局部 import 保证拿到 patch 后的名字（模块级 import 会锁死旧引用）。
        from app.ui.org_manage_page import SiteClient

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


