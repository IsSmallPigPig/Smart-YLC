"""账号网页：标签上限与登录流程的纯逻辑部分。

QWebEngineView 需要真实图形环境，这里只测不依赖渲染的判定与常量。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.constants import SITE_BASE_URL
from app.domain.login_protocol import build_login_url
from app.ui.browser_page import LOGIN_CHECK_JS, MAX_TABS, SITE_HOME


def test_tab_limit_is_bounded():
    assert 1 <= MAX_TABS <= 12


def test_login_url_targets_api_login():
    """登录走接口 GET：浏览器打开它，由服务端 Set-Cookie 建立会话。"""
    url = build_login_url("ssfz12407", "abc12345")
    assert url.startswith("https://tuanapi.12355.net/login/adminLogin?")
    assert "userName=ssfz12407" in url


def test_home_is_the_site_dashboard():
    assert SITE_HOME.startswith(SITE_BASE_URL)
    assert SITE_HOME.endswith("/bg/index_main.html")


def test_site_root_counts_as_login_page():
    """站点根路径就是登录页：打完接口登录后回根路径，等于一直停在登录页。"""
    from PySide6.QtCore import QUrl

    from app.ui.browser_page import _is_home_page, _is_login_page

    assert _is_login_page(QUrl(SITE_BASE_URL))
    assert _is_login_page(QUrl(f"{SITE_BASE_URL}/bg/index.html"))
    assert not _is_login_page(QUrl(SITE_HOME))
    assert _is_home_page(QUrl(SITE_HOME))
    assert not _is_home_page(QUrl(SITE_BASE_URL))
    assert not _is_login_page(QUrl("https://tuanapi.12355.net/login/adminLogin"))


def test_login_check_reads_password_box():
    """首页判定：密码框还在 = 没进系统；站点失效提示优先报。"""
    assert "input[type=password]" in LOGIN_CHECK_JS
    assert "登录状态已失效" in LOGIN_CHECK_JS


def test_branch_picker_lists_accounts(monkeypatch, tmp_path):
    """弹窗列出账号；确认后把选中的账号名交回调用方。"""
    from app.services import store

    monkeypatch.setattr(store, "CONFIG_FILE", os.path.join(tmp_path, "config.json"))
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])
    from app.ui.branch_picker import BranchPickerDialog

    host = QWidget()
    host.resize(800, 600)
    accounts = [{"userName": "ssfz2400", "remark": "普通高中高一7班"},
                {"userName": "ssfz2401", "remark": "普通高中高一8班"}]
    dlg = BranchPickerDialog(accounts, host, current="ssfz2401")
    assert dlg.combo.count() == 2
    assert dlg.combo.currentData() == "ssfz2401"  # 默认选中调用方给的账号
    dlg.combo.setCurrentIndex(0)
    dlg.accept()
    app.processEvents()
    assert dlg.chosen == "ssfz2400"


def test_pick_branch_without_accounts_returns_none():
    from app.ui.branch_picker import pick_branch

    assert pick_branch([], None, lambda user: None) is None
