"""组织状况概览：四张卡的取数口径、圆环分段、异常名单详情视图。

四张卡分别是团员概览（多段圆环）、异常团员（点进去看名单）、欠缴团费（应补缴
总额）、没有档案。数字口径错了不会崩，只会静静地显示一个错数，所以逐条钉住。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication, QWidget

from app.domain.check_rules import CheckResult, MemberRecord
from app.domain.org_stats import merge_totals, unpaid_amount


@pytest.fixture()
def app():
    yield QApplication.instance() or QApplication([])


def _fee(amount: float, months: int = 1, abnormal: bool = True) -> CheckResult:
    return CheckResult("fees", MemberRecord(name="x", mobile="1"), abnormal,
                       [], amount, months, "" if not abnormal else "欠缴")


def test_unpaid_amount_sums_only_unpaid():
    """应补缴总额 = 欠缴金额之和；不欠缴的不计入。"""
    assert unpaid_amount([_fee(12.5), _fee(3.0), _fee(0, abnormal=False)]) == 15.5
    assert unpaid_amount([]) == 0


@pytest.mark.parametrize("system,local,total,missing", [
    (260, 0, 260, 0),      # 没导入本地名单：全按线上算，没有缺口
    (260, 260, 260, 0),    # 两边一样
    (255, 260, 260, 5),    # 本地多 5 人：线上这 5 人还没报到
    (270, 260, 270, 0),    # 线上多 10 人：以线上为准，缺口记在本地漏登
])
def test_merge_totals_takes_larger_and_reports_missing(system, local, total, missing):
    """圆环基数取本地/线上较大者；线上少于较大者时差额算「没有报道」。"""
    assert merge_totals(system, local) == (total, missing)


class _Win(QWidget):
    def __init__(self, accounts=(), roster=None):
        super().__init__()
        class _S:
            pass
        self.settings_page = _S()
        self.settings_page.accounts = list(accounts)
        self.settings_page.roster = roster


def _page(win):
    from app.ui.org_page import OrgPage

    page = OrgPage(win)
    page.resize(1100, 700)
    page.show()
    QApplication.processEvents()
    return page


def _feed(page, *, total, by_kind, rows=(), unpaid=0.0):
    page._total = total
    page._by_kind = dict(by_kind)
    page._error_rows = list(rows)
    page._unpaid_amount = unpaid
    page._refresh_overview()


def test_four_cards_show_the_numbers(app):
    """四张卡各显示各的数：圆环总数、异常人数、应补缴金额、没有档案人数。"""
    page = _page(_Win())
    _feed(page, total=260,
          by_kind={"identity": 4, "report": 25, "archive": 3, "fees": 9},
          rows=[object()] * 38, unpaid=157.5)

    assert page.ring._total == 260
    assert page.abnormal_value.text() == "38"
    assert page.fees_amount.text() == "157.50"
    assert "9 人欠缴" in page.fees_sub.text()
    assert page.archive_value.text() == "3"
    assert "3 人" in page.archive_sub.text()


def test_ring_segments_follow_kind_counts(app):
    """环上四段按类别计数，顺序与图例一致；颜色用黄/红/灰那套。"""
    page = _page(_Win())
    _feed(page, total=100,
          by_kind={"identity": 7, "report": 11, "archive": 2, "fees": 5},
          rows=[])

    segs = page.ring.segments_snapshot()
    assert [name for name, _ in segs] == ["信息错误", "欠缴团费", "没有报道", "没有档案"]
    assert [value for _, value in segs] == [7, 5, 11, 2]


def test_local_roster_larger_than_online_adds_missing(app):
    """本地名单比线上多时，圆环基数用本地，并把差额显示为「没有报道」。"""
    class _Roster:
        rows = [1] * 270

    page = _page(_Win(roster=_Roster()))
    _feed(page, total=260, by_kind={"identity": 0, "report": 0,
                                    "archive": 0, "fees": 0}, rows=[])
    assert page.ring._total == 270
    assert page.ring._missing == 10


def test_abnormal_card_opens_detail_with_breadcrumb(app):
    """点异常卡进名单视图：面包屑出现、返回键可用、退回来复原。"""
    page = _page(_Win())
    _feed(page, total=10, by_kind={"identity": 1, "report": 0,
                                   "archive": 0, "fees": 0},
          rows=[type("R", (), {"name": "张三", "mobile": "1", "kinds": "信息错误",
                               "problems": "收入不符", "branch": "1班"})()])

    assert not page.in_detail()
    assert page.detail_box.isHidden()
    assert page.overview_box.isVisible() or page.overview_box is not None

    page._open_detail()
    assert page.in_detail()
    assert page.crumb_host.isVisible()
    assert not page.detail_box.isHidden()
    # itemMap 的键是路由键；节点文本在节点自己身上
    assert page.crumb_bar.itemMap["org"].text == "组织状况"
    assert page.crumb_bar.itemMap["errors"].text == "异常团员"
    assert page.error_title.text() == "异常团员 1"

    assert page.back_to_overview() is True
    assert not page.in_detail()
    assert page.crumb_host.isHidden()
    assert page.back_to_overview() is False   # 已在概览，不重复触发


# ---------- 辅助指标（待办数据 / 档案完整度） ----------

def test_ecard_missing_counts_blank_photo():
    """电子团员证未采集：状态里没有「已采集」的都算。"""
    from app.domain.check_rules import MemberRecord
    from app.domain.org_stats import ecard_missing

    ms = [MemberRecord(name="a", ecard_photo_status="已采集"),
          MemberRecord(name="b", ecard_photo_status=""),
          MemberRecord(name="c", ecard_photo_status=None)]
    assert ecard_missing(ms) == 2


def test_duplicate_mobiles_counts_extra_people():
    """手机号重复：同一号码出现 N 次，多出来的 N-1 人算重复；空号不算。"""
    from app.domain.check_rules import MemberRecord
    from app.domain.org_stats import duplicate_mobiles

    ms = [MemberRecord(name="a", mobile="13800000000"),
          MemberRecord(name="b", mobile="13800000000"),
          MemberRecord(name="c", mobile="13800000000"),
          MemberRecord(name="d", mobile="13900000000"),
          MemberRecord(name="e", mobile="")]
    assert duplicate_mobiles(ms) == 2


def test_unpaid_months_and_oldest():
    """欠缴月数合计与最长欠缴：一个求和、一个取最大。"""
    from app.domain.check_rules import MemberRecord
    from app.domain.org_stats import oldest_unpaid, unpaid_months_total

    ms = [MemberRecord(name="a", unpaid_months=18),
          MemberRecord(name="b", unpaid_months=2),
          MemberRecord(name="c", unpaid_months=0)]
    assert unpaid_months_total(ms) == 20
    assert oldest_unpaid(ms) == 18


def test_overview_has_all_cards_after_refresh(app):
    """八张卡刷新后都要有值，不能有一张永远显示 0/—。"""
    page = _page(_Win())
    _feed(page, total=260,
          by_kind={"identity": 5, "report": 16, "archive": 6, "fees": 10},
          rows=[object()] * 37, unpaid=104.5)
    page._uploaded = 245
    page._extras = {"dupMobile": 1, "ecardMissing": 9,
                    "unpaidMonths": 71, "oldestUnpaid": 54}
    page._refresh_overview()

    assert page.upload_value.text() == "94%"
    assert "245 / 260" in page.upload_sub.text()
    assert page._strip["ecardMissing"].text() == "9"
    assert page._strip["unpaidMonths"].text() == "71"
    # 档案完整度三项都是「齐 / 总」
    assert page._complete_rows["uploaded"].text() == "245 / 260"
    assert page._complete_rows["ecard"].text() == "251 / 260"
    assert page._complete_rows["mobile"].text() == "259 / 260"


def test_grid_is_staggered_not_equal(app):
    """错落网格：圆环卡跨两行、异常卡跨两列，不是四等分。

    只比数量不够——四张卡等宽等高时也满足「有 4 张卡」，这里直接查网格跨度。
    """
    page = _page(_Win())
    grid = page.overview_lay
    def span(w):
        idx = grid.indexOf(w)
        return grid.getItemPosition(idx)[2:]   # (rowSpan, colSpan)
    assert span(page.overview_card) == (2, 1), "圆环卡应跨两行"
    assert span(page.abnormal_card) == (1, 2), "异常卡应跨两列"
