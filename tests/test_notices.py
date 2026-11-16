"""通知中心与公告面板：去重语义、更新不重复触发、面板行复用。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from types import SimpleNamespace

from app.services.notices import Notice, NoticeCenter


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def test_upsert_dedupes_and_signals_only_new(app):
    center = NoticeCenter()
    fresh: list[str] = []
    changed: list[int] = []
    center.new_issue.connect(lambda n: fresh.append(n.key))
    center.changed.connect(lambda: changed.append(1))

    notice = Notice(key="account:a", title="账号 a：密码错误")
    assert center.upsert(notice) is True
    assert center.upsert(Notice(key="account:a", title="账号 a：密码错误")) is False
    assert fresh == ["account:a"]        # 同一问题只报一次
    assert len(changed) == 1             # 内容没变不触发重绘
    assert center.issue_count() == 1


def test_update_is_not_a_new_issue(app):
    center = NoticeCenter()
    fresh: list[str] = []
    center.new_issue.connect(lambda n: fresh.append(n.key))

    center.upsert(Notice(key="k", title="一"))
    center.upsert(Notice(key="k", title="二"))
    assert fresh == ["k"]                 # 更新不算新问题
    assert center.get("k").title == "二"


def test_replace_prefix_clears_stale(app):
    center = NoticeCenter()
    center.replace_prefix("account:", [Notice(key="account:a", title="a"),
                                       Notice(key="account:b", title="b")])
    assert center.issue_count() == 2
    center.replace_prefix("account:", [Notice(key="account:b", title="b")])
    assert center.get("account:a") is None
    assert center.get("account:b") is not None
    assert center.issue_count() == 1


def test_panel_reuses_rows_and_empties_cleanly(app):
    from app.ui.notice_panel import NoticePanel

    panel = NoticePanel()
    panel.set_notices([Notice(key="a", title="一")])
    row = panel._rows["a"]
    panel.set_notices([Notice(key="a", title="二")])
    assert panel._rows["a"] is row        # 同一行复用，不整块重建（不闪烁）
    panel.set_notices([])
    assert panel._rows == {}


class _BarStub:
    """只提供 _flush_bars 用到的东西：待发清单 + 记录实际弹出的条目。"""

    def __init__(self, pending, accounts=None):
        self._pending_bars = list(pending)
        self.shown = []
        self.settings_page = SimpleNamespace(accounts=list(accounts or []))

    def _show_notice_bar(self, notice):
        self.shown.append(notice)

    def _show_account_notice(self, notices):
        # 合并规则用真实实现，桩只负责记录弹出来的条目
        from app.ui.main_window import MainWindow

        MainWindow._show_account_notice(self, notices)


def test_same_batch_account_issues_merge_into_one_bar(app):
    from app.ui.main_window import MainWindow

    pending = [Notice(key=f"account:{i}", title=f"账号 {i}：需验证码",
                      detail="图片验证码错误！(本轮跳过该账号的数据刷新，稍后自动重试)",
                      action=f"accounts:{i}", action_text="去设置", tone="error",
                      brief=f"初中部初三{i}班 账号异常")
               for i in (2, 3, 4)]
    accounts = [{"userName": str(i), "health": {"status": "captcha"}} for i in (2, 3, 4)]
    stub = _BarStub(pending, accounts)
    MainWindow._flush_bars(stub)

    assert len(stub.shown) == 1                  # 13 个账号同时异常也只弹一条
    bar = stub.shown[0]
    assert bar.detail == "3 个账号异常：需验证码 3"   # 一条里按原因归并
    assert bar.brief == bar.detail and bar.title == ""
    assert bar.action == "accounts:2"            # 点「去设置」定位到第一个异常账号


def test_single_account_issue_keeps_brief_text(app):
    from app.ui.main_window import MainWindow

    stub = _BarStub([Notice(key="account:x", title="账号 x：需验证码", detail="原因",
                            action="accounts:x", action_text="去设置", tone="error",
                            brief="x 账号异常")])
    MainWindow._flush_bars(stub)
    assert [n.brief for n in stub.shown] == ["x 账号异常"]


def test_upload_panel_lists_only_upload_issues(app):
    """上传页的公告只列与上传有关的：账号问题归系统配置，不该混进来。"""
    from app.ui.notice_panel import NoticePanel
    from app.ui.upload_page import UploadPage

    center = NoticeCenter()
    center.upsert(Notice(key="account:a", title="账号 a：登录冷却", scope="settings"))
    center.upsert(Notice(key="account:b", title="账号 b：需验证码", scope="settings"))
    center.upsert(Notice(key="upload:missing", title="支部名单缺团员：2 份", scope="upload"))

    class _PageStub:
        _notice_center = center
        notice_panel = NoticePanel()

    UploadPage._sync_notices(_PageStub)
    assert list(_PageStub.notice_panel._rows) == ["upload:missing"]
    assert _PageStub.notice_panel._summary.text() == "1 项待处理"
