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
    from app.ui.page import CollapsibleCard
    from app.ui.settings_page import SettingsPage

    page = SettingsPage()
    assert page.notify_mode.count() == 3
    assert page.notify_row.desc_label is not None
    # 动态信息没有内容时也要有四个字的默认占位
    assert page.workspace_row.desc_label.text() == "尚未选择"
    assert page.roster_row.desc_label.text() == "尚未导入"
    assert page.sec_row.desc_label.text() == "尚未导入"
    assert page.rule_row.desc_label.text() == "当前 3 条规则"
    assert page.file_btn.text() == "导入导出"
    assert [a.text() for a in page.file_menu.actions()] == [
        "从表格文件中导入", "从系统备份中导入", "下载账号模板", "导出系统备份"]
    assert page.roster_more_btn.text() == "名单管理"
    assert page.sec_more_btn.text() == "支书管理"
    cards = page.findChildren(CollapsibleCard)
    assert cards and all(card.desc_label.text() for card in cards)


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
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("single"))
    assert dialog.mode == "single"
    assert dialog.scope == "branch"
    dialog.scope_combo.setCurrentIndex(dialog.scope_combo.findData("merged"))
    assert dialog.scope == "merged"  # 一个入口里换范围，不再两个按钮


def test_home_reopen_keeps_page_instead_of_covering_it(app, isolated_store):
    """从托盘回来、切回首页时不再铺白色过渡页：概览已经在屏幕上，盖一层只会整页闪白。"""
    from PySide6.QtGui import QShowEvent

    from app.ui.home_page import HomePage

    home = HomePage(FakeWindow())
    home._stats_shown = True
    home.showEvent(QShowEvent())
    overlay = getattr(home, "_loading", None)
    assert overlay is None or overlay.isHidden()


def test_home_first_show_still_covers_page(app, isolated_store):
    """首次显示（还没算过概览）仍要过渡页，遮住未就绪的内容。"""
    from app.ui.home_page import HomePage

    home = HomePage(FakeWindow())
    assert home._stats_shown is False
    home._show_loading()
    assert not home._loading.isHidden()


def test_home_snapshot_failure_drops_loading_overlay(app, isolated_store, monkeypatch):
    """快照抛异常时撤掉过渡页：否则整页停在「正在加载概览…」，看起来像没渲染出来。"""
    import time

    from app.services import org_snapshot
    from app.ui.home_page import HomePage

    def boom(_cfg):
        raise RuntimeError("配置读不了")

    monkeypatch.setattr(org_snapshot, "snapshot", boom)
    home = HomePage(FakeWindow())
    home.refresh_stats(animate=False, show_loading=True)
    end = time.monotonic() + 3
    while time.monotonic() < end and not home._loading.isHidden():
        app.processEvents()
        time.sleep(0.01)
    assert home._loading.isHidden()
    assert home._stats_shown is False


def test_home_and_upload_build(app, isolated_store):
    from app.ui.home_page import HomePage
    from app.ui.upload_page import UploadPage

    window = FakeWindow()
    home = HomePage(window)
    assert len(home.tiles) == 4
    assert len(home.bars) == 4
    upload = UploadPage(window)
    # 压缩目标固定 9.5MB，界面上不再给选项
    from app.constants import LIMITS
    from app.ui.upload_page import COMPRESS_TARGET

    assert COMPRESS_TARGET == LIMITS["PDF_DEFAULT_TARGET_BYTES"]
    assert not hasattr(upload, "target_combo")
    upload._apply_statuses([], [])
    assert not upload.upload_btn.isEnabled()


def test_import_config_replaces_and_reloads(app, isolated_store, tmp_path):
    """导入配置 = 整份替换并即时重载；替换前留一份可回退的备份。"""
    from app.services import store
    from app.ui.settings_page import SettingsPage

    store.save({"accounts": [{"userName": "old1", "remark": "高一1班", "password": "a"}],
                "themeMode": "light", "translucentMaterial": "none"})
    page = SettingsPage()

    page._do_import_config({
        "accounts": [{"userName": "new1", "remark": "高二2班", "password": "b"}],
        "rosters": {"2026秋": "/tmp/b.xlsx"},
        "themeMode": "dark", "translucentMaterial": "acrylic",
        "workspaceDir": "/tmp/ws-new",
    })

    assert [a["userName"] for a in store.load()["accounts"]] == ["new1"]
    assert [a["userName"] for a in page.accounts] == ["new1"]
    assert page.theme_combo.currentIndex() == 2          # dark
    assert page.material_combo.currentText() == "Acrylic"
    assert page.ws_edit.text() == "/tmp/ws-new"

    folder = os.path.dirname(store.CONFIG_FILE)
    assert any("before-import" in n for n in os.listdir(folder))


def test_autostart_does_not_require_accounts(app, isolated_store, monkeypatch):
    """开机自启只跟「托盘要不要开机跑」有关，没配账号也能开。"""
    from app.services import autostart
    from app.ui.settings_page import SettingsPage

    calls = []
    monkeypatch.setattr(autostart, "is_installed", lambda: False)
    monkeypatch.setattr(autostart, "install", lambda: calls.append("install") or "/tmp/x.plist")

    page = SettingsPage()
    page.accounts = []
    page._toggle_autostart()
    assert calls == ["install"]


def test_rounded_corners_is_windows11_only(app):
    """圆角走 DWM，Windows 11 之外直接返回 False，不再假装成功。"""
    from app.services.materials import apply_rounded_corners, win11_build

    widget = QWidget()
    assert apply_rounded_corners(widget) is (win11_build() >= 22000)


def test_material_default_is_mica(app, isolated_store):
    """窗口背景默认 Mica；关闭项写「关闭模糊」。"""
    from app.services.materials import preferred
    from app.ui.settings_page import SettingsPage

    assert preferred({}) == "mica"
    assert preferred({"translucent": False}) == "none"     # 旧配置明确关掉过就保持关闭
    assert preferred({"translucentMaterial": "acrylic"}) == "acrylic"

    page = SettingsPage()
    labels = [page.material_combo.itemText(i) for i in range(page.material_combo.count())]
    assert labels == ["关闭模糊", "Acrylic", "Mica"]
    assert page.material_combo.currentText() == "Mica"


def test_settings_page_does_not_write_before_load(app, isolated_store):
    """配置没载入完成时不许整份回写：那会把磁盘上的账号/名单抹成空。"""
    from app.services import store
    from app.ui.settings_page import SettingsPage

    store.update(lambda c: c.__setitem__("accounts", [{"userName": "keep"}]))
    before = store.load()

    page = SettingsPage()
    page._loaded = False          # 模拟「构造到一半」
    page.accounts = []
    page._save_config()
    assert store.load()["accounts"] == before["accounts"]


def test_upload_branch_cards_use_tags(app, isolated_store):
    """支部卡片标题只留班名，份数与学段做标签，不再用「·」拼字符串。"""
    from app.domain.upload_inspect import FileStatus
    from app.ui.upload_page import UploadPage

    upload = UploadPage(FakeWindow())
    upload._apply_statuses([], [
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
    assert upload.stat_values["total"].text() == "2"
    assert upload.stat_values["branches"].text() == "1"


@pytest.mark.skipif(
    sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen"),
    reason="无边框窗口要真实 NSWindow，离屏下 qframelesswindow 的原生钩子会崩",
)
def test_browser_window_shell_builds(app, isolated_store):
    """账号网页窗口外壳：标签条接管标题栏，没有标签时停在欢迎页。"""
    from app.ui.browser_page import TAB_HEIGHT, BrowserWindow

    win = BrowserWindow()
    assert win.tab_count() == 0
    # 标题栏按系统按钮的中线定高，标签行在它里面居中：三者中心同一条水平线
    assert win.tab_row.height() == TAB_HEIGHT
    assert win.titleBar.height() >= TAB_HEIGHT
    assert abs(win.tab_row.geometry().center().y()
               - win.titleBar.height() // 2) <= 1
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


def test_health_summary_merges_reasons(app, isolated_store):
    """一轮测活结束只给一条汇总，按原因归并，不再逐个账号弹消息条。"""
    from app.ui import settings_page as sp

    page = sp.SettingsPage()
    page.accounts = [
        {"userName": f"u{i}", "remark": f"高三{i}班",
         "health": {"status": status, "message": ""}}
        for i, status in enumerate(("failed", "captcha", "ok", "network"))
    ]
    page._health_checked = [a["userName"] for a in page.accounts]

    shown = []
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(sp.InfoBar, "warning",
                        lambda title, content, **k: shown.append((title, content)))
    monkeypatch.setattr(sp.InfoBar, "success",
                        lambda title, content, **k: shown.append((title, content)))
    try:
        page._report_health()
    finally:
        monkeypatch.undo()

    assert len(shown) == 1
    title, content = shown[0]
    assert title == "3 个账号异常"
    assert "密码错误 1" in content and "需验证码 1" in content and "网络错误 1" in content


def test_branch_picker_marks_abnormal_accounts(app):
    """异常账号也列出来，标在名字后面：加号不能因为全异常就不弹选择框。"""
    from PySide6.QtWidgets import QWidget

    from app.ui.branch_picker import BranchPickerDialog

    host = QWidget()
    host.resize(600, 400)
    dlg = BranchPickerDialog([
        {"userName": "a", "remark": "高三一班", "health": {"status": "captcha"}},
        {"userName": "b", "remark": "高二二班", "health": {"status": "ok"}},
    ], host, note="1 个账号状态异常")
    assert dlg.combo.count() == 2
    assert "需验证码" in dlg.combo.itemText(0)
    assert "需验证码" not in dlg.combo.itemText(1)


def test_browser_tab_row_drags_on_press(app):
    """拖窗必须在按下时交给系统。

    macOS 的 performWindowDragWithEvent 只认 mouseDown；等鼠标移动再从
    mouseMove 调，AppKit 报「Window move completed without beginning」，
    而且这一下经常真的拖不动。
    """
    from app.ui.browser_page import ChromeRow

    assert "mousePressEvent" in ChromeRow.__dict__
    assert "mouseMoveEvent" not in ChromeRow.__dict__


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


def test_banner_light_theme_is_pale_with_dark_text(app, isolated_store):
    """浅色模式的头图与首页同一套浅蓝，字换成深色——白字在浅底上看不清。"""
    from qfluentwidgets import Theme, setTheme

    from app.ui.page import (BANNER_STOPS_LIGHT, PageBanner, banner_stops,
                             banner_text_colors)

    setTheme(Theme.LIGHT)
    banner = PageBanner("上传档案", "把团员档案 PDF 上传到站点")

    assert banner_stops() == BANNER_STOPS_LIGHT
    assert BANNER_STOPS_LIGHT[0].lightness() > 180      # 顶色是浅蓝，不是饱和深蓝
    assert BANNER_STOPS_LIGHT[1].lightness() > 200      # 下端更浅，融进页面底色
    title, _ = banner_text_colors()
    assert title.lightness() < 80
    assert banner.title_label._lightColor.lightness() < 80

    setTheme(Theme.DARK)                                 # 切主题后字要跟着换回白色
    assert banner_stops() != BANNER_STOPS_LIGHT
    assert banner.title_label._darkColor.lightness() > 200


def test_upload_add_dirs_dedupes_nested(app, isolated_store, tmp_path):
    """加文件夹：已添加目录之下的子目录跳过，重复点选不重复计入。"""
    from app.services.workspace import load_workspace_dirs
    from app.ui.upload_page import UploadPage

    root = tmp_path / "年级"
    child = root / "高一1班"
    other = tmp_path / "高一2班"
    child.mkdir(parents=True)
    other.mkdir()

    upload = UploadPage(FakeWindow())
    upload.refresh = lambda: None        # 不启动扫描线程，只看列表怎么变

    assert upload.add_dirs([str(root)]) == 1
    assert upload.add_dirs([str(child), str(other)]) == 1   # 子目录被跳过
    assert upload.add_dirs([str(root)]) == 0                # 完全重复
    assert load_workspace_dirs() == [str(root), str(other)]


def test_upload_remove_dir_keeps_others(app, isolated_store, tmp_path):
    from app.services.workspace import load_workspace_dirs, save_workspace_dirs
    from app.ui.upload_page import UploadPage

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    save_workspace_dirs([str(a), str(b)])

    upload = UploadPage(FakeWindow())
    upload.refresh = lambda: None
    upload._dirs = load_workspace_dirs()
    upload.remove_dir(str(a))
    assert load_workspace_dirs() == [str(b)]


def test_upload_drop_payload_takes_folders_and_pdf_parents(app, isolated_store, tmp_path):
    """拖进来的文件夹直接加；拖的是 PDF 就用它所在的文件夹。"""
    from PySide6.QtCore import QMimeData, QUrl

    from app.ui.upload_page import UploadPage

    folder = tmp_path / "高一1班"
    folder.mkdir()
    pdf = folder / "张三+13800000001+入团志愿书.pdf"
    pdf.write_bytes(b"x")
    zipped = tmp_path / "名单.zip"
    zipped.write_bytes(b"x")

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(folder)), QUrl.fromLocalFile(str(pdf)),
                  QUrl.fromLocalFile(str(zipped))])
    dirs, parents = UploadPage._drop_payload(mime)
    assert dirs == [str(folder)]
    assert parents == [str(folder)]      # PDF 归到它所在的文件夹
    assert UploadPage._drop_payload(QMimeData()) == ([], [])


def test_upload_folder_card_is_plain_card(app, isolated_store, tmp_path):
    """来源目录本身折叠；导入的文件夹只是一张普通卡片，不再各自展开一份文件清单。"""
    from app.domain.archive_scan import scan_folder
    from app.domain.upload_inspect import FileStatus
    from app.ui.upload_page import UploadPage

    folder = tmp_path / "高一1班"
    folder.mkdir()
    (folder / "关于放假的通知.pdf").write_bytes(b"x")

    upload = UploadPage(FakeWindow())
    upload._dirs = [str(folder)]
    upload._scans = [scan_folder(str(folder))]
    upload.statuses = [FileStatus(path=str(folder / "张三.pdf"), name="张三.pdf",
                                  root=str(folder), pages=4, account="ssfz12407",
                                  branch="普通高中高三七班")]
    upload._rebuild_sources()

    assert upload.source_card.title_label.text() == "来源目录"
    card = upload._folder_cards[str(folder)]
    assert card.title_label.text() == "高一1班"
    assert card.desc_label.text() == str(folder)
    assert [p.text() for p in card._tags] == ["1 份", "忽略 1"]
    assert not hasattr(card, "body")          # 普通卡片，不再自带折叠/文件清单


def test_update_download_failure_clears_progress(app, isolated_store):
    """下载失败必须收掉「正在下载」浮层，按钮回到可重试的状态。"""
    from PySide6.QtWidgets import QVBoxLayout

    from app.ui.about_card import AboutCard

    host = QWidget()
    host.resize(900, 600)
    lay = QVBoxLayout(host)
    card = AboutCard()
    lay.addWidget(card)
    host.show()
    _pump(app, 50)

    card._info = type("Info", (), {"version": "9.9.9"})()
    card._open_toast("下载更新").set_progress(42, 100, "正在下载更新包 42%")
    assert card._toast is not None

    card._on_download_failed("直接下载失败：The read operation timed out")
    assert card._toast is None
    assert card.check_btn.isEnabled()
    assert "重新下载" in card.check_btn.text()


def _instant_card_timers(monkeypatch, card_holder):
    """测试里把卡片自己的延时立刻执行，别的 QTimer 保持原样。"""
    from PySide6.QtCore import QTimer

    original = QTimer.singleShot

    def _single(msec, callback, *args, **kwargs):
        if getattr(callback, "__self__", None) is card_holder.get("card"):
            callback(*args, **kwargs)
            return
        return original(msec, callback, *args, **kwargs)

    monkeypatch.setattr(QTimer, "singleShot", _single)


def test_download_starts_silent_install(app, isolated_store, tmp_path, monkeypatch):
    """下载完就交给独立进程静默安装，不再弹出安装窗口。"""
    from PySide6.QtWidgets import QVBoxLayout

    from app.services import silent_update
    from app.ui.about_card import AboutCard

    calls = []
    monkeypatch.setattr(silent_update, "start", lambda path: calls.append(path) or True)
    holder = {}
    _instant_card_timers(monkeypatch, holder)

    host = QWidget()
    host.resize(900, 600)
    lay = QVBoxLayout(host)
    card = AboutCard()
    holder["card"] = card
    lay.addWidget(card)
    host.show()
    _pump(app, 50)

    pkg = tmp_path / "SmartYouth-Windows-x64-setup.exe"
    pkg.write_bytes(b"x")
    card._open_toast("下载更新").set_progress(100, 100, "正在下载更新包 100%")
    card._on_downloaded(str(pkg))

    assert calls == [str(pkg)]
    assert card._toast is None
    assert card.check_btn.text() == "正在安装"


def test_silent_install_failure_keeps_downloaded_package(app, isolated_store, tmp_path, monkeypatch):
    """助手拉不起来时保留更新包，并让按钮回到「打开目录」手动安装。"""
    from PySide6.QtWidgets import QVBoxLayout

    from app.services import silent_update
    from app.ui.about_card import AboutCard

    def _boom(path):
        raise silent_update.SilentInstallError("没有权限打开")

    monkeypatch.setattr(silent_update, "start", _boom)
    holder = {}
    _instant_card_timers(monkeypatch, holder)

    host = QWidget()
    host.resize(900, 600)
    lay = QVBoxLayout(host)
    card = AboutCard()
    holder["card"] = card
    lay.addWidget(card)
    host.show()
    _pump(app, 50)

    pkg = tmp_path / "SmartYouth-Windows-x64-setup.exe"
    pkg.write_bytes(b"x")
    card._open_toast("下载更新").set_progress(100, 100, "正在下载更新包 100%")
    card._on_downloaded(str(pkg))

    assert card._toast is None
    assert card._downloaded == str(pkg)
    assert card.check_btn.isEnabled()
    assert card.check_btn.text() == "打开目录"


def test_update_check_latest_finishes_progress(app, isolated_store):
    """已是最新：浮层收掉并把结论留在浮层文案上，不再另弹信息条。"""
    from PySide6.QtWidgets import QVBoxLayout

    from app.ui.about_card import AboutCard

    host = QWidget()
    host.resize(900, 600)
    lay = QVBoxLayout(host)
    card = AboutCard()
    lay.addWidget(card)
    host.show()
    _pump(app, 50)

    card._open_toast("检查更新").set_indeterminate("正在检查更新…")
    card._on_checked(None)
    assert card._toast is None
    assert card.check_btn.text() == "检查更新"


def test_update_found_moves_action_into_row(app, isolated_store):
    """发现新版本：动作落在行内按钮上，不再往信息条里加控件（那会撑出窗口）。"""
    from PySide6.QtWidgets import QVBoxLayout

    from app.ui.about_card import AboutCard

    host = QWidget()
    host.resize(900, 600)
    lay = QVBoxLayout(host)
    card = AboutCard()
    lay.addWidget(card)
    host.show()
    _pump(app, 50)

    card._open_toast("检查更新").set_indeterminate()
    card._on_checked(type("Info", (), {"version": "9.9.9"})())
    assert card._toast is None
    assert card.check_btn.text() == "下载 9.9.9"
    assert "9.9.9" in card.update_row.desc_label.text()


def test_progress_toast_indeterminate_scans(app, isolated_store):
    """总量未知时进度线自己走：不定模式有推动的相位，收尾会停下来。"""
    from app.ui.widgets import ProgressToast

    host = QWidget()
    host.resize(600, 400)
    host.show()
    toast = ProgressToast("检查更新", host)
    toast.set_indeterminate("正在检查更新…")
    _pump(app, 80)
    assert toast._phase > 0

    toast.finish(text="已是最新版本")
    assert toast._label.text() == "已是最新版本"
    assert toast._indeterminate is False
    assert not toast._marquee.isActive()
    toast.close()


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


@pytest.mark.skipif(
    sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen"),
    reason="主窗口用 qframelesswindow 接管原生标题栏，离屏下构造会崩",
)
def test_page_switch_does_not_fade_page(app, isolated_store):
    """切页不套整页透明度动画。

    QGraphicsOpacityEffect 会把整页（含 banner）先渲染成低透明度离屏图，首帧是一张
    发虚、边界全无的页面：banner 先"没有"再出来，像闪一下；卡片边框也跟着时隐时现。
    切页是同一窗口内的内容替换，直接换就行，不需要入场动画。"""
    from app.ui.main_window import MainWindow

    win = MainWindow()
    win.switchTo(win.org_page)
    assert win.org_page.graphicsEffect() is None
    win.close()


def test_reveal_branch_opens_matching_detail(app, isolated_store):
    """组织状况点错误团员：按支部名定位到组织管理的支部详情。

    reveal_branch 已改为异步（树没算好时挂到后台刷新完成后再开，绝不阻塞点击），
    返回 None；找不到支部由 on_missing 兜底。这里走「树已就绪」的同步打开路径。"""
    import time

    from app.domain.org_manage import BranchNode
    from app.ui.org_manage_page import OrgManagePage

    page = OrgManagePage(FakeWindow())
    page._branches = [BranchNode(code="ssfz12407", label="普通高中高三七班"),
                      BranchNode(code="ssfz12408", label="普通高中高三八班")]
    page._refreshed_at = time.monotonic()  # 树已就绪：直接同步打开

    missing = []
    page.reveal_branch("普通高中高三八班", on_missing=lambda: missing.append(1))
    assert page._detail_code == "ssfz12408"
    page.reveal_branch("高三七班", on_missing=lambda: missing.append(1))  # 备注被截短也能对上
    assert page._detail_code == "ssfz12407"

    # 找不到支部：不打开详情，on_missing 兜底
    page._detail_code = ""
    page.reveal_branch("不存在的支部", on_missing=lambda: missing.append(1))
    assert page._detail_code == ""
    assert len(missing) == 1


def test_org_manage_clear_hides_old_cards(app, isolated_store):
    """切页重建前旧卡片必须先隐藏：deleteLater 要等事件循环，叠在新卡片上会糊成两层。"""
    from app.ui.org_manage_page import OrgManagePage

    page = OrgManagePage(FakeWindow())
    old = QWidget()
    page.feed_lay.addWidget(old)
    old.show()
    page._clear(page.feed_lay)
    assert old.isHidden()


def test_org_page_has_no_save_button_and_runs_hourly(app, isolated_store):
    from app.ui.org_page import OrgPage

    page = OrgPage(FakeWindow())
    assert not hasattr(page, "export_btn")
    assert page._auto_timer.interval() == 60 * 60 * 1000
    assert page._auto_timer.isActive()


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

    旧实现用 setParent(None) 归还控件，会在 Windows/macOS 上变成带标题栏的独立小窗
    （通知设置里的「08:00」时间框曾因此浮在屏幕左上角）。新实现：弹窗完全拥有内容
    控件，关窗时随弹窗一起销毁，控件自始至终不是顶层窗口。"""
    from qfluentwidgets import ComboBox

    from app.ui.dialogs import ConfigDialog

    host = QWidget()
    combo = ComboBox(host)
    combo.addItem("每 4 小时")

    dialog = ConfigDialog("通知设置", host)
    dialog.add_row(combo)
    dialog.show()
    assert combo.parentWidget() is not host  # 已挪进弹窗
    assert not combo.isWindow()              # 不是顶层窗口

    dialog._release()
    # 控件仍属于弹窗子树，绝不是顶层窗口；弹窗销毁时一并清理
    assert not combo.isWindow()
    assert combo.parentWidget() is not host


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


# ---------------------------------------------------------------- 单实例

def test_second_instance_is_rejected(app, monkeypatch):
    from app.services import single_instance

    name = f"SmartYLC-test-{os.getpid()}"
    monkeypatch.setattr(single_instance, "server_name", lambda: name)
    first = single_instance.SingleInstance()
    assert first.attach() is True
    second = single_instance.SingleInstance()
    assert second.attach() is False
    first.close()


def test_single_instance_notifies_existing_window(app, monkeypatch):
    from app.services import single_instance

    name = f"SmartYLC-notify-{os.getpid()}"
    monkeypatch.setattr(single_instance, "server_name", lambda: name)
    first = single_instance.SingleInstance()
    assert first.attach() is True
    got = []
    first.set_handler(got.append)
    assert single_instance.notify_running() is True
    for _ in range(20):
        app.processEvents()
        if got:
            break
    assert got == ["show"]
    first.close()
