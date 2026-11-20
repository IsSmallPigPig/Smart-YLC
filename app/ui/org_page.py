"""组织状况仪表盘：上传进度圆环 + 异常分类条形图 + 异常团员名单（可导出）。

异常分类：信息错误（报到身份）/ 没有报道（报到情况）/ 欠缴团费（团员缴费）。
名单只列错误级异常的团员，每行末尾是所属支部（账号备注，无备注回退账号名）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from PyQt5.QtCore import QRectF, Qt, QThread, QTimer, pyqtSignal as Signal
from PyQt5.QtGui import QColor, QFont, QPainter
from PyQt5.QtWidgets import (QGridLayout, QHBoxLayout, QLabel,
                               QVBoxLayout, QWidget)

from qfluentwidgets import (
    BodyLabel,
        CardWidget,
    FluentIcon as FIF,
    InfoBar,
    PrimaryPushButton,
    StrongBodyLabel,
    CaptionLabel,
    isDarkTheme,
    qconfig,
)

from app.domain.check_rules import (
    build_fee_results,
    build_identity_results,
    build_report_results,
)
from app.domain.org_stats import (KIND_LABEL, ErrorMemberRow, build_error_rows,
                                  duplicate_mobiles, ecard_missing, merge_totals,
                                  oldest_unpaid, summarize_members, unpaid_amount,
                                  unpaid_months_total)
from app.domain.account_health import excluded_summary, split_accounts
from app.domain.transfer import fetch_transfer_applies, is_graduated
from app.services import store
from app.services.api import SiteClient
from app.ui.charts import SegmentedDonut
from app.ui import palette as P
from app.ui.widgets import PillLabel
from app.domain.check_templates import default_identity_template, default_report_template
from app.ui.page import (BusyOverlay, CallbackWorker, FluentPage, PageBanner,
                         note_label, paint_tinted_card, set_tip,
                         section_title, theme_font)
# 面包屑与组织管理页同一套实现：节点按主题取色、按内容取宽，直接复用
from app.ui.org_manage_page import CrumbBar

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
# by_kind 里的旁路键：承载「应补缴金额」，不参与分类计数
_UNPAID = "__unpaid__"
_EXTRAS = "__extras__"
# 概览卡里小标签的语气键：黄=信息错误、红=欠缴、灰=没有报道
_RING_TONE = {"identity": "ring-identity", "fees": "ring-fees",
              "report": "ring-report", "archive": "ring-archive"}



def _money(value: float) -> str:
    """金额显示：整数不带小数，有角分才保留两位。"""
    value = round(float(value or 0), 2)
    return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}"


class _Dot(QWidget):
    """图例小圆点：颜色在 paintEvent 按主题取，切主题只重绘一次。"""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(10, 10)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(0, 0, self.width(), self.height())


class _MiniBar(QWidget):
    """6px 细进度条：只画一条轨道 + 一段填充，没有文字与交互。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ratio = 0.0
        self.setFixedHeight(6)

    def set_ratio(self, ratio: float):
        self._ratio = max(0.0, min(1.0, ratio))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        h = self.height()
        r = h / 2
        p.setBrush(QColor(255, 255, 255, 26) if isDarkTheme() else QColor(0, 0, 0, 15))
        p.drawRoundedRect(QRectF(0, 0, self.width(), h), r, r)
        if self._ratio > 0:
            w = max(self.width() * self._ratio, h)
            p.setBrush(P.fg("brand"))
            p.drawRoundedRect(QRectF(0, 0, w, h), r, r)


class TapCard(CardWidget):
    """可点卡片：整块响应左键，用来把「异常团员」做成入口。"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


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
            kinds = dict(s.by_kind)
            # 金额随 by_kind 一起带出去：不改信号签名，调用方按 _UNPAID 取值
            kinds[_UNPAID] = unpaid_amount(results)
            kinds[_EXTRAS] = {
                "dupMobile": duplicate_mobiles(members),
                "ecardMissing": ecard_missing(members),
                "unpaidMonths": unpaid_months_total(members),
                "oldestUnpaid": oldest_unpaid(members),
            }
            return user, branch, s.total, s.uploaded, s.abnormal, kinds, "ok", "", transfer, rows
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
    detail_changed = Signal(bool)   # 概览 ↔ 异常名单，主窗口返回键据此可用

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
        # 详情视图的类别筛选：None=全部异常；"fees"/"archive" 等=只看该类。
        # 概览的欠缴/没有档案卡点进来时设对应值，异常团员卡设 None。
        self._detail_kind: str | None = None
        self._transfer_applied = 0
        self._unpaid_amount = 0.0
        self._extras = {"dupMobile": 0, "ecardMissing": 0,
                        "unpaidMonths": 0, "oldestUnpaid": 0}
        self._snapshot = None  # 刷新前的展示快照（整轮失败时回滚）
        self._first_result = True
        self._ok_accounts = 0
        self._banner_on = True   # 概览有头图；进子页（异常名单）时收掉
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

        # ── 面包屑：只在「异常团员名单」详情视图出现，点「组织状况」回概览 ──
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

        # ── 概览：错落网格（不是四等分）──
        # 左：团员概览跨两行（圆环是主角，给足高度）
        # 中：异常团员跨两列（异常名单入口，横向铺开才装得下分类标签）
        # 右：欠缴团费 / 没有档案 上下叠两张
        # 底：一条紧凑指标带（上传进度、完整档案、手机号重复、最长欠缴）
        self.overview_box = QWidget()
        grid = QGridLayout(self.overview_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        self.overview_lay = grid

        self.overview_card = self._build_overview_card()
        self.abnormal_card = self._build_abnormal_card()
        self.fees_card = self._build_fees_card()
        self.archive_card = self._build_archive_card()
        self.upload_card = self._build_upload_card()
        self.extra_strip = self._build_extra_strip()
        self.rank_card = self._build_branch_rank_card()
        self.health_card = self._build_health_card()

        grid.addWidget(self.overview_card, 0, 0, 2, 1)      # 左：跨两行
        grid.addWidget(self.abnormal_card, 0, 1, 1, 2)      # 中上：跨两列
        grid.addWidget(self.fees_card, 1, 1, 1, 1)          # 中下左
        grid.addWidget(self.archive_card, 1, 2, 1, 1)       # 中下右
        grid.addWidget(self.upload_card, 0, 3, 1, 1)        # 右上
        grid.addWidget(self.extra_strip, 1, 3, 1, 1)        # 右下
        grid.addWidget(self.rank_card, 2, 0, 1, 2)          # 第三行：支部排行
        grid.addWidget(self.health_card, 2, 2, 1, 2)        # 第三行：账号状态
        # 列宽全部按比例分配：任何一列都不许 stretch=0。
        # stretch=0 的列宽由内容决定，窗口变宽时它纹丝不动、只把余量全让给邻居，
        # 看起来就是「只有中间在变，左边那张卡钉死了」。四列都给权重，
        # 窗口一变宽四张卡一起长。
        # 比例 30:32:32:22 是照内容配的：圆环卡装得下 150px 圆环 + 图例，
        # 中间两列等宽放异常卡并排的两张，右列窄一点留给两行数字。
        for col, weight in enumerate((30, 32, 32, 22)):
            grid.setColumnStretch(col, weight)
        grid.setRowStretch(2, 1)

        # 概览区整体（四张卡 + 下方转接说明）一起显隐，进名单视图时整块收起
        self.overview_group = QWidget()
        grp = QVBoxLayout(self.overview_group)
        grp.setContentsMargins(0, 0, 0, 0)
        grp.setSpacing(10)
        grp.addWidget(self.overview_box, 1)
        self.transfer_note = note_label("")
        grp.addWidget(self.transfer_note)
        lay.addWidget(self.overview_group, 1)

        # ── 异常团员名单：点异常卡片进来，竖向占满剩余高度 ──
        self.detail_box = QWidget()
        det = QVBoxLayout(self.detail_box)
        det.setContentsMargins(0, 0, 0, 0)
        det.setSpacing(10)
        # 不要用 det.setAlignment(AlignTop)：布局级对齐会让带 stretch 的子控件
        # 拿不到分配到的空间，标题被压扁、卡片被挤到底部——看起来就是「渲染错位」。
        # 让内容自然铺开，末尾留一条 stretch 把焦点压回顶部即可。
        self.detail_lay = det
        lay.addWidget(self.detail_box, 1)
        self.detail_box.setVisible(False)

        self.error_box = QWidget()
        self.error_lay = QVBoxLayout(self.error_box)
        self.error_lay.setContentsMargins(0, 0, 0, 0)
        self.error_lay.setSpacing(8)
        self.error_title = section_title("异常团员 0")
        # section_title 只管字号，不设颜色；不显式取主题色时，浅色主题下
        # 的白字画在白底上，整行标题像是消失了
        self.error_title.setTextColor(*P.text_colors("fg"))
        det.addWidget(self.error_title)
        det.addWidget(self.error_box, 1)
        det.addStretch(0)

        # 概览内容不足一屏时靠这条 stretch 顶住顶部；进名单视图要摘掉它，
        # 否则它和 detail_box 的 stretch 平分剩余高度，名单只占半屏。
        # QBoxLayout 的 spacer 不能改 stretch 值，只能整条移出 / 插回。
        lay.addStretch(1)
        self._detail_open = False
        self._set_nav(False)
        # 绑定方法而非 lambda：页面销毁后 qconfig 仍会发信号，
        # lambda 会打进悬空对象（PyQt5 里直接 abort）
        qconfig.themeChanged.connect(self._on_theme_changed)

    def _on_theme_changed(self, *_):
        """切主题：概览卡里的数字色、图例圆点、详情标题都要按新主题重画。"""
        self._refresh_overview()
        self.error_title.setTextColor(*P.text_colors("fg"))
    # ---------- 概览卡片：构建 ----------

    # 错落网格：不同角色给不同高度，避免四块一样大的死板感。
    # 圆环卡跨两行，要装下圆环+图例；中间两列一行高；右边一列更紧凑。
    CARD_MIN_H = 150      # 单行卡（右列 / 中下两卡）
    CARD_WIDE_H = 150     # 中间异常卡（单行、跨两列）
    # 跨两行的圆环卡不钉高度：由两行实际高度撑开，钉死会比邻列高出一截
    CARD_TALL_H = None

    def _card_shell(self, title: str, hint: str = "",
                    height: int | None = None,
                    clickable: bool = False) -> tuple[CardWidget, QVBoxLayout]:
        """概览卡共用外壳：标题一行 + 内容区。高度按角色给，不再全等。

        clickable=True 时整块卡可点（手型光标 + clicked 信号），用于「点卡进名单」
        这类入口；默认 False 保持静态卡行为不变。
        """
        card = TapCard() if clickable else CardWidget()
        card.setMinimumHeight(height if height is not None else self.CARD_MIN_H)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(theme_font(StrongBodyLabel(title), P.FONT["title"], int(QFont.Weight.DemiBold)))
        head.addStretch(1)
        lay.addLayout(head)
        if hint:
            note = note_label(hint)
            lay.addWidget(note)
        return card, lay

    def _add_card_arrow(self, lay: QVBoxLayout):
        """在 _card_shell 的标题行右端加「查看 ›」，提示这张卡可点进名单。

        标题行是 lay 的第一个 item，末尾已有 addStretch；箭头追加在 stretch 之后，
        因此贴着卡片右缘。与「异常团员」卡的提示保持同一套文案与配色。
        """
        head = lay.itemAt(0)
        if head is None or head.layout() is None:
            return
        arrow = CaptionLabel("查看 ›")
        arrow.setTextColor(*P.text_colors("brand"))
        head.layout().addWidget(arrow)

    def _build_overview_card(self) -> CardWidget:
        """团员概览：多段圆环，圆心是总团员数，环上按类别分色。"""
        card, lay = self._card_shell("团员概览")
        row = QHBoxLayout()
        row.setSpacing(14)
        self.ring = SegmentedDonut()
        row.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignTop)

        self.ring_legend = QVBoxLayout()
        self.ring_legend.setSpacing(7)
        self._legend_rows: dict[str, tuple[QLabel, QLabel]] = {}
        for key in ("identity", "fees", "report", "archive"):
            label = KIND_LABEL[key]
            color = P.RING[key]
            line = QHBoxLayout()
            line.setSpacing(6)
            dot = _Dot(color)
            line.addWidget(dot)
            name = CaptionLabel(label)
            name.setTextColor(*P.text_colors("fg2"))
            line.addWidget(name)
            line.addStretch(1)
            value = theme_font(StrongBodyLabel("0"), P.FONT["caption"], int(QFont.Weight.DemiBold))
            line.addWidget(value)
            self._legend_rows[key] = (dot, value)
            self.ring_legend.addLayout(line)
        self.ring_legend.addStretch(1)
        row.addLayout(self.ring_legend, 1)
        lay.addStretch(1)
        lay.addLayout(row)
        lay.addSpacing(14)

        # 环下面补一行档案完整度：圆环只说异常分布，这里说「底子齐不齐」
        self.completeness = QVBoxLayout()
        self.completeness.setSpacing(7)
        self._complete_rows: dict[str, StrongBodyLabel] = {}
        for key, label in (("uploaded", "档案已上传"),
                           ("ecard", "团员证照片齐全"),
                           ("mobile", "手机号可用")):
            line = QHBoxLayout()
            line.setSpacing(6)
            name = note_label(label)
            name.setWordWrap(False)
            line.addWidget(name, 1)
            value = theme_font(StrongBodyLabel("—"), P.FONT["caption"],
                               int(QFont.Weight.DemiBold))
            line.addWidget(value, 0, Qt.AlignmentFlag.AlignRight)
            self._complete_rows[key] = value
            self.completeness.addLayout(line)
        lay.addLayout(self.completeness)
        lay.addStretch(1)
        return card

    def _build_abnormal_card(self) -> CardWidget:
        """异常团员：点进去看名单（竖向可以更长）。"""
        card = TapCard()
        card.clicked.connect(self._open_detail)
        card.setMinimumHeight(self.CARD_WIDE_H)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(theme_font(StrongBodyLabel("异常团员"), P.FONT["title"], int(QFont.Weight.DemiBold)))
        head.addStretch(1)
        arrow = CaptionLabel("查看 ›")
        arrow.setTextColor(*P.text_colors("brand"))
        head.addWidget(arrow)
        lay.addLayout(head)

        self.abnormal_value = theme_font(StrongBodyLabel("0"), P.FONT["metric"], int(QFont.Weight.Bold))
        lay.addWidget(self.abnormal_value)
        self.abnormal_sub = note_label("存在异常项，点卡片查看名单")
        lay.addWidget(self.abnormal_sub)

        # 三枚小标签横向排开：信息错误 / 欠缴团费 / 没有报道
        self.abnormal_chips = QHBoxLayout()
        self.abnormal_chips.setSpacing(6)
        self._abnormal_chip_labels: dict[str, PillLabel] = {}
        for key in ("identity", "fees", "report"):
            chip = PillLabel(f"{KIND_LABEL[key]} 0", _RING_TONE[key])
            self._abnormal_chip_labels[key] = chip
            self.abnormal_chips.addWidget(chip)
        self.abnormal_chips.addStretch(1)
        lay.addSpacing(2)
        lay.addLayout(self.abnormal_chips)
        lay.addStretch(1)
        return card

    def _build_fees_card(self) -> CardWidget:
        """欠缴团费：展示总计要补缴的金额；点整张卡看欠缴名单。"""
        card, lay = self._card_shell("欠缴团费", clickable=True)
        card.clicked.connect(lambda: self._open_detail(kind="fees"))
        # 标题行右端加箭头提示可点（_card_shell 的 head 是 lay 里第一个 QHBoxLayout）
        self._add_card_arrow(lay)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.fees_amount = theme_font(StrongBodyLabel("0"), P.FONT["metric"], int(QFont.Weight.Bold))
        self.fees_amount.setTextColor(*P.text_colors("danger"))
        row.addWidget(self.fees_amount)
        unit = CaptionLabel("元")
        unit.setTextColor(*P.text_colors("fg2"))
        row.addWidget(unit, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        lay.addLayout(row)
        self.fees_sub = note_label("0 人欠缴")
        lay.addWidget(self.fees_sub)
        return card

    def _build_archive_card(self) -> CardWidget:
        """没有档案：只缺电子志愿书档案的人数；点整张卡看缺档名单。"""
        card, lay = self._card_shell("没有档案", clickable=True)
        card.clicked.connect(lambda: self._open_detail(kind="archive"))
        self._add_card_arrow(lay)
        self.archive_value = theme_font(StrongBodyLabel("0"), P.FONT["metric"], int(QFont.Weight.Bold))
        lay.addWidget(self.archive_value)
        self.archive_sub = note_label("0 人缺少电子志愿书档案")
        lay.addWidget(self.archive_sub)
        return card

    def _build_branch_rank_card(self) -> CardWidget:
        """支部异常排行：哪几个支部问题最多，直接指向要催的班。

        异常名单是按人排的，管理员真正先问的是「哪个班最麻烦」。
        这里按异常人数降序列出前几名，点一行就进对应支部。
        """
        card, lay = self._card_shell("支部异常排行")
        self._rank_rows = QVBoxLayout()
        self._rank_rows.setSpacing(8)
        lay.addLayout(self._rank_rows)
        self._rank_empty = note_label("还没有支部数据")
        lay.addWidget(self._rank_empty)
        self._rank_widgets: list[QWidget] = []
        return card

    def _build_health_card(self) -> CardWidget:
        """账号在线状态：几个能取数、几个要重新登录，决定统计能不能跑全。"""
        card, lay = self._card_shell("账号状态")
        self._health_text = theme_font(StrongBodyLabel("—"), P.FONT["metric"],
                                       int(QFont.Weight.Bold))
        lay.addWidget(self._health_text)
        self._health_sub = note_label("可以正常取数")
        lay.addWidget(self._health_sub)
        return card

    def _build_upload_card(self) -> CardWidget:
        """上传进度：已上传档案 / 应上传总数，给一个能直接看进度的百分比。"""
        card, lay = self._card_shell("上传进度")
        self.upload_value = theme_font(StrongBodyLabel("0%"), P.FONT["metric"],
                                       int(QFont.Weight.Bold))
        lay.addWidget(self.upload_value)
        self.upload_sub = note_label("0 / 0 人已上传")
        lay.addWidget(self.upload_sub)

        # 细进度条：一眼看出还差多少，不占地方
        self.upload_bar = _MiniBar()
        lay.addWidget(self.upload_bar)
        return card

    def _build_extra_strip(self) -> CardWidget:
        """紧凑指标带：四项与档案/数据质量相关的小数字，竖排贴着右列。

        这些是「顺手能修」的活儿（补照片、并重复号、催最长欠缴），
        单独成卡会占掉一整块版面，做成一条紧凑列表更合适。
        """
        card = CardWidget()
        card.setMinimumHeight(self.CARD_MIN_H)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(9)
        lay.addWidget(theme_font(StrongBodyLabel("待办数据"), P.FONT["title"],
                                 int(QFont.Weight.DemiBold)))

        self._strip: dict[str, StrongBodyLabel] = {}
        for key, label in (("ecardMissing", "电子团员证未采集"),
                           ("dupMobile", "手机号重复"),
                           ("unpaidMonths", "欠缴月数合计"),
                           ("oldestUnpaid", "最长欠缴月数")):
            row = QHBoxLayout()
            row.setSpacing(6)
            name = note_label(label)
            name.setWordWrap(False)
            row.addWidget(name, 1)
            value = theme_font(StrongBodyLabel("0"), P.FONT["body"],
                               int(QFont.Weight.DemiBold))
            row.addWidget(value, 0, Qt.AlignmentFlag.AlignRight)
            self._strip[key] = value
            lay.addLayout(row)
        return card

    # ---------- 概览卡片：刷新 ----------

    def _set_ratio(self, total: int, uploaded: int):
        """兼容旧调用点：概览卡不再用「已上传」圆环，只记住数字。"""
        self._uploaded = uploaded

    def _refresh_overview(self):
        """把当前数字铺到全部概览卡上。

        数字只来自 _total/_uploaded/_by_kind/_error_rows/_unpaid_amount/_extras，
        不在这里发请求、不重算——刷新路径与离线重算各算一次，避免两处走样。"""
        local_total = self._local_roster_total()
        total, missing = merge_totals(self._total, local_total)

        # 环上顺序与图例一致：黄（信息错误）→ 红（欠缴）→ 灰（没有报道/档案）
        segments = [
            (KIND_LABEL[key], self._by_kind.get(key, 0), P.RING[key])
            for key in ("identity", "fees", "report", "archive")
        ]
        self.ring.set_data(total, segments, missing=missing,
                           caption="总团员" if total else "暂无数据")
        for key, (_dot, value) in self._legend_rows.items():
            value.setText(str(self._by_kind.get(key, 0)))

        abnormal = len(self._error_rows)
        self.abnormal_value.setText(str(abnormal))
        self.abnormal_sub.setText(
            "存在异常项，点卡片查看名单" if abnormal else "没有发现异常项")
        for key, chip in self._abnormal_chip_labels.items():
            chip.set_text(f"{KIND_LABEL[key]} {self._by_kind.get(key, 0)}")

        amount = self._unpaid_amount
        self.fees_amount.setText(_money(amount))
        unpaid_count = self._by_kind.get("fees", 0)
        self.fees_sub.setText(
            f"{unpaid_count} 人欠缴" if unpaid_count else "没有欠缴团费")

        archive = self._by_kind.get("archive", 0)
        self.archive_value.setText(str(archive))
        self.archive_sub.setText(
            f"{archive} 人缺少电子志愿书档案" if archive else "档案齐全")

        # 上传进度：分母用圆环那套总人数（本地/线上取大），分子是实际上传数
        uploaded = min(self._uploaded, total) if total else 0
        ratio = uploaded / total if total else 0.0
        self.upload_value.setText(f"{ratio * 100:.0f}%" if total else "—")
        self.upload_sub.setText(f"{uploaded} / {total} 人已上传" if total else "暂无数据")
        self.upload_bar.set_ratio(ratio)

        for key, value in self._strip.items():
            value.setText(str(int(self._extras.get(key, 0) or 0)))

        # 档案完整度：与圆环同一分母（总人数），三项各报「齐 / 总」
        ecard_missing = int(self._extras.get("ecardMissing", 0) or 0)
        dup = int(self._extras.get("dupMobile", 0) or 0)
        self._complete_rows["uploaded"].setText(f"{uploaded} / {total}" if total else "—")
        self._complete_rows["ecard"].setText(
            f"{max(0, total - ecard_missing)} / {total}" if total else "—")
        self._complete_rows["mobile"].setText(
            f"{max(0, total - dup)} / {total}" if total else "—")

        self._refresh_rank()
        self._refresh_health()

    def _branch_rows(self) -> list[tuple[str, int, int]]:
        """从 orgStatsCache 取 (支部名, 异常数, 团员数)，按异常数降序。

        异常数用该支部 error_rows 的条数——它就是名单里那批人，
        与「异常团员」卡的数字同源。"""
        cache = store.load().get("orgStatsCache") or {}
        rows = []
        for entry in cache.values():
            name = str(entry.get("branch") or "").strip()
            if not name:
                continue
            rows.append((name, len(entry.get("error_rows") or []),
                         int(entry.get("total") or 0)))
        rows.sort(key=lambda r: (-r[1], r[0]))
        return rows

    def _refresh_rank(self):
        """支部异常排行：最多列 5 行，异常为 0 的不占地方。"""
        for w in getattr(self, "_rank_widgets", []):
            w.deleteLater()
        self._rank_widgets = []

        rows = [r for r in self._branch_rows() if r[1] > 0][:5]
        self._rank_empty.setVisible(not rows)
        for name, abnormal, total in rows:
            line = QWidget()
            h = QHBoxLayout(line)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            label = note_label(name)
            label.setWordWrap(False)
            h.addWidget(label, 1)
            # 右侧「异常 5 / 43」：分母是该支部人数，方便判断严重程度
            stat = theme_font(StrongBodyLabel(f"{abnormal} / {total} 人"),
                              P.FONT["caption"], int(QFont.Weight.DemiBold))
            h.addWidget(stat, 0, Qt.AlignmentFlag.AlignRight)
            self._rank_rows.addWidget(line)
            self._rank_widgets.append(line)

    def _refresh_health(self):
        """账号状态：正常可统计 / 需处理（密码错、验证码、冷却等）。"""
        accounts = list(getattr(self.window_ref.settings_page, "accounts", []) or [])
        usable, bad = split_accounts(accounts)
        if not accounts:
            self._health_text.setText("—")
            self._health_sub.setText("还没有配置支部账号")
            return
        # 一行读完：「6 / 6 正常」，有事才把待处理数点出来
        self._health_text.setText(f"{len(usable)} / {len(accounts)}")
        self._health_sub.setText(
            "全部可用" if not bad else f"{len(bad)} 个需重新登录，本轮统计会跳过")

    def _local_roster_total(self) -> int:
        """本地名单（系统配置里导入的 xlsx）人数；没导入时为 0。"""
        settings = getattr(self.window_ref, "settings_page", None)
        roster = getattr(settings, "roster", None) if settings is not None else None
        rows = getattr(roster, "rows", None) or []
        return len(rows)

    # ---------- 视图切换（概览 ↔ 异常名单） ----------

    def _open_detail(self, *_args, kind: str | None = None):
        """切到名单子视图。kind 指定只看某一类（fees/archive/...），None=全部异常。

        概览有三处入口：「异常团员」卡（kind=None）、「欠缴团费」卡（fees）、
        「没有档案」卡（archive）。都进同一个名单视图，只是标题与可见行不同。
        名单是子页，不带头图：banner 是页面级的东西，进到子页还挂着主导航的图，
        视觉上会以为还在概览层。同时把顶部边距从 banner 高度收回 16。
        """
        from app.ui.anim import switch_with_fade

        self._detail_kind = kind
        self._add_crumb(kind)
        self._detail_open = True
        self._set_nav(True)
        self._set_banner(False)
        self.detail_changed.emit(True)
        self.detail_box.setVisible(True)
        self._apply_detail_filter()
        # 两个视图在同一位置、同一尺寸，直接换会硬切；淡出→淡入让点击有回应
        switch_with_fade(self.overview_group, self.detail_box)

    def _apply_detail_filter(self):
        """按 _detail_kind 决定名单里哪些行可见，并同步标题。

        名单卡片在数据刷新时全量铺好，这里只切可见性：不用重建，筛选即时生效。
        卡片携带的 _kinds 是类别 key 列表（欠缴=fees、没有档案=archive）。
        """
        kind = self._detail_kind

        def in_kind(row) -> bool:
            # 行的 kinds 是中文标签串（如「欠缴团费、没有档案」），与卡片上
            # 携带的类别 key 两套表示；这里按标签判断，口径与圆环计数一致。
            return kind is None or (
                str(getattr(row, "kinds", "")) and
                KIND_LABEL.get(kind, "") in getattr(row, "kinds", ""))

        # 标题按数据源计数，不依赖卡片是否已铺（离线恢复/测试可能还没铺卡）
        shown = sum(1 for r in self._error_rows if in_kind(r))
        for i in range(self.error_lay.count()):
            item = self.error_lay.itemAt(i)
            card = item.widget() if item else None
            if card is None:
                continue
            card_kinds = getattr(card, "_kinds", [])
            visible = kind is None or kind in card_kinds
            card.setVisible(visible)
        label = KIND_LABEL.get(kind, "异常团员") if kind else "异常团员"
        self.error_title.setText(f"{label} {shown}")

    def back_to_overview(self, *_):
        """返回概览：面包屑与主窗口返回键共用。"""
        if not self._detail_open:
            return False
        from app.ui.anim import switch_with_fade

        self._detail_open = False
        self._detail_kind = None
        self._set_nav(False)
        self._set_banner(True)
        self.detail_changed.emit(False)
        # 从名单回概览是「返回」，用同样时长的反向过渡，方向感一致
        switch_with_fade(self.detail_box, self.overview_group)
        return True

    def _set_banner(self, on: bool):
        """子页收起头图，并把内容区顶部边距一起改掉。

        边距写死在布局上（banner 高度 + 16）：只藏 banner 不改边距，子页顶上
        会留一条 196px 的空白，看起来像内容整体被往下推、渲染错位。
        """
        self._banner_on = on
        if getattr(self, "header", None) is not None:
            self.header.setVisible(on)
        lay = self.vBoxLayout
        m = lay.contentsMargins()
        top = PageBanner.HEIGHT + 16 if on else 16
        if m.top() != top:
            lay.setContentsMargins(m.left(), top, m.right(), m.bottom())
        if on:
            self._place_banner()
        self._place_busy()
        self._set_bottom_stretch(on)

    def _set_bottom_stretch(self, on: bool):
        """页面末尾那条「顶住顶部」的 stretch：概览要，名单不要。

        QBoxLayout 的 spacer 改不了 stretch 值，只能把它取出来再插回去。
        名单视图里它和 detail_box 都是 stretch=1，会把剩余高度对半分，
        名单就只占了半屏、下半屏一片空白。
        """
        lay = self.vBoxLayout
        last = lay.count() - 1
        has = lay.itemAt(last) is not None and lay.itemAt(last).spacerItem() is not None
        if on and not has:
            lay.addStretch(1)
        elif not on and has:
            lay.takeAt(last)

    def _add_crumb(self, kind: str | None = None):
        if "org" not in self.crumb_bar.itemMap:
            self.crumb_bar.addItem("org", "组织状况")
        label = KIND_LABEL.get(kind, "异常团员") if kind else "异常团员"
        if "errors" in self.crumb_bar.itemMap:
            self.crumb_bar.setItemText("errors", label)
            self.crumb_bar.setCurrentItem("errors")
        else:
            self.crumb_bar.addItem("errors", label)

    def _on_crumb(self, route_key: str):
        if route_key == "org":
            self.back_to_overview()

    def in_detail(self) -> bool:
        return bool(self._detail_open)

    def _set_nav(self, detail: bool):
        self.crumb_host.setVisible(detail)

    def _update_bars(self):
        """数字变了就重铺四张概览卡（原条形图已由概览卡取代）。"""
        self._refresh_overview()

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
        top = PageBanner.HEIGHT if getattr(self, "_banner_on", True) else 0
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
        # 打开页面时圆环从零长到当前值（与支部详情一致的入场节奏）
        self._refresh_overview()
        from app.ui.anim import value_animation

        self.ring.set_progress(0.0)
        value_animation(self.ring, 0.0, 1.0,
                        lambda v: self.ring.set_progress(float(v)), duration=520)

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
        # 用 dict 而不是越拼越长的元组：字段一多，元组解包漏一个就错位
        self._snapshot = {
            "total": self._total, "uploaded": self._uploaded,
            "by_kind": dict(self._by_kind),
            "transfer": self._transfer_applied,
            "note": self.transfer_note.text(),
            "rows": list(self._error_rows),
            "unpaid": self._unpaid_amount,
            "extras": dict(self._extras),
        }
        self._total = self._uploaded = 0
        self._by_kind = {"identity": 0, "report": 0, "archive": 0, "fees": 0}
        self._transfer_applied = 0
        self._unpaid_amount = 0.0
        self._extras = {"dupMobile": 0, "ecardMissing": 0,
                        "unpaidMonths": 0, "oldestUnpaid": 0}
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
            snap = self._snapshot
            self._total = snap["total"]
            self._uploaded = snap["uploaded"]
            self._by_kind = snap["by_kind"]
            self._transfer_applied = snap["transfer"]
            self._error_rows = snap["rows"]
            self._unpaid_amount = snap["unpaid"]
            self._extras = dict(snap.get("extras") or {})
            note, rows = snap["note"], snap["rows"]
            self.transfer_note.setText(note)
            self._set_ratio(self._total, self._uploaded)
            self._update_bars()
            self._clear_cards()
            for r in rows:
                self._append_card(r.name, r.mobile, [k for k in KIND_COLOR if KIND_LABEL[k] in r.kinds],
                                  r.problems, r.branch)
            self.error_title.setText(f"异常团员 {len(rows)}")
            if self._detail_open:
                self._apply_detail_filter()  # 统计刷新时保持当前类别筛选
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
                "extras": {
                    "dupMobile": duplicate_mobiles(members),
                    "ecardMissing": ecard_missing(members),
                    "unpaidMonths": unpaid_months_total(members),
                    "oldestUnpaid": oldest_unpaid(members),
                },
                "unpaidAmount": unpaid_amount(results),
                "updatedAt": datetime.now().isoformat(timespec="seconds"),
            }
            # 逐个支部照样落盘（transfer 传 None = 保留上次在线结果）
            self._persist_account(user, branch, s.total, s.uploaded,
                                  dict(s.by_kind), None, rows,
                                  unpaid=unpaid_amount(results),
                                  extras=stats[user]["extras"])

        agg = self._aggregate(stats)
        return (agg["total"], agg["uploaded"], agg["by_kind"], agg["rows"],
                agg["unpaid_amount"], agg["extras"])

    def _apply_offline(self, data):
        total, uploaded, by_kind, error_rows, unpaid, extras = data
        self._total, self._uploaded = total, uploaded
        self._by_kind = by_kind
        self._error_rows = error_rows
        self._unpaid_amount = unpaid
        self._extras = dict(extras or {})
        self._set_ratio(total, uploaded)
        self._update_bars()
        self._clear_cards()
        for r in error_rows:
            self._append_card(r.name, r.mobile, [k for k in KIND_COLOR if KIND_LABEL[k] in r.kinds],
                              r.problems, r.branch)
        self.error_title.setText(f"异常团员 {len(error_rows)}")
        if self._detail_open:
            self._apply_detail_filter()  # 统计刷新时保持当前类别筛选

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
            if k == _UNPAID:
                self._unpaid_amount += float(v or 0)
            elif k == _EXTRAS:
                for ek, ev in (v or {}).items():
                    # 最长欠缴取最大值，其余累加
                    if ek == "oldestUnpaid":
                        self._extras[ek] = max(int(self._extras.get(ek, 0)), int(ev or 0))
                    else:
                        self._extras[ek] = int(self._extras.get(ek, 0)) + int(ev or 0)
            elif k in self._by_kind:
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
                              f"{STATUS_LABEL.get(status, status)}：{message}", branch)
        for r in rows:
            self._error_rows.append(r)
            self._append_card(r.name, r.mobile, [k for k in KIND_COLOR if KIND_LABEL[k] in r.kinds],
                              r.problems, r.branch)
        self.error_title.setText(f"异常团员 {len(self._error_rows)}")
        if self._detail_open:
            self._apply_detail_filter()  # 统计刷新时保持当前类别筛选
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
        amount = 0.0
        names: list[str] = []
        updated = ""
        # 辅助指标：手机号重复、电子团员证未采集、欠缴月数、最长欠缴
        extras = {"dupMobile": 0, "ecardMissing": 0, "unpaidMonths": 0,
                  "oldestUnpaid": 0}
        for entry in (cache or {}).values():
            total += int(entry.get("total") or 0)
            uploaded += int(entry.get("uploaded") or 0)
            for k, v in (entry.get("by_kind") or {}).items():
                if k in by_kind:
                    by_kind[k] += int(v or 0)
            rows.extend(ErrorMemberRow(**r) for r in entry.get("error_rows") or [])
            amount += float(entry.get("unpaidAmount") or 0)
            extra = entry.get("extras") or {}
            for key in extras:
                extras[key] += int(extra.get(key) or 0)
            t = entry.get("transfer") or {}
            applied += int(t.get("applied") or 0)
            succeeded += int(t.get("succeeded") or 0)
            pending += int(t.get("pending") or 0)
            returned += int(t.get("returned") or 0)
            names.extend(t.get("names") or [])
            updated = max(updated, str(entry.get("updatedAt") or ""))
        return {"total": total, "uploaded": uploaded, "by_kind": by_kind, "rows": rows,
                "applied": applied, "succeeded": succeeded, "pending": pending,
                "returned": returned, "names": names, "updated": updated,
                "unpaid_amount": round(amount, 2), "extras": extras}

    def _persist_account(self, user, branch, total, uploaded, by_kind, transfer, rows,
                         unpaid: float = 0.0, extras: dict | None = None):
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
                # 金额单独存，别混进 by_kind 的四个分类里。
                # 在线路径从旁路键取，离线路径由调用方显式传入。
                "unpaidAmount": round(float(unpaid or by_kind.get(_UNPAID, 0) or 0), 2),
                "extras": dict(extras or {}),
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
            # 有成员缓存却没有统计缓存（首次启动、导入配置、换机恢复备份）：
            # 页面会一直停在 0，直到 30 秒后的成员同步才重算。这里直接离线算一次，
            # 不联网、不打扰，打开就有数。
            if (store.load().get("memberCache") or {}):
                self.refresh_offline()
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
                              r.problems, r.branch)
        self.error_title.setText(f"异常团员 {len(agg['rows'])}")
        if self._detail_open:
            self._apply_detail_filter()  # 统计刷新时保持当前类别筛选
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

    def _append_card(self, name, mobile, kinds: list, problem, branch):
        """一张异常团员卡片：左侧姓名+手机号，右侧支部；下方类别与问题。"""
        # 记下类别 key，供详情按类别筛选显示；账号级失败卡 kinds=[] 归到「全部异常」
        card_kinds = list(kinds) if kinds else []
        # kinds 为 None 是账号级失败，没有可跳转的支部，保持普通卡片
        if kinds is None or not branch:
            card = TintedCard("danger" if kinds is None else "warning")
        else:
            card = MemberCard()
            set_tip(card, f"「{branch}」的团员名单")
            card.clicked.connect(lambda b=branch: self._goto_branch(b))
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 9, 18, 9)
        v.setSpacing(5)

        head = QHBoxLayout()
        title = theme_font(StrongBodyLabel(name or ""), P.FONT["body"], int(QFont.Weight.DemiBold))
        head.addWidget(title)
        if mobile and mobile != "—":
            head.addSpacing(8)
            head.addWidget(CaptionLabel(mobile))
        head.addStretch(1)
        if kinds:
            for k in kinds:
                # 与本页圆环同一套配色：信息错误=黄、欠缴=红、没有报道/档案=灰。
                # 名单里的类别标签若还用全局分类色，同一页出现两套读法。
                chip = PillLabel(KIND_LABEL[k], _RING_TONE.get(k, k))
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

        card._kinds = card_kinds      # 详情筛选按此判断，见 _apply_detail_filter
        self.error_lay.addWidget(card)

    def _goto_branch(self, branch: str):
        """跳到组织管理，并打开这个支部的详情。

        先切页（立刻出画面），再把「按支部名打开详情」推迟到下一轮事件循环——
        组织管理页首显会异步算分支树，这一步不阻塞点击，树算好后自动补开。
        找不到支部时由 reveal_branch 的 on_missing 兜底提示。"""
        from PyQt5.QtCore import QTimer

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
