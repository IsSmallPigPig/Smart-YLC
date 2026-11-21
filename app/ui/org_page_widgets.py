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
from app.domain.org_stats import (KIND_LABEL, ErrorMemberRow, aggregate_org_stats, build_error_rows,
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
