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
    # 未选目录时不写说明：这一行的 desc 只承载已选路径，空的就隐藏
    assert page.workspace_row.desc_label.text() == ""
    assert not page.workspace_row.desc_label.isVisible()


def test_load_config_keeps_workspace_and_rosters(app, isolated_store):
    """载入配置不能把还没读进来的项回写成空值。

    _load_config 里的 setCurrentIndex 会触发 currentIndexChanged →
    _on_notify_mode_changed → _save_config，而那一刻名单和工作区还没载入。
    没有 _loading 闸门时，每次启动都会把磁盘上的 rosters / workspaceDir 抹掉。"""
    store = isolated_store
    store.update(lambda c: (
        c.__setitem__("workspaceDir", "/tmp/ws"),
        c.__setitem__("rosters", {"2026秋": "/tmp/r.xlsx"}),
    ))

    from app.ui.settings_page import SettingsPage

    SettingsPage()

    cfg = store.load()
    assert cfg["workspaceDir"] == "/tmp/ws"
    assert cfg["rosters"] == {"2026秋": "/tmp/r.xlsx"}


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
    assert [p.text() for p in card._tags] == ["普通高中", "2 份", "缺团员 1"]
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
    """搜索只过滤下方按支部折叠的卡片：匹配不到的支部整体不出现。"""
    from app.ui.upload_page import UploadPage
    from app.domain.upload_inspect import FileStatus

    upload = UploadPage(FakeWindow())
    upload.statuses = [
        FileStatus(path="/a/张三.pdf", name="张三.pdf", pages=4,
                   branch="普通高中高一7班", account="acc1", naming="ok", member="ok"),
        FileStatus(path="/a/李四.pdf", name="李四.pdf", pages=2,
                   branch="初中部初三十班", account="acc2",
                   naming="error", problem="页数不符"),
    ]
    upload._rebuild_branches()
    assert len(upload._branch_rows) == 2  # 无搜索：两份都在

    upload.set_search("高一7班")
    assert len(upload._branch_rows) == 1  # 只保留匹配到的那个支部
    assert "/a/张三.pdf" in upload._branch_rows

    upload.set_search("")
    assert len(upload._branch_rows) == 2  # 清空搜索后两份恢复


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
    # 输入框不再常驻页面；打开弹窗时才随弹窗新建，并挂在弹窗自身之下
    assert not hasattr(page, "new_user")
    page._open_add_account()
    dialog = page._add_dialog
    assert dialog is not None
    from qfluentwidgets import LineEdit

    assert isinstance(dialog.user_edit, LineEdit)
    # 输入框是弹窗内容树的一部分，而不是页面的持久控件
    assert dialog.user_edit.parentWidget() is not None
    dialog.reject()


def test_add_account_dialog_fresh_per_open(app, isolated_store):
    """回归：添加账号弹窗每次打开都用全新输入框，连开两次不串控件、不残留。

    旧实现复用页面上的持久 LineEdit，跨两次弹窗 reparent 会留下渲染错位/穿透。"""
    from qfluentwidgets import LineEdit

    from app.ui.settings_page import SettingsPage

    page = SettingsPage()
    page.window_ref = None  # 提交链里 refresh_all 走空，不连真实服务

    # 第一次打开
    page._open_add_account()
    d1 = page._add_dialog
    assert isinstance(d1.user_edit, LineEdit)
    assert d1.user_edit.parentWidget() is not None
    first_id = id(d1.user_edit)

    # 填好提交：账号入库，弹窗关闭
    d1.user_edit.setText("ssfz24101")
    d1.pwd_edit.setText("secret")
    assert page._add_manual(user="ssfz24101", pwd="secret", remark="") is True
    assert any(a["userName"] == "ssfz24101" for a in page.accounts)
    # 关闭后输入框随弹窗销毁，不再挂在页面上
    assert not hasattr(page, "new_user")

    # 第二次打开：必须是另一批全新控件，而不是第一次那批
    page._open_add_account()
    d2 = page._add_dialog
    assert d2 is not d1
    assert id(d2.user_edit) != first_id
    assert isinstance(d2.user_edit, LineEdit)
    assert d2.user_edit.parentWidget() is not None
    d2.reject()


def test_notify_dialog_fresh_per_open_and_writeback(app, isolated_store):
    """回归：通知弹窗每次打开用全新控件，关窗把时间/间隔写回持久属性。

    旧实现复用页面上的持久 time_edit/interval 控件，两次打开会重复 reparent
    导致渲染错位；且改动不会落回 _save_config 读取的属性。"""
    from PySide6.QtWidgets import QApplication

    from qfluentwidgets import ComboBox

    from app.ui.settings_page import SettingsPage

    page = SettingsPage()
    # isolated_store 默认 notifyInterval=4
    assert page.notify_interval.currentData() == 4

    captured = []
    orig = page._config_dialog
    page._config_dialog = lambda *a, **k: captured.append(orig(*a, **k)) or captured[-1]

    page._open_notify_dialog()
    dlg = captured[-1]
    combo = dlg.findChild(ComboBox)
    assert combo is not None
    assert combo is not page.notify_interval  # 弹窗里是全新控件，不是页面持久控件

    idx = combo.findData(2)
    combo.setCurrentIndex(idx)
    dlg.accept()
    QApplication.processEvents()  # 让 deleteLater / 回调落地
    # 值已写回持久属性并落盘
    assert page.notify_interval.currentData() == 2

    # 再次打开：又是一批全新控件，且读到的仍是上次写回的 2 小时
    page._open_notify_dialog()
    dlg2 = captured[-1]
    combo2 = dlg2.findChild(ComboBox)
    assert combo2 is not None and combo2 is not page.notify_interval
    assert page.notify_interval.currentData() == 2
    dlg2.reject()


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
