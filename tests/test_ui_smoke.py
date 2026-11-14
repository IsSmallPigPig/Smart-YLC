"""界面构造冒烟：真实实例化各页面，捕获参数名、属性名这类构造期错误。

这些页面在测试里不会走完整交互，但只要构造通过，就说明信号连接、控件参数、
布局嵌套没有拼错——本轮就是靠它发现 SettingRow 传错关键字和分段控件取值错误的。
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def isolated_store(monkeypatch):
    from app.services import store

    tmp = tempfile.mkdtemp(prefix="gd-smoke-")
    monkeypatch.setattr(store, "CONFIG_FILE", os.path.join(tmp, "config.json"))
    store.save({
        "themeMode": "dark",
        "accounts": [],
        "memberCache": {},
        "messagesCache": {},
        "orgStatsCache": {},
        "notifyMode": "interval",
        "notifyInterval": 4,
    })
    return store


class FakeSettings:
    accounts = []
    rosters = {}
    roster = None

    def expected_pages(self) -> int:
        return 0

    def org_rule(self):
        from app.domain.org_rule import OrgRule

        return OrgRule()


class FakeWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.settings_page = FakeSettings()
        self.upload_page = None
        self.messages_page = None
        self.org_manage_page = None

    def switchTo(self, page):
        pass


def test_settings_page_builds(app, isolated_store):
    from app.ui.settings_page import SettingsPage

    page = SettingsPage()
    assert page.notify_mode.count() == 3
    assert page.notify_row.desc_label is not None
    assert page.workspace_row.desc_label.text().startswith("先选择工作目录")


def test_messages_page_builds_and_splits_by_tab(app, isolated_store):
    from app.ui.messages_page import MessagesPage

    page = MessagesPage(FakeWindow())
    assert page.kind_seg.currentRouteKey() == "report"
    assert page.read_seg.currentRouteKey() == "unread"
    page._rebuild()  # 空缓存走空态，不应抛错


def test_export_dialog_builds(app, isolated_store):
    from app.ui.export_dialog import ExportDialog

    host = FakeWindow()
    host.resize(900, 600)
    dialog = ExportDialog("branch", host)
    assert len(dialog._contents()) == 5
    dialog.mode.setCurrentItem("single")
    assert dialog.mode.currentRouteKey() == "single"
    assert dialog.scope == "branch"
    dialog.scope_seg.setCurrentItem("merged")
    assert dialog.scope == "merged"  # 一个入口里换范围，不再两个按钮


def test_home_and_upload_build(app, isolated_store):
    from app.ui.home_page import HomePage
    from app.ui.upload_page import UploadPage

    window = FakeWindow()
    home = HomePage(window)
    assert len(home.tiles) == 4
    assert len(home.bars) == 4
    upload = UploadPage(window)
    assert upload.target_combo.count() == 1
    upload._apply_statuses([])
    assert not upload.upload_btn.isEnabled()


def test_upload_branch_cards_use_tags(app, isolated_store):
    """支部卡片标题只留班名，份数与学段做标签，不再用「·」拼字符串。"""
    from app.domain.upload_inspect import FileStatus
    from app.ui.upload_page import UploadPage

    upload = UploadPage(FakeWindow())
    upload._apply_statuses([
        FileStatus(path="/tmp/a.pdf", name="张三+13800000001+入团志愿书.pdf",
                   pages=4, member="ok", account="ssfz12407",
                   branch="普通高中高三十二班"),
        FileStatus(path="/tmp/b.pdf", name="李四+13800000002+入团志愿书.pdf",
                   pages=4, member="missing", account="ssfz12407",
                   branch="普通高中高三十二班",
                   notice="该支部名单里没有这位团员"),
    ])
    cards = [upload.branch_lay.itemAt(i).widget() for i in range(upload.branch_lay.count())]
    assert len(cards) == 1
    card = cards[0]
    assert card.title_label.text() == "高三十二班"
    assert [p.text() for p in card._tags] == ["普通高中", "2 份", "1 缺团员"]
    assert "·" not in card.title_label.text()
    assert "缺团员" in upload.summary.text()


@pytest.mark.skipif(
    sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen"),
    reason="无边框窗口要真实 NSWindow，离屏下 qframelesswindow 的原生钩子会崩",
)
def test_browser_window_shell_builds(app, isolated_store):
    """账号网页窗口外壳：标签条接管标题栏，没有标签时停在欢迎页。"""
    from app.ui.browser_page import BrowserWindow

    win = BrowserWindow()
    assert win.tab_count() == 0
    assert not win.back_btn.isEnabled()
    assert win.empty.isVisible()  # 没有标签时只有欢迎页
    # 继承来的空 stackedWidget 会停在窗口左上角画一块不透明底，必须藏起来
    assert win.stackedWidget.isHidden()
    # 窗口里不再单独占一行状态：状态只挂在标签提示上
    assert not hasattr(win, "status")
    win.prompt()
    app.processEvents()
    assert win.tab_count() == 0
    win.close()


def test_about_card_has_clean_handle(app, isolated_store):
    from app.ui.about_card import AboutCard

    card = AboutCard()
    assert "IsSmallPigPig" in card.CREDIT
    assert "/" not in card.CREDIT
    assert "尚未检查" in card.update_row.desc_label.text()


def _pump(app, ms: int = 60):
    """跑一小段事件循环：分批建卡与淡入淡出都靠事件驱动，需要让它走完。"""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _payload(count: int, digest: str = "d1") -> dict:
    cache = {"ssfz12407": {"messages": [
        {"receive_id": i, "account": "ssfz12407",
         "title": "[系统通知][报道认证审核提醒]",
         "content": f"团员{i}向您所在团组织提交了团员报到申请",
         "mobile": "", "kind": "report", "status": 0, "status_str": "未读",
         "time_str": f"2026年11月10日 1{i % 10}:20:00"} for i in range(count)]}}
    return {"digest": digest, "cache": cache, "read": {}, "memberCache": {}}


def test_messages_paginates_and_flips(app, isolated_store):
    from app.ui.messages_page import PAGE_SIZE, MessagesPage

    page = MessagesPage(FakeWindow())
    page._on_cache_loaded(_payload(PAGE_SIZE * 2 + 3))
    _pump(app)
    assert len(page._views) == PAGE_SIZE * 2 + 3
    assert page.list_lay.count() == PAGE_SIZE  # 只建当前页
    assert page.page_label.text() == "第 1 / 3 页"
    assert page.prev_btn.isEnabled() is False
    assert page.next_btn.isEnabled() is True

    page._flip(1)
    _pump(app)
    assert page._page == 1
    assert page.list_lay.count() == PAGE_SIZE
    assert page.page_label.text() == "第 2 / 3 页"
    assert page.prev_btn.isEnabled() is True

    page._flip(9)  # 越界钳到最后页
    _pump(app)
    assert page._page == 2
    assert page.next_btn.isEnabled() is False


def test_messages_same_digest_skips_rebuild(app, isolated_store):
    from app.ui.messages_page import MessagesPage

    page = MessagesPage(FakeWindow())
    page._on_cache_loaded(_payload(6))
    _pump(app)
    before = page.list_lay.count()
    page._on_cache_loaded(_payload(6))  # 同一份缓存，不应重建
    assert page.list_lay.count() == before


def test_message_load_worker_emits_payload(app, isolated_store):
    from app.ui.messages_page import MessageLoadWorker

    captured = {}
    worker = MessageLoadWorker()
    worker.loaded.connect(lambda p: captured.update(p))
    worker.run()  # 直接在测试线程里跑一次，不需要真开线程
    assert set(captured) == {"digest", "cache", "read", "memberCache"}
    assert captured["cache"] == {}


def test_cross_fade_applies_work_and_cleans_effect(app, isolated_store):
    from app.ui.anim import cross_fade

    calls = []
    widget = QWidget()
    widget.resize(200, 120)
    widget.show()
    cross_fade(widget, lambda: calls.append(1), out=10, back=10)
    _pump(app, 120)
    assert calls == [1]
    assert widget.graphicsEffect() is None  # 结束后不残留合成层


def test_upload_search_filters_rows(app, isolated_store):
    from app.ui.upload_page import UploadPage

    upload = UploadPage(FakeWindow())
    upload._items_all = [
        {"title": "张三.pdf", "note": "", "tone": "ok", "status": "正常",
         "meta": ["4 页", "普通高中高一7班"]},
        {"title": "李四.pdf", "note": "页数不符", "tone": "error", "status": "异常",
         "meta": ["2 页", "初中部初三十班"]},
    ]
    upload._apply_filter()
    assert upload.status_list.count() == 2
    upload.set_search("高一7班")
    assert upload.status_list.count() == 1
    upload.set_search("")
    assert upload.status_list.count() == 2


def test_theme_switch_fades_then_applies(app, isolated_store):
    """切主题：先淡出、在透明帧里换肤、再淡入，结束后不留合成层。"""
    from app.ui.main_window import MainWindow
    from app.ui.messages_page import MessagesPage

    page = MessagesPage(FakeWindow())
    page.resize(600, 400)
    page.show()

    class Stack:
        def currentWidget(self):
            return page

    class Stub:
        _theme_busy = False
        stackedWidget = Stack()

        def __init__(self):
            self.updated = 0
            self.theme_calls = 0

        def update(self):
            self.updated += 1

        def on_theme_changed(self):
            self.theme_calls += 1

    stub = Stub()
    done = []
    MainWindow.apply_theme_async(stub, "light", done=lambda: done.append(1))
    assert stub._theme_busy is True  # 过渡期间不接受第二次点击
    _pump(app, 1200)  # 淡出+换肤+淡入约 300ms，整包跑时留足余量避免计时抖动
    assert done == [1]
    assert stub.theme_calls >= 1
    assert stub._theme_busy is False
    assert isolated_store.load()["themeMode"] == "light"
    assert page.scrollWidget.graphicsEffect() is None


def test_settings_control_column_stays_compact(app, isolated_store):
    """控件列按内容取宽：不超过六字按钮的宽度，次要项收进齿轮弹窗。"""
    from app.ui.about_card import AboutCard
    from app.ui.page import CONTROL_MAX_WIDTH
    from app.ui.settings_page import SettingsPage

    page = SettingsPage()
    rows = [page.rule_row, page.sec_row, page.workspace_row, page.notify_row]
    card = AboutCard()
    rows.append(card.update_row)
    for row in rows:
        holder = getattr(row, "control_holder", None)
        assert holder is not None, "该行没有控件列"
        assert holder.maximumWidth() == CONTROL_MAX_WIDTH
        assert holder.width() <= CONTROL_MAX_WIDTH


def test_add_account_lives_in_one_row(app, isolated_store):
    """「添加账号」的入口就在行内，不再单独铺一行输入框。"""
    from app.ui.settings_page import SettingsPage

    page = SettingsPage()
    holder = page.add_open_btn.parentWidget()
    while holder is not None and holder is not page:
        if getattr(page, "add_open_btn", None) is not None and \
                holder.findChild(type(page.add_open_btn), "") is page.add_open_btn:
            break
        holder = holder.parentWidget()
    assert page.add_open_btn.text() == "添加账号"
    # 四个输入框不在页面上，只在弹窗里
    assert not page.new_user.isVisible()


def test_open_web_uses_browser_window(app, isolated_store):
    """设置页的「打开网页」走独立窗口，且不能因为缺常量报 NameError。"""
    from app.ui.page import CONTROL_WIDTH  # noqa: F401  （确认常量可导入）
    from app.ui.settings_page import SettingsPage

    opened = []

    class Window(FakeWindow):
        def open_browser(self, user=""):
            opened.append(user)

    store_ = isolated_store
    store_.save({"accounts": [{"userName": "ssfz2400", "password": "x"}],
                 "activeUser": "ssfz2400"})
    page = SettingsPage()
    page.window_ref = Window()
    page.accounts = [{"userName": "ssfz2400", "password": "x"}]
    page.active_user = "ssfz2400"
    page._open_web()
    assert opened == ["ssfz2400"]


def test_abnormal_account_click_opens_embedded_browser(app, isolated_store):
    """健康状态异常时点那一格直接进内嵌浏览器手动登录；正常的账号点了不弹窗。"""
    from app.ui.settings_page import SettingsPage

    opened = []

    class Window(FakeWindow):
        def open_browser(self, user=""):
            opened.append(user)

    page = SettingsPage()
    page.window_ref = Window()
    page.accounts = [
        {"userName": "ssfz2400", "password": "x",
         "health": {"status": "cooling", "message": "验证码"}},
        {"userName": "ssfz2401", "password": "x", "health": {"status": "ok"}},
    ]
    page._on_cell_clicked(1, 6)          # 正常账号：不打开
    assert opened == []
    page._on_cell_clicked(0, 6)          # 异常账号：打开该账号
    assert opened == ["ssfz2400"]
    page._on_cell_clicked(0, 2)          # 其它列：不动
    assert opened == ["ssfz2400"]
    page._on_cell_double_clicked(1, 1)   # 双击任意行
    assert opened == ["ssfz2400", "ssfz2401"]


@pytest.mark.skipif(
    sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen"),
    reason="主窗口用 qframelesswindow 接管原生标题栏，离屏下构造会崩",
)
def test_main_window_close_shuts_browser(app, isolated_store):
    """主窗口关掉时账号网页窗口要一起收掉，不能留一个没有主的窗口。"""
    from app.ui.main_window import MainWindow

    win = MainWindow()

    class FakeBrowser:
        closed = False

        def shutdown(self):
            self.closed = True

    fake = FakeBrowser()
    win.browser_window = fake
    win.close()
    assert fake.closed


def test_reveal_branch_opens_matching_detail(app, isolated_store):
    """组织状况点错误团员：按支部名定位到组织管理的支部详情。"""
    from app.domain.org_manage import BranchNode
    from app.ui.org_manage_page import OrgManagePage

    page = OrgManagePage(FakeWindow())
    page._branches = [BranchNode(code="ssfz12407", label="普通高中高三七班"),
                      BranchNode(code="ssfz12408", label="普通高中高三八班")]
    assert page.reveal_branch("普通高中高三八班")
    assert page._detail_code == "ssfz12408"
    assert page.reveal_branch("高三七班")       # 备注被截短也能对上
    assert page._detail_code == "ssfz12407"
    assert not page.reveal_branch("不存在的支部")


def test_member_card_click_emits(app, isolated_store):
    """错误团员卡片点击发射信号，交给页面做跳转。"""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from app.ui.org_page import MemberCard

    card = MemberCard()
    card.resize(220, 70)
    card.show()
    hits = []
    card.clicked.connect(lambda: hits.append(1))
    QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    assert hits


def test_config_dialog_release_keeps_widgets_parented(app, isolated_store):
    """齿轮弹窗关掉后，控件不能变成顶层窗口。

    控件用 setParent(None) 归还时在 macOS 上会变成带交通灯的独立小窗
    （通知频率下拉框曾因此浮在屏幕左上角），这里断言它仍挂在原父级下。"""
    from qfluentwidgets import ComboBox

    from app.ui.dialogs import ConfigDialog

    host = QWidget()
    combo = ComboBox(host)
    combo.addItem("每 4 小时")

    dialog = ConfigDialog("通知设置", host)
    dialog.add_row(combo)
    dialog.show()
    assert combo.parentWidget() is not host  # 已挪进弹窗

    dialog._release()
    assert combo.parentWidget() is host
    assert not combo.isWindow()


@pytest.mark.skipif(
    sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen"),
    reason="主窗口用 qframelesswindow 接管原生标题栏，离屏下构造会崩",
)
def test_back_button_stays_visible_without_history(app, isolated_store):
    """返回键一直留在标题栏上：没有返回历史时置灰，不整块消失。"""
    from app.ui.main_window import MainWindow

    win = MainWindow()
    try:
        assert win.back_btn.isVisibleTo(win)
        win._sync_back_state()
        assert win.back_btn.isVisibleTo(win)
        # 标题文字左边缘固定：有无可返回的历史都一样
        left = win.titleBar.titleLabel.geometry().left()
        win._sync_back_state()
        assert win.titleBar.titleLabel.geometry().left() == left
    finally:
        win.close()
