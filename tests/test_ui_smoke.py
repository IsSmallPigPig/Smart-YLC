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
from PyQt5.QtWidgets import QApplication, QWidget


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
    # 账号卡片只管账号表格；系统备份单独成卡，不再混在「账号文件」里
    assert page.file_btn.text() == "账号表格"
    assert [a.text() for a in page.file_menu.actions()] == [
        "导入文件", "下载模版"]
    assert page.backup_export_btn.text() == "导出备份"
    assert page.backup_import_btn.text() == "恢复备份"
    assert page.roster_more_btn.text() == "名单管理"
    assert page.sec_more_btn.text() == "支书管理"
    cards = page.findChildren(CollapsibleCard)
    assert cards and all(card.desc_label.text() for card in cards)
    backup = next(c for c in cards if c.title_label.text() == "系统备份")
    assert "全部" in backup.desc_label.text()
    # 备份是账号卡之外的一张独立卡
    assert backup is not page.acc_card


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
    # 分班导出：一个班一张表，呈现方式没得选
    assert not dialog.mode_combo.isEnabled()
    dialog.scope_combo.setCurrentIndex(dialog.scope_combo.findData("merged"))
    assert dialog.scope == "merged"  # 一个入口里换范围，不再两个按钮
    assert dialog.mode_combo.isEnabled()
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("single"))
    assert dialog.mode == "single"


def test_home_reopen_keeps_page_instead_of_covering_it(app, isolated_store):
    """从托盘回来、切回首页时不再铺白色过渡页：概览已经在屏幕上，盖一层只会整页闪白。"""
    from PyQt5.QtGui import QShowEvent

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


def test_save_config_keeps_workspace_dirs(app, isolated_store, tmp_path):
    """保存设置不得把上传页加的来源目录清掉。

    设置页只显示第一个目录，旧逻辑在「单值 ≠ 列表首项」时把列表收成单值：
    上传页加的多目录、或移除过首个目录之后，下一次保存设置就会整列被覆盖，
    表现是每次重新打开都要重选文件夹。列表必须以 workspaceDirs 为准。
    """
    from app.services import store, workspace

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    workspace.save_workspace_dirs([str(a), str(b)])

    from app.ui.settings_page import SettingsPage

    page = SettingsPage()
    page.workspace_dir = ""            # 某处没同步过来的旧单值
    page._save_config()
    assert store.load()["workspaceDirs"] == [str(a), str(b)]

    # 用户在设置页亲手选了目录，才收窄成这一个
    page.workspace_dir = str(b)
    page._ws_touched = True
    page._save_config()
    assert store.load()["workspaceDirs"] == [str(b)]


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
    assert page.workspace_dir == "/tmp/ws-new"

    folder = os.path.dirname(store.CONFIG_FILE)
    assert any("before-import" in n for n in os.listdir(folder))


def test_system_backup_covers_every_setting(app, isolated_store, tmp_path, monkeypatch):
    """系统备份必须是全部软件设置的快照，不能只导出支部账号。"""
    import json

    from PyQt5.QtWidgets import QFileDialog

    from app.services import store
    from app.ui.settings_page import SettingsPage

    full = {
        "accounts": [{"userName": "a", "remark": "支部", "password": "p"}],
        "rosters": {"2026秋": "/tmp/r.xlsx"},
        "themeMode": "dark",
        "translucentMaterial": "acrylic",
        "headerImage": "/tmp/banner.png",
        "notifyMode": "interval",
        "notifyTime": "09:30",
        "notifyInterval": 6,
        "workspaceDir": "/tmp/ws",
        "workspaceDirs": ["/tmp/ws"],
        "expectedPages": 4,
        "orgRule": {"version": 1},
    }
    store.save(full)
    page = SettingsPage()

    out = str(tmp_path / "backup.json")
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **k: (out, ""))
    page._export_config_json()

    with open(out, encoding="utf-8") as f:
        dumped = json.load(f)
    # 每个设置键都原样进备份；accounts 不再是唯一的备份对象
    for key, value in full.items():
        assert dumped.get(key) == value, f"备份漏掉了 {key}"


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


def test_rounded_corners_dispatch_by_platform(app):
    """窗口圆角按平台分派：macOS 与 Win11 有系统圆角，其余平台保持直角。"""
    import sys

    from app.services.materials import (
        WINDOWS_MICA_BUILD,
        clear_corners,
        set_corners,
        win11_build,
    )

    widget = QWidget()
    widget.resize(400, 300)

    # 统一入口在当前平台上的预期结果
    if sys.platform == "darwin":
        assert set_corners(widget, 10) is True
    elif sys.platform == "win32":
        assert set_corners(widget, 10) is (win11_build() >= WINDOWS_MICA_BUILD)
    else:
        assert set_corners(widget, 10) is False

    assert widget.mask().isEmpty(), "任何平台都不该再用遮罩切圆角"


def test_corner_setting_is_idempotent(app, monkeypatch):
    """同尺寸重复调用不再写 DWM 圆角偏好。"""
    from app.services import materials

    widget = QWidget()
    widget.resize(640, 480)

    calls = []
    monkeypatch.setattr(materials, "apply_dwm_round_corners",
                        lambda w: calls.append("dwm") or True)

    materials.clear_corners(widget)
    materials.set_corners(widget, 10, platform="win32", build=22631)
    assert calls == ["dwm"]

    # 同尺寸再调三次：不应再碰系统接口
    for _ in range(3):
        materials.set_corners(widget, 10, platform="win32", build=22631)
    assert calls == ["dwm"], f"同尺寸重复切圆角了：{calls}"

    # 尺寸变了才应该重切
    widget.resize(700, 500)
    materials.set_corners(widget, 10, platform="win32", build=22631)
    assert calls == ["dwm", "dwm"]

    # Win10 不设圆角，也不产生任何系统调用
    calls.clear()
    materials.clear_corners(widget)
    assert materials.set_corners(widget, 10, platform="win32", build=19045) is False
    assert calls == []


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


def test_status_row_click_opens_without_signal_error(app):
    """点一行不该崩：父类 CardWidget 的 clicked 是无参信号。

    StatusRow 曾经把 clicked 重定义成 Signal(str)，父类 mouseReleaseEvent 里
    `clicked.emit()` 少传一个参数，点任何一行都抛 TypeError。
    """
    from PyQt5.QtCore import QEvent, QPointF, Qt
    from PyQt5.QtGui import QMouseEvent

    from app.ui.widgets import StatusRow

    row = StatusRow({"title": "张三", "path": "/tmp/a.pdf",
                     "status": "可上传", "tone": "ok"})
    got = []
    row.opened.connect(got.append)

    def mouse(kind, buttons, held):
        return QMouseEvent(kind, QPointF(6, 6), QPointF(0, 0),
                           buttons, held, Qt.KeyboardModifier.NoModifier)

    row.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease,
                               Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    row.mousePressEvent(mouse(QEvent.Type.MouseButtonPress,
                             Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    assert got == ["/tmp/a.pdf"]
    row.deleteLater()


def test_preview_zoom_scales_and_scrolls(app):
    """预览支持缩放：放大后图片变大、出现滚动范围，缩回 100% 正好整页。"""
    from PyQt5.QtGui import QColor, QPixmap
    from PyQt5.QtWidgets import QVBoxLayout, QWidget

    from app.ui.archive_preview import _MAX_ZOOM, ArchivePreviewView

    host = QWidget()
    host.resize(900, 640)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    view = ArchivePreviewView(host, "/nonexistent.pdf", "x.pdf", expected_pages=13)
    view.stop_workers()
    lay.addWidget(view)
    host.show()
    app.processEvents()
    view._full_pixmap = QPixmap(1200, 1600)
    view._full_pixmap.fill(QColor("white"))
    view._render_display()
    app.processEvents()

    fit_w = view.preview_lbl.pixmap().width()
    base_max = view.scroll.verticalScrollBar().maximum()
    assert view.zoom_label.text() == "100%"

    view.set_zoom(2.0)
    app.processEvents()
    assert view.preview_lbl.pixmap().width() >= fit_w * 1.9
    # 放大后内容超出视口，滚动范围必须跟着变大（否则放大等于看不见下半页）
    assert view.scroll.verticalScrollBar().maximum() > base_max
    assert view.zoom_label.text() == "200%"

    view.set_zoom(99)
    assert view._zoom == _MAX_ZOOM
    assert not view.zoom_in_btn.isEnabled()
    view.set_zoom(1.0)
    app.processEvents()
    assert view.preview_lbl.pixmap().width() == fit_w
    assert view.scroll.verticalScrollBar().maximum() == base_max
    view.deleteLater()
    host.deleteLater()


def test_archive_preview_has_crumb_and_back(app, tmp_path):
    """档案详情是上传页内的子页面：面包屑负责返回，不另开窗口。"""
    from PyQt5.QtWidgets import QWidget

    from app.ui.archive_preview import ArchivePreviewView

    pdf = tmp_path / "张三+13800000000+入团志愿书.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    back = []
    host = QWidget()
    view = ArchivePreviewView(host, str(pdf), pdf.name, expected_pages=13,
                              on_back=lambda: back.append(1))
    view.stop_workers()
    assert hasattr(view, "crumb"), "子页面必须有面包屑导航"
    assert view.crumb.itemMap.get("upload") is not None
    view._on_crumb("upload")
    assert back == [1]
    view.deleteLater()
    host.deleteLater()


@pytest.mark.skipif(
    sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen"),
    reason="无边框窗口要真实 NSWindow，离屏下 qframelesswindow 的原生钩子会崩",
)
def test_browser_window_shell_builds(app, isolated_store):
    """账号网页窗口外壳：标签条接管标题栏，没有标签时停在欢迎页。"""
    from app.ui.browser_page import (TAB_HEIGHT, TAB_TOP, TITLE_BAR_HEIGHT,
                                     BrowserWindow)

    win = BrowserWindow()
    assert win.tab_count() == 0
    # 标签行上沿让出 TAB_TOP，行内 32px 的按钮中线才落在系统按钮那条线上；
    # 标题栏再比标签行高一截，页面不会贴在标签条下面
    assert win.tab_row.height() == TAB_HEIGHT + TAB_TOP
    assert win.titleBar.height() == TITLE_BAR_HEIGHT
    assert win.titleBar.height() > win.tab_row.height()
    plus = win.tab_bar.addButton.mapTo(win, win.tab_bar.addButton.rect().center()).y()
    assert plus == TAB_TOP + TAB_HEIGHT // 2
    assert not win.back_btn.isEnabled()
    assert win.empty.isVisible()  # 没有标签时只有欢迎页
    # 继承来的空 stackedWidget 会停在窗口左上角画一块不透明底，必须连同原生
    # 视图一起收起来：光 hide() 不够，窗口重新显示后它还会冒出来
    assert win.stackedWidget.isHidden()
    assert win.stackedWidget.width() == 0 and win.stackedWidget.height() == 0
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


def test_about_card_exports_logs_to_chosen_path(app, isolated_store, monkeypatch, tmp_path):
    """导出日志按钮就在反馈旁边，路径由用户选，内容交给 app_log 汇总。"""
    from PyQt5.QtWidgets import QFileDialog

    from app.services import app_log
    from app.ui.about_card import AboutCard
    from app.ui.page import CONTROL_WIDTH

    dest = tmp_path / "crash-report.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(dest), "文本文件 (*.txt)"))
    calls = []
    monkeypatch.setattr(app_log, "export_logs", lambda path: calls.append(path) or 2)

    card = AboutCard()
    buttons = [b for b in card.findChildren(type(card.check_btn)) if b.text() in ("导出日志", "提交反馈")]
    assert [b.text() for b in buttons] == ["导出日志", "提交反馈"]
    assert all(b.width() == CONTROL_WIDTH for b in buttons)
    export_btn = next(b for b in buttons if b.text() == "导出日志")
    export_btn.click()
    assert calls == [str(dest)]


def _pump(app, ms: int = 60):
    """跑一小段事件循环：分批建卡与淡入淡出都靠事件驱动，需要让它走完。"""
    from PyQt5.QtCore import QEventLoop, QTimer

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


def test_messages_first_show_uses_loading_page(app, isolated_store, monkeypatch):
    """系统消息首次显示先铺加载页，列表分批建完再收；失败路径也不能卡在加载态。"""
    from PyQt5.QtGui import QShowEvent

    from app.ui.messages_page import MessagesPage
    from app.ui.page import PageBanner

    page = MessagesPage(FakeWindow())
    page.resize(800, 600)
    monkeypatch.setattr(page, "_load_cache", lambda: None)
    monkeypatch.setattr(page, "auto_fetch", lambda: None)

    page.showEvent(QShowEvent())
    busy = page._busy
    assert busy is not None and not busy.isHidden()
    assert busy.geometry().y() == PageBanner.HEIGHT  # 只盖头图下面的内容区

    page._on_cache_loaded(_payload(3))
    _pump(app, 80)
    assert page._initial_loaded is True
    assert busy.isHidden()

    page._show_busy()
    page._on_cache_failed("缓存读不了")
    assert busy.isHidden()


def test_messages_reopen_does_not_repeat_loading_page(app, isolated_store, monkeypatch):
    from PyQt5.QtGui import QShowEvent

    from app.ui.messages_page import MessagesPage

    page = MessagesPage(FakeWindow())
    page._initial_loaded = True
    monkeypatch.setattr(page, "_load_cache", lambda: None)
    monkeypatch.setattr(page, "auto_fetch", lambda: None)
    page.showEvent(QShowEvent())
    assert page._busy is None or page._busy.isHidden()


def test_message_load_worker_emits_payload(app, isolated_store):
    from app.ui.messages_page import MessageLoadWorker

    captured = {}
    worker = MessageLoadWorker()
    worker.loaded.connect(lambda p: captured.update(p))
    worker.run()  # 直接在测试线程里跑一次，不需要真开线程
    assert set(captured) == {"digest", "cache", "read", "memberCache"}
    assert captured["cache"] == {}


def test_repeated_expand_restores_layout(app, isolated_store):
    """快速连点展开箭头后，卡片和子控件必须回到原来的高度。

    动画把 body 的 maximumHeight 压到中间值时，QVBoxLayout 会顺手把子控件挤矮。
    旧实现结束时只恢复 maximumHeight，外层布局仍拿着动画中途的尺寸提示，
    结果卡片越点越矮、下面的卡片跟着上移。这里用真实设置页那种不等高的行来锁住。
    """
    from PyQt5.QtCore import QSize
    from PyQt5.QtWidgets import QVBoxLayout

    from app.ui.anim import animate_expand

    class SizedRow(QWidget):
        def __init__(self, height: int):
            super().__init__()
            self._h = height

        def sizeHint(self):
            return QSize(200, self._h)

    card = QWidget()
    outer = QVBoxLayout(card)
    outer.setContentsMargins(20, 12, 20, 12)
    outer.setSpacing(8)
    head = QWidget()
    head.setFixedHeight(49)
    outer.addWidget(head)

    body = QWidget()
    body_lay = QVBoxLayout(body)
    body_lay.setContentsMargins(0, 0, 0, 0)
    body_lay.setSpacing(0)
    for h in (63, 63, 257, 80, 63):
        body_lay.addWidget(SizedRow(h))
    outer.addWidget(body)
    body.hide()
    host = QWidget()
    host_lay = QVBoxLayout(host)
    host_lay.setContentsMargins(0, 0, 0, 0)
    host_lay.addWidget(card)
    host_lay.addStretch(1)
    host.resize(500, 300)
    host.show()
    app.processEvents()
    base_height = card.height()
    base_row_heights = [body_lay.itemAt(i).widget().sizeHint().height()
                        for i in range(body_lay.count())]
    assert base_height == 73

    for show in (True, False) * 4:
        animate_expand(body, show, duration=40)
        app.processEvents()
    _pump(app, 160)

    assert body.isHidden()
    assert card.height() == base_height
    assert [body_lay.itemAt(i).widget().height() for i in range(body_lay.count())] == [0] * 5
    for i, h in enumerate(base_row_heights):
        assert body_lay.itemAt(i).widget().sizeHint().height() == h


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


def test_branch_picker_lists_only_normal_accounts(app):
    """只列状态正常的账号，并在弹窗里说明隐藏了多少异常账号。"""
    from PyQt5.QtWidgets import QWidget

    from app.ui.branch_picker import BranchPickerDialog, pick_branch

    host = QWidget()
    host.resize(600, 400)
    accounts = [
        {"userName": "a", "remark": "高三一班", "health": {"status": "captcha"}},
        {"userName": "b", "remark": "高二二班", "health": {"status": "ok"}},
    ]
    dlg = BranchPickerDialog(accounts, host)
    assert dlg.combo.count() == 1
    assert dlg.combo.itemText(0) == "高二二班（b）"
    from qfluentwidgets import CaptionLabel

    assert any("已隐藏 1 个异常账号" in lbl.text()
               for lbl in dlg.widget.findChildren(CaptionLabel))
    assert pick_branch(accounts[:1], None, lambda user: None) is None


def test_mirror_combo_lists_sites_and_defaults_to_auto(app, isolated_store):
    """镜像源：自动 → 各站 → 直连 → 自定义；默认自动，选中即写配置。"""
    from app.services import update_check, updater
    from app.ui.mirror_combo import AUTO_TEXT, CUSTOM_TEXT, MirrorComboBox

    combo = MirrorComboBox()
    assert combo.count() == len(updater.MIRRORS) + 3
    assert combo.itemText(0) == AUTO_TEXT
    assert combo.itemData(0) == updater.AUTO
    assert combo.itemText(combo.count() - 1) == CUSTOM_TEXT
    # 默认自动选择，收起状态也只显示「自动选择」（延迟只在列表里）
    assert combo.mode() == updater.AUTO
    assert combo.text() == AUTO_TEXT

    index = combo._index_of("gh.zwy.one")
    combo.setCurrentIndex(index)
    assert combo.mode() == "gh.zwy.one"
    assert update_check.mirror_mode() == "gh.zwy.one"
    assert combo.text() == "gh.zwy.one"

    # 测速结果回填成「站名 (ms)」，收起状态的文案不带延迟
    combo._on_measured("gh.zwy.one", 845)
    assert combo.itemText(index) == "gh.zwy.one (845 ms)"
    assert combo.text() == "gh.zwy.one"
    combo._on_unavailable("wget.la", "timed out")
    assert combo.itemText(combo._index_of("wget.la")) == "wget.la (超时)"
    combo.deleteLater()


def test_notice_rows_are_compact(app):
    """公告行单行排：折行会把行高翻倍，看着就是「行距太宽」。"""
    from app.services.notices import Notice
    from app.ui.notice_panel import NoticePanel, _NoticeRow

    panel = NoticePanel()
    panel.resize(900, 600)
    panel.show()
    panel.set_notices([
        Notice(key=f"k{i}", title=f"账号 初中部初三{i}班：登录冷却",
               detail="上次登录要求验证码（密码失败过多），约 59 分钟后再试",
               action=f"accounts:{i}", action_text="去设置", tone="error")
        for i in range(3)
    ])
    panel._expanded = True
    panel._body.setVisible(True)
    panel._sync_height()
    app.processEvents()

    rows = panel.findChildren(_NoticeRow)
    assert rows and all(row.height() <= 46 for row in rows)
    assert all(not row.detail_lbl.wordWrap() for row in rows)


def test_messages_content_digest_tracks_changes(app):
    """列表只按「账号 + 消息号 + 时间」比摘要：一样就不重建，避免每 30 秒闪一次。"""
    from types import SimpleNamespace

    from app.ui.messages_page import MessagesPage

    def view(account, rid, when):
        return SimpleNamespace(account=account, receive_id=rid, time_str=when)

    a = [view("u1", 1, "2026-11-16 10:00"), view("u1", 2, "2026-11-16 11:00")]
    assert MessagesPage._content_digest(a) == MessagesPage._content_digest(list(a))
    assert MessagesPage._content_digest(a) != MessagesPage._content_digest(
        [view("u1", 1, "2026-11-16 10:00"), view("u1", 3, "2026-11-16 11:00")])


def test_browser_drag_hands_off_to_system(app, monkeypatch):
    """标题栏与标签条空白处的拖动交给系统接管，不自己算位移。

    自绘拖动（记录按下点、移动时 move 窗口）能避开 macOS 那条
    “Window move completed without beginning” 日志，但会丢掉系统侧行为：
    多屏不同缩放的换算、窗口吸附、触控板手势。所以改回系统接管——
    断言「按下去就是发起一次系统拖动」，而不是「窗口位置被自己改了」。
    """
    from PyQt5.QtCore import QPoint, Qt
    from PyQt5.QtWidgets import QWidget

    from app.ui import browser_page

    calls = []
    monkeypatch.setattr(browser_page, "_start_system_move",
                        lambda host, pos: calls.append((host, pos)) or True)

    # ChromeRow：按在空白处就发起系统拖动
    row = browser_page.ChromeRow()
    row.mousePressEvent(type("E", (), {
        "button": lambda self: Qt.MouseButton.LeftButton,
        "pos": lambda self: QPoint(400, 10),
        "globalPos": lambda self: QPoint(200, 120),
    })())
    assert len(calls) == 1, "标签条空白处没有发起系统拖动"

    # 自绘拖动类必须已经移除，别再偷偷回退
    assert not hasattr(browser_page, "_WindowDragger")


def test_browser_tab_click_does_not_start_drag(app, monkeypatch):
    """TabBar 的标签项会把按下事件转发给父级；落在标签段上的按下必须被
    ChromeRow 豁免，否则点标签同时发起窗口拖动，标签就点不动了。"""
    from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
    from PyQt5.QtGui import QMouseEvent

    from app.ui import browser_page

    calls = []
    monkeypatch.setattr(browser_page, "_start_system_move",
                        lambda host, pos: calls.append(1) or True)
    row = browser_page.ChromeRow()
    row.setFixedSize(600, 38)
    bar = browser_page.BranchTabBar(row)
    bar.add_tab("ssfz1", "普通高中高三十一班")
    row.add_no_drag(bar)
    row.show()
    app.processEvents()

    item = bar.tabItem(0)
    on_tab = item.mapTo(row, QPoint(item.width() // 2, item.height() // 2))

    def press(pos):
        return QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(pos), QPointF(row.mapToGlobal(pos)),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)

    row.mousePressEvent(press(on_tab))
    assert calls == [], "按在标签上不应发起窗口拖动"

    blank = QPoint(560, 10)   # 标签段之外的空白
    row.mousePressEvent(press(blank))
    assert calls == [1], "标签段之外的空白仍应能拖动窗口"
    row.deleteLater()


def test_tab_bar_fills_row_and_keeps_add_button(app):
    """标签条必须吃满可用宽度。

    只按 sizeHint 给宽度（AlignLeft、不给 stretch）时，标签会被压到内容宽、
    内置的 ＋ 按钮直接被挤出可见区——用户看到的就是「标签渲染不全、没有加号」。
    """
    from PyQt5.QtWidgets import QHBoxLayout

    from app.ui.browser_page import TAB_HEIGHT, BranchTabBar, ChromeRow

    row = ChromeRow()
    row.setFixedSize(1000, TAB_HEIGHT + 4)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(10, 4, 88, 0)
    lay.setSpacing(4)
    bar = BranchTabBar(row)
    bar.add_tab("ssfz1", "普通高中高一1班")
    lay.addWidget(bar, 1)
    row.show()
    app.processEvents()

    assert bar.width() > 600, "标签条没有占满标题栏中间的空白"
    assert bar.tabItem(0).width() == BranchTabBar.MAX_WIDTH, "标签没有被放宽到最大档"
    assert bar.addButton.isVisible(), "＋ 按钮被挤出可见区域"
    assert bar.addButton.x() + bar.addButton.width() <= bar.width()
    row.deleteLater()


def test_browser_drag_ignores_non_left_button(app, monkeypatch):
    """右键不触发窗口拖动，否则右键菜单一按窗口就跟着跑。"""
    from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
    from PyQt5.QtGui import QMouseEvent

    from app.ui import browser_page

    calls = []
    monkeypatch.setattr(browser_page, "_start_system_move",
                        lambda host, pos: calls.append(1) or True)
    row = browser_page.ChromeRow()
    event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(1, 1), QPointF(1, 1),
                        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
                        Qt.KeyboardModifier.NoModifier)
    row.mousePressEvent(event)
    assert calls == []


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


def test_settings_rows_keep_card_height(app, isolated_store):
    """标题与说明贴紧 2px；卡片行高靠上下留白撑起，两者互不牵连。"""
    from app.ui.page import CollapsibleCard, SettingRow
    from app.ui.settings_page import SettingsPage

    page = SettingsPage()
    page.resize(1000, 1800)
    page.show()
    app.processEvents()
    rows = [r for r in page.findChildren(SettingRow) if r.isVisible()]
    assert rows
    assert all(r.height() >= 58 for r in rows), [r.height() for r in rows]
    assert all(r._text_lay.spacing() == 2 for r in rows)

    # 卡片头部「外观设置」与其小字说明共用一组布局，间距同样是 2px。
    cards = [c for c in page.findChildren(CollapsibleCard)
             if c.title_label.text() in ("上传规则", "外观设置", "工作与通知")]
    assert cards
    assert all(c._head_lay.spacing() == 2 for c in cards)
    assert all(c.desc_label.geometry().top() - c.title_label.geometry().bottom() <= 3
               for c in cards)


def test_settings_controls_share_one_width(app, isolated_store):
    """行内控件统一 126px：按钮 106、下拉 125、下拉框 76 各取宽会左右都对不齐。"""
    from app.ui.about_card import AboutCard
    from app.ui.page import CONTROL_MAX_WIDTH, CONTROL_WIDTH
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

    for widget in (page.web_btn, page.file_btn, page.theme_combo,
                   page.expected_pages_edit, page.notify_more_btn,
                   card.check_btn):
        assert widget.width() == CONTROL_WIDTH


def test_settings_rows_all_have_desc(app, isolated_store):
    """每行标题下都要有一句小字：空着的那几行全是用户问「这里没有说明」的地方。"""
    from qfluentwidgets import StrongBodyLabel

    from app.ui.page import SettingRow
    from app.ui.settings_page import SettingsPage

    page = SettingsPage()
    page.resize(1000, 1800)
    page.show()
    app.processEvents()
    missing = []
    for row in page.findChildren(SettingRow):
        desc = getattr(row, "desc_label", None)
        if desc is None or not desc.text().strip():
            labels = row.findChildren(StrongBodyLabel)
            missing.append(labels[0].text() if labels else "?")
    assert missing == []


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


def test_fluent_semantic_roles_have_light_and_dark_values(app):
    """语义色按 Fluent 2 给两套值；异常/警告/成功/信息取色随主题切换。"""
    from qfluentwidgets import Theme, setTheme

    from app.ui import palette as P

    # 锁语义约束而不是具体 hex：色值会随 Fluent 2 校准调整，
    # 但「每档都有明暗两套、深色更亮」这个结构不能变。
    for role in ("danger", "warning", "success", "info", "brand"):
        light, dark = P.pair(role)
        assert light.startswith("#") and len(light) == 7, role
        assert dark.startswith("#") and len(dark) == 7, role
        assert light != dark, f"{role} 明暗两档不能相同"
    # 深色档比浅色档亮（深色底上才读得清）
    for role in ("danger", "warning", "success", "info", "brand", "fees", "fg2"):
        from PyQt5.QtGui import QColor
        light, dark = (QColor(v) for v in P.pair(role))
        assert dark.lightness() > light.lightness(), role

    try:
        light_hex, dark_hex = P.pair("danger")

        setTheme(Theme.LIGHT)
        assert P.fg("danger").name().lower() == light_hex.lower()
        assert P.solid("danger") == light_hex
        light_pair = P.text_colors("danger")
        assert light_pair[0].name().lower() == light_hex.lower()

        setTheme(Theme.DARK)
        assert P.fg("danger").name().lower() == dark_hex.lower()
        assert P.solid("danger") == dark_hex
    finally:
        setTheme(Theme.DARK)


def test_health_roles_resolve_and_no_hardcoded_status_hex(app, isolated_store):
    """健康状态只存角色名；页面里不再写死旧的状态 hex。"""
    from pathlib import Path

    from app.domain.account_health import HEALTH_ROLE
    from app.ui import palette as P

    assert HEALTH_ROLE["ok"] == "success"
    assert HEALTH_ROLE["failed"] == "danger"
    assert HEALTH_ROLE["captcha"] == "warning"
    assert HEALTH_ROLE["unknown"] == "muted"
    assert all(role in P.ROLES for role in HEALTH_ROLE.values())

    root = Path(__file__).resolve().parent.parent / "app" / "ui"
    old = ("#dc2626", "#16a34a", "#b45309", "#f87171", "#ff99a4",
           "#c42b1c", "#8ab4ff", "#b9bcc4")
    offenders = []
    for path in root.glob("*.py"):
        if path.name == "palette.py":      # 色值就定义在这里
            continue
        low = path.read_text(encoding="utf-8").lower()
        for hexv in old:
            if hexv in low:
                offenders.append(f"{path.name}:{hexv}")
    assert offenders == []


def test_notice_rows_keep_default_text_color(app, isolated_store):
    """公告面板用背景传达等级，标题不再单独染成红字。"""
    from pathlib import Path

    from app.services.notices import Notice
    from app.ui.notice_panel import _role_of

    source = (Path(__file__).resolve().parent.parent
              / "app" / "ui" / "notice_panel.py").read_text(encoding="utf-8")
    assert "setTextColor" not in source
    assert _role_of(Notice(key="a", title="", tone="error")) == "danger"
    assert _role_of(Notice(key="b", title="", tone="warning")) == "warning"
    assert _role_of(Notice(key="c", title="", tone="info")) == "info"
    assert _role_of(Notice(key="d", title="")) == "warning"


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
    from PyQt5.QtCore import QMimeData, QUrl

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
    from PyQt5.QtWidgets import QVBoxLayout

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
    from PyQt5.QtCore import QTimer

    original = QTimer.singleShot

    def _single(msec, callback, *args, **kwargs):
        if getattr(callback, "__self__", None) is card_holder.get("card"):
            callback(*args, **kwargs)
            return
        return original(msec, callback, *args, **kwargs)

    monkeypatch.setattr(QTimer, "singleShot", _single)


def test_download_starts_silent_install(app, isolated_store, tmp_path, monkeypatch):
    """下载完就交给独立进程静默安装，不再弹出安装窗口。"""
    from PyQt5.QtWidgets import QVBoxLayout

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
    from PyQt5.QtWidgets import QVBoxLayout

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
    from PyQt5.QtWidgets import QVBoxLayout

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
    from PyQt5.QtWidgets import QVBoxLayout

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
    from PyQt5.QtWidgets import QApplication

    from qfluentwidgets import ComboBox

    from app.ui.settings_page import SettingsPage

    page = SettingsPage()
    # isolated_store 默认 notifyInterval=4
    assert page._notify_interval_hours == 4

    captured = []
    orig = page._config_dialog
    page._config_dialog = lambda *a, **k: captured.append(orig(*a, **k)) or captured[-1]

    page._open_notify_dialog()
    dlg = captured[-1]
    combo = dlg.findChild(ComboBox)
    assert combo is not None
    assert not combo.isWindow()  # 弹窗里的控件不能是独立顶层窗口

    idx = combo.findData(2)
    combo.setCurrentIndex(idx)
    dlg.accept()
    QApplication.processEvents()  # 让 deleteLater / 回调落地
    # 值已写回持久属性并落盘
    assert page._notify_interval_hours == 2

    # 再次打开：又是一批全新控件，且读到的仍是上次写回的 2 小时
    page._open_notify_dialog()
    dlg2 = captured[-1]
    combo2 = dlg2.findChild(ComboBox)
    assert combo2 is not None and not combo2.isWindow()
    assert page._notify_interval_hours == 2
    dlg2.reject()


def test_settings_has_no_orphan_top_level_widgets(app, isolated_store):
    """设置页不能留下无父控件：Qt 会把它们当独立窗口，一 setVisible 就弹出来。

    回归：notify_time_edit（默认 08:00 的输入框）与 notify_interval（每 N 小时
    下拉框）曾既没进布局，又被按通知模式 setVisible——「每天」弹出前者，
    「按间隔」弹出后者；ws_edit（工作目录框）也是隐藏的游离窗口。现在三者
    都只是普通值属性。"""
    from PyQt5.QtWidgets import QApplication, QMenu

    from app.ui.settings_page import SettingsPage

    before = set(app.allWidgets())
    page = SettingsPage()
    page.show()
    app.processEvents()
    created = lambda: [w for w in app.allWidgets() if w not in before]
    orphans = lambda: [w for w in created()
                       if w.isWindow() and w.parentWidget() is None
                       and w is not page and not isinstance(w, QMenu)]

    assert orphans() == [], f"设置页留下无父控件: {orphans()}"

    for mode in ("daily", "interval", "off"):
        idx = page.notify_mode.findData(mode)
        if idx < 0:
            continue
        page.notify_mode.setCurrentIndex(idx)
        app.processEvents()
        assert orphans() == [], f"{mode} 模式留下无父控件: {orphans()}"


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


def test_org_manage_detail_drops_page_banner(app, isolated_store):
    """支部详情不要头图：进详情收起 banner、顶部留白收回常规边距，并让出面包屑那一行；
    返回列表时头图与留白一起复原。"""
    from app.domain.org_manage import BranchNode
    from app.ui.org_manage_page import OrgManagePage
    from app.ui.page import PageBanner

    page = OrgManagePage(FakeWindow())
    page._branches = [BranchNode(code="ssfz12407", label="普通高中高三七班")]

    page._open_branch("ssfz12407", animate=False)
    assert page._banner_on is False
    assert page.header.isHidden()
    assert page.vBoxLayout.contentsMargins().top() == 16
    assert not page.crumb_host.isHidden()

    page.back_to_feed()
    assert page._banner_on is True
    assert page.vBoxLayout.contentsMargins().top() == PageBanner.HEIGHT + 16
    assert page.crumb_host.isHidden()


def test_branch_status_is_exclusive_and_fees_priority(app, isolated_store):
    """状态判定互斥：无系统数据→none，有其它异常→异常（压过欠缴），
    纯欠缴→欠缴，都没有→正常。异常里的 kind 用 report_kind 细分后的键。"""
    from app.domain.org_manage import branch_status

    def stats(system, identity=0, report=0, archive=0, fees=0):
        return {"system": system,
                "by_kind": {"identity": identity, "report": report,
                            "archive": archive, "fees": fees}}

    assert branch_status(stats(0)) == "none"
    assert branch_status(stats(10)) == "normal"
    assert branch_status(stats(10, fees=3)) == "fees"
    assert branch_status(stats(10, identity=1)) == "abnormal"
    assert branch_status(stats(10, report=2)) == "abnormal"
    assert branch_status(stats(10, archive=1)) == "abnormal"
    # 异常优先于欠缴：同时有时只算异常，不会在两个筛选里重复出现
    assert branch_status(stats(10, identity=1, fees=3)) == "abnormal"


def test_org_manage_status_filter_rebuilds_matching_branches(app, isolated_store):
    """状态下拉按互斥分类筛支部，卡片色条与筛选同源。"""
    from app.domain.org_manage import BranchNode, MemberView
    from app.ui.org_manage_page import OrgManagePage

    page = OrgManagePage(FakeWindow())
    def branch(code, label):
        return BranchNode(code=code, label=label,
                          members=[MemberView("团员", "13800000000")])
    nodes = [
        branch("a", "正常支部"),
        branch("b", "欠缴支部"),
        branch("c", "异常支部"),
        branch("d", "又异常又欠缴"),
    ]
    page._branches = nodes
    def stats(identity=0, fees=0):
        return {"total": 5, "system": 5, "abnormal": identity + fees,
                "by_kind": {"identity": identity, "report": 0, "archive": 0, "fees": fees},
                "unpaid_amount": 0.0}
    page._stats_cache = {
        "a": stats(),
        "b": stats(fees=2),
        "c": stats(identity=1),
        "d": stats(identity=1, fees=4),
    }
    assert [page.status_combo.itemText(i) for i in range(page.status_combo.count())] == [
        "全部支部", "正常", "异常", "欠缴"]

    expected = {"all": 4, "normal": 1, "abnormal": 2, "fees": 1}
    for key, n in expected.items():
        page.status_combo.setCurrentIndex(page.status_combo.findData(key))
        page._rebuild()
        import time
        end = time.monotonic() + 2
        while page.feed_lay.count() < n and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert page.feed_lay.count() == n, (key, page.feed_lay.count(), n)

    # 详情态收起筛选控件
    page._set_toolbar(True)
    assert page.status_combo.isHidden()
    page._set_toolbar(False)
    assert not page.status_combo.isHidden()


def test_org_manage_first_show_uses_loading_page(app, isolated_store, monkeypatch):
    """组织管理首次显示先铺加载页，支部卡片分批建完才收，失败也会撤掉。"""
    import time

    from PyQt5.QtGui import QShowEvent

    from app.domain.org_manage import BranchNode
    from app.ui.org_manage_page import OrgManagePage
    from app.ui.page import PageBanner

    page = OrgManagePage(FakeWindow())
    page.resize(800, 600)
    monkeypatch.setattr(page, "refresh", lambda: None)

    page.showEvent(QShowEvent())
    busy = page._busy
    assert busy is not None and not busy.isHidden()
    assert busy.geometry().y() == PageBanner.HEIGHT

    page._apply_refresh(([], {}))
    assert busy.isHidden()

    page._branches = [BranchNode(code="x", label="示例支部")]
    page._refreshed_at = time.monotonic()
    page.showEvent(QShowEvent())  # 已有树：再切回来不重复铺加载页
    assert busy.isHidden()


def test_org_manage_loading_page_hides_on_failure(app, isolated_store, monkeypatch):
    from PyQt5.QtCore import QObject, QTimer, pyqtSignal as Signal
    from PyQt5.QtGui import QShowEvent

    from app.ui import page as page_mod
    from app.ui.org_manage_page import OrgManagePage

    class FailingWorker(QObject):
        ok = Signal(object)
        fail = Signal(str)

        def __init__(self, _fn, parent=None):
            super().__init__(parent)
            QTimer.singleShot(0, lambda: self.fail.emit("后台刷新失败"))

        def start(self):
            pass

    monkeypatch.setattr(page_mod, "CallbackWorker", FailingWorker)
    page = OrgManagePage(FakeWindow())
    page.resize(800, 600)
    page.showEvent(QShowEvent())
    busy = page._busy
    assert busy is not None and not busy.isHidden()

    _pump(app, 40)
    assert busy.isHidden()


def test_org_page_has_no_save_button_and_runs_hourly(app, isolated_store):
    from app.ui.org_page import OrgPage

    page = OrgPage(FakeWindow())
    assert not hasattr(page, "export_btn")
    assert page._auto_timer.interval() == 60 * 60 * 1000
    assert page._auto_timer.isActive()


def test_org_page_statistics_cover_layout_changes(app, isolated_store):
    """手动统计时逐条插入异常卡片，页面高度会被撑得一跳一跳；加载页盖到收尾。"""
    from app.ui.org_page import OrgPage
    from app.ui.page import PageBanner

    page = OrgPage(FakeWindow())
    page.resize(800, 600)
    page.show()
    page._snapshot = (0, 0, {}, 0, "", [])
    page._show_busy()
    busy = page._busy
    assert busy is not None and not busy.isHidden()
    assert busy.geometry().y() == PageBanner.HEIGHT

    page._ok_accounts = 1
    for i in range(6):
        page._append_card(f"团员{i}", "13800000000", [], f"问题 {i}", "示例支部")
    page._on_run_done()
    _pump(app, 40)
    assert busy.isHidden()


def test_member_card_click_emits(app, isolated_store):
    """错误团员卡片点击发射信号，交给页面做跳转。"""
    from PyQt5.QtCore import QPoint, Qt
    from PyQt5.QtTest import QTest

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


def test_tint_roles_have_light_and_dark_backgrounds(app):
    """提示卡片底色按 Fluent 2 明暗各一套，异常用黄、硬失败才用红。"""
    from qfluentwidgets import Theme, setTheme

    from app.ui import palette as P

    warn_light, warn_dark = P.pair("warning")
    assert warn_light != warn_dark
    assert P.TINT["warning"] == ("#FFF4CE", "#463414")
    assert P.TINT["danger"] == ("#FDE7E9", "#442324")
    try:
        setTheme(Theme.LIGHT)
        assert P.tint("warning").name() == "#fff4ce"
        assert P.tint("warning").alpha() == P.TINT_ALPHA[0]
        setTheme(Theme.DARK)
        assert P.tint("warning").name() == "#463414"
        assert P.tint("warning").alpha() == P.TINT_ALPHA[1]
        assert P.tint("warning", 99).alpha() == 99
    finally:
        setTheme(Theme.DARK)


def test_branch_card_abnormal_uses_warning_background(app, isolated_store):
    """支部卡片异常态用黄底，角色由 branch_status 与筛选共用。"""
    from app.domain.org_manage import BranchNode, MemberView
    from app.ui.org_manage_page import BranchCard, OrgManagePage

    card = BranchCard("warning")
    assert card.role == "warning"

    page = OrgManagePage(FakeWindow())
    node = BranchNode(code="x", label="异常支部",
                      members=[MemberView("团员", "13800000001")])
    page._stats_cache = {"x": {
        "total": 1, "system": 1, "abnormal": 1,
        "by_kind": {"identity": 0, "report": 1, "archive": 0, "fees": 0},
        "unpaid_amount": 0.0,
    }}
    widget = page._feed_card(node)
    try:
        assert isinstance(widget, BranchCard)
        assert widget.role == "warning"
    finally:
        widget.deleteLater()


def test_tables_bind_fluent_context_menus(app, isolated_store):
    """三张表都挂上 Fluent 右键菜单，且菜单不是系统原生 QMenu。"""
    from PyQt5.QtCore import Qt
    from qfluentwidgets import RoundMenu

    from app.ui.settings_page import SettingsPage

    page = SettingsPage()
    for table in (page.table, page.roster_table, page.sec_table):
        assert table.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
        assert table.customContextMenuRequested is not None
    # 绑定的辅助函数产生的菜单必须是 RoundMenu
    from app.ui.page import bind_table_context_menu
    from PyQt5.QtWidgets import QTableWidget

    probe = QTableWidget(1, 1)
    menus = []
    original = RoundMenu.exec

    def fake_exec(self, *args, **kwargs):
        menus.append(self)
        return None

    RoundMenu.exec = fake_exec
    try:
        from PyQt5.QtCore import QPoint
        from qfluentwidgets import Action, FluentIcon as FIF
        bind_table_context_menu(
            probe, lambda row: [Action(FIF.COPY, "复制")])
        probe.customContextMenuRequested.emit(QPoint(0, 0))
        assert len(menus) == 1
        assert isinstance(menus[0], RoundMenu)
    finally:
        RoundMenu.exec = original
        probe.deleteLater()
        page.deleteLater()


def test_design_doc_records_tinted_prompt_style():
    """DESIGN.md 写明提示卡片用背景色、异常用黄、表格用 Fluent 右键菜单。"""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    text = (root / "DESIGN.md").read_text(encoding="utf-8")
    assert "提示用背景色，不用细色条" in text
    assert "异常是「需要关注」，底色用黄" in text
    assert "右键菜单用 Fluent 控件" in text


def test_account_file_menu_uses_short_labels(app, isolated_store):
    """账号表格下拉菜单用「导入文件 / 下载模版」。"""
    from app.ui.settings_page import SettingsPage

    page = SettingsPage()
    try:
        labels = [a.text() for a in page.file_menu.actions()]
        assert "导入文件" in labels
        assert "下载模版" in labels
        assert "从表格文件中导入" not in labels
        assert "下载账号模板" not in labels
    finally:
        page.deleteLater()


def test_notice_panel_tints_by_severity(app):
    """父卡取子行最高等级底色，每条子行用自己的等级，文字不改色。"""
    from app.services.notices import Notice
    from app.ui.notice_panel import NoticePanel, _NoticeRow

    panel = NoticePanel()
    panel.set_notices([
        Notice(key="a", title="需验证码", tone="warning"),
        Notice(key="b", title="登录冷却", tone="error"),
        Notice(key="c", title="待处理", tone="info"),
    ])
    assert panel._role == "danger"
    roles = {r._notice.title: r._role for r in panel.findChildren(_NoticeRow)}
    assert roles == {"需验证码": "warning", "登录冷却": "danger", "待处理": "info"}
    panel.deleteLater()


def test_branch_card_tints_only_abnormal(app, isolated_store):
    """正常、纯欠缴、无记录的支部保持原背景，只有异常铺 warning 底。"""
    from app.domain.org_manage import BranchNode, MemberView
    from app.ui.org_manage_page import BranchCard, OrgManagePage

    page = OrgManagePage(FakeWindow())
    cases = {
        "abnormal": ({"system": 1, "abnormal": 1,
                      "by_kind": {"identity": 0, "report": 1, "archive": 0, "fees": 0}},
                     "warning"),
        "fees": ({"system": 1, "abnormal": 1,
                  "by_kind": {"identity": 0, "report": 0, "archive": 0, "fees": 1}}, None),
        "normal": ({"system": 1, "abnormal": 0,
                    "by_kind": {"identity": 0, "report": 0, "archive": 0, "fees": 0}}, None),
    }
    try:
        for key, (stats, expected) in cases.items():
            node = BranchNode(code=key, label=key,
                              members=[MemberView("团员", "13800000001")])
            page._stats_cache = {key: {
                "total": 1, "unpaid_amount": 0.0, **stats,
            }}
            card = page._feed_card(node)
            try:
                assert isinstance(card, BranchCard)
                assert card.role == expected, key
            finally:
                card.deleteLater()
    finally:
        page.deleteLater()


def test_browser_screenshot_wiring(app, isolated_store, monkeypatch):
    """浏览器有截图按钮；保存目录固定；提示条带查看图像与打开目录。"""
    import os

    from PyQt5.QtGui import QDesktopServices
    from PyQt5.QtWidgets import QPushButton, QWidget
    from qfluentwidgets import InfoBar

    from app.ui import browser_page
    from app.ui.browser_page import SHOT_DIR, BrowserWindow

    assert SHOT_DIR == os.path.join(
        os.path.dirname(browser_page.CONFIG_FILE), "screenshots")
    # 系统级拖动是那条 "Window move completed without beginning" 的来源
    source = (browser_page.__file__).replace(".pyc", ".py")
    assert "windowHandle().startSystemMove" not in open(source, encoding="utf-8").read()

    host = QWidget()
    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toLocalFile()))
    try:
        BrowserWindow._show_shot_bar(host, "/tmp/shot.png")
        app.processEvents()
        bars = host.findChildren(InfoBar)
        assert bars
        texts = [b.text() for b in bars[0].findChildren(QPushButton)]
        assert "查看图像" in texts and "打开目录" in texts
        for btn in bars[0].findChildren(QPushButton):
            if btn.text() == "打开目录":
                btn.click()
        assert opened == ["/tmp"]
    finally:
        host.deleteLater()


def test_value_animation_stops_when_widget_destroyed(app):
    """控件被销毁后，数值补间不能再回调它。

    页面切走或测试收尾时，520ms 的动画还在跑，而回调闭包抓着已销毁的控件。
    PyQt5 碰到悬空对象直接 abort（没有 Python 异常可捕），表现为整轮测试随机
    段错误。这里显式销毁控件并推进事件循环，进程不崩即通过。
    """
    from app.ui.anim import value_animation

    widget = QWidget()
    seen: list[float] = []
    value_animation(widget, 0.0, 1.0, seen.append, duration=400)
    _pump(app, 80)
    assert seen, "动画在控件存活期间就该有回调"

    widget.deleteLater()
    app.processEvents()
    # 让动画在控件已销毁的状态下继续走完，进程不崩即通过
    _pump(app, 400)


def test_search_box_is_disabled_not_hidden_on_pages_without_search(app, isolated_store):
    """没有搜索功能的页面把搜索框置灰禁用，而不是让它消失。

    整块消失会让标题栏中间的视觉重心随切页左右跳；用户也分不清
    「这页不能搜」和「搜索框坏了」。

    不构造整个 MainWindow：它会拉起同步线程与 WebEngine，在测试进程里
    既慢又容易在收尾时崩。这里用最小替身只验 _sync_search 的判定逻辑。
    """
    from qfluentwidgets import SearchLineEdit

    from app.ui.main_window import MainWindow

    class Fake:
        pass

    host = Fake()
    host.SEARCH_PAGES = MainWindow.SEARCH_PAGES
    host.SEARCH_DISABLED_HINT = MainWindow.SEARCH_DISABLED_HINT
    host.search_edit = SearchLineEdit()
    hides: list[int] = []
    host.search_edit.hide = lambda: hides.append(1)
    host.stackedWidget = Fake()
    host._place_search = lambda: None
    for attr, _hint in MainWindow.SEARCH_PAGES:
        page = Fake()
        page.set_search = lambda text: None
        setattr(host, attr, page)
        host.stackedWidget.currentWidget = lambda p=page: p
        MainWindow._sync_search(host)
        assert host.search_edit.isEnabled(), f"{attr} 支持搜索，不该禁用"
        assert host.search_edit.placeholderText() != MainWindow.SEARCH_DISABLED_HINT

    # 换成一个不在清单里的页面
    other = Fake()
    host.stackedWidget.currentWidget = lambda: other
    MainWindow._sync_search(host)
    assert not host.search_edit.isEnabled(), "没有搜索的页面应禁用搜索框"
    assert host.search_edit.placeholderText() == MainWindow.SEARCH_DISABLED_HINT
    # 关键：禁用但不隐藏——遮住 hide() 看有没有被调用，别比 isHidden()：
    # 没挂父控件的控件本来就算 hidden，那个值分不出「主动藏」和「还没显示」
    assert hides == [], "没有搜索的页面不该把搜索框藏起来"


def test_donut_arc_length_matches_ratio(app):
    """圆环分段的弧长要等于人数占比，且与底环同心。

    这是这个部件真正的契约：读者直接从弧长读比例。之前底环用 drawArc（矩形是
    笔画中心线）、分段用 QPainterPath.arcTo（矩形是弧的边界），两者基准不同，
    彩色段整体向内偏，看着就是环上错位。

    端头样式（平头/圆头）是视觉选择，不在这里锁——两种都能满足「弧长等于占比」。
    """
    import math

    from PyQt5.QtGui import QPixmap

    from app.ui.charts import SegmentedDonut

    YELLOW = (0xEA, 0xB3, 0x08)
    donut = SegmentedDonut()
    # 只放一段，量到的角度就是这一段
    donut.set_data(260, [("信息错误", 26, "#EAB308")])
    donut.set_progress(1.0)
    pm = QPixmap(150, 150)
    pm.fill()
    donut.render(pm)
    img = pm.toImage()

    def is_yellow(x: int, y: int) -> bool:
        c = img.pixelColor(x, y)
        return (c.alpha() > 200
                and abs(c.red() - YELLOW[0]) < 40
                and abs(c.green() - YELLOW[1]) < 40
                and c.blue() < 70)

    cx, cy = img.width() / 2, img.height() / 2
    angles, radii = [], []
    for y in range(img.height()):
        for x in range(img.width()):
            if not is_yellow(x, y):
                continue
            dx, dy = x - cx, cy - y
            angles.append(math.degrees(math.atan2(dy, dx)))
            radii.append(math.hypot(dx, dy))

    assert angles, "应该画出一段黄色圆弧"
    # 一段从 12 点顺时针铺开：角度落在 (90-占比, 90] 区间内
    expected = 360 * 26 / 260          # 36°
    span = max(angles) - min(angles)
    assert abs(span - expected) < 6, f"弧长 {span:.1f}° 与占比 {expected:.0f}° 不符"

    # 同心：所有彩色像素的半径都应落在同一圈上（环宽 14，允许抗锯齿余量）
    assert max(radii) - min(radii) < 18, \
        f"分段径向跨度 {max(radii) - min(radii):.1f}px 过大，可能没贴在同一条圆上"
def test_resize_path_never_touches_window_frame():
    """resizeEvent 的同步路径里不许改窗口 frame。

    `addShadowEffect` 内部是 `DwmExtendFrameIntoClientArea(-1,-1,-1,-1)`，
    它会让 DWM 重算 frame 并发出新的 WM_SIZE；在 resize 回调里调它就成了
    「resize → 改 frame → resize」自激循环——Windows 上表现为启动后 CPU 打满、
    进主界面即闪退。

    只检查真实调用（去掉注释与 docstring），否则解释这条坑的注释本身会把
    测试判红。
    """
    import io
    import inspect
    import tokenize

    from app.ui.main_window import MainWindow

    def calls_only(fn) -> str:
        """去掉注释/docstring，只留真实执行的代码。"""
        src = inspect.getsource(fn)
        out = []
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
        return " ".join(out)

    resize_code = calls_only(MainWindow.resizeEvent)
    assert "apply_rounded_corners" not in resize_code, \
        "resizeEvent 里不能调 apply_rounded_corners（它包含 addShadowEffect）"
    assert "addShadowEffect" not in resize_code
    assert "set_corners" not in resize_code, "Win11 圆角偏好在 showEvent 设一次即可，resize 不必重设"


def test_corner_dispatch_windows_and_linux(app, monkeypatch):
    """按注入的平台/版本验证分派：Win11 只写 DWM，其余系统保持直角。"""
    from app.services import materials

    widget = QWidget()
    widget.resize(500, 400)

    calls = []
    monkeypatch.setattr(materials, "apply_dwm_round_corners",
                        lambda w: calls.append("dwm") or True)

    # Win11：写 DWM 圆角偏好
    materials.clear_corners(widget)
    assert materials.set_corners(widget, 10, platform="win32", build=22631) is True
    assert calls == ["dwm"], f"Win11 应只走 DWM：{calls}"

    # Win10：直角，什么都不做
    calls.clear()
    materials.clear_corners(widget)
    assert materials.set_corners(widget, 10, platform="win32", build=19045) is False
    assert calls == [], f"Win10 不该改窗口区域：{calls}"

    # Linux：桌面环境自带圆角与阴影，不动窗口区域
    calls.clear()
    materials.clear_corners(widget)
    assert materials.set_corners(widget, 10, platform="linux", build=0) is False
    assert calls == [], f"Linux 不该动窗口区域：{calls}"

    # macOS：系统自带，什么都不做
    materials.clear_corners(widget)
    assert materials.set_corners(widget, 10, platform="darwin", build=0) is True
    assert calls == []


def test_animate_expand_interruption_finalizes_state(app):
    """连续点击折叠按钮不能让卡片越按越歪。

    `QAbstractAnimation.stop()` 不触发 `finished`，所以被打断时 `_done` 的收尾
    （放开 maximumHeight、逐级重排父布局）必须手动补一次，否则每打断一次积
    一层误差，卡片高度与子控件位置就偏了。
    """
    from PyQt5.QtWidgets import QLabel, QVBoxLayout

    from app.ui.anim import _content_height, animate_expand

    host = QWidget()
    lay = QVBoxLayout(host)
    panel = QWidget()
    pl = QVBoxLayout(panel)
    pl.setContentsMargins(0, 0, 0, 0)
    for _ in range(4):
        pl.addWidget(QLabel("x" * 40))
    panel.setVisible(False)
    lay.addWidget(panel)
    lay.addStretch(1)
    host.resize(500, 400)
    host.show()
    app.processEvents()

    full = _content_height(panel)
    assert full > 0

    # 连点偶数次 → 最终收起
    for i in range(10):
        animate_expand(panel, i % 2 == 0, duration=40)
        app.processEvents()
    from PyQt5.QtTest import QTest

    QTest.qWait(150)
    app.processEvents()
    assert panel.maximumHeight() == 16777215, "打断后 maximumHeight 必须放开"
    assert not panel.isVisible(), "连点偶数次最终应收起"
    assert panel.height() == 0, f"收起后高度应为 0，实际 {panel.height()}"

    # 连点奇数次 → 最终展开到完整高度
    for i in range(9):
        animate_expand(panel, i % 2 == 0, duration=40)
        app.processEvents()
    QTest.qWait(150)
    app.processEvents()
    assert panel.isVisible(), "连点奇数次最终应展开"
    assert panel.height() == full, f"展开高度应回到 {full}，实际 {panel.height()}"


def test_org_detail_hides_banner_and_top_margin(app, isolated_store):
    """异常团员子页不带头图，顶部边距随之收回。

    只藏 banner 不改边距会在子页顶部留一条 196px 空白；
    反过来只改边距不藏 banner，头图会盖住面包屑。两者要联动。
    """
    from app.ui.org_page import OrgPage

    class Host(QWidget):
        def __init__(self):
            super().__init__()
            self.settings_page = type("S", (), {
                "accounts": [], "roster": None})()

    page = OrgPage(Host())
    page.resize(900, 700)
    page.show()
    app.processEvents()

    assert page.header.isVisible()
    assert page.vBoxLayout.contentsMargins().top() == 212

    page._open_detail()
    app.processEvents()
    assert not page.header.isVisible(), "子页不该显示头图"
    assert page.vBoxLayout.contentsMargins().top() == 16, "子页顶部边距应收回"
    assert not page.detail_box.isHidden()

    page.back_to_overview()
    app.processEvents()
    assert page.header.isVisible(), "返回概览应恢复头图"
    assert page.vBoxLayout.contentsMargins().top() == 212


def test_switch_with_fade_switches_visibility_immediately(app):
    """视图切换必须在函数内切完可见性，不能依赖动画结束信号。

    stop() 不触发 finished；连续点「查看 → 返回」时上一段被打断，
    若 setVisible(True) 在 finished 回调里就永远不执行，
    页面停在错误状态（标题消失、卡片被挤到底部）。
    """
    from app.ui.anim import switch_with_fade

    a, b = QWidget(), QWidget()
    a.show()
    b.hide()
    app.processEvents()

    switch_with_fade(a, b, duration=200)
    # 不等待动画结束，立刻检查
    assert not a.isVisible()
    assert b.isVisible()

    # 反向再切一次，打断上一段
    switch_with_fade(b, a, duration=200)
    assert a.isVisible()
    assert not b.isVisible()


def test_detail_title_uses_theme_foreground(app, isolated_store):
    """异常团员标题要取主题前景色，不能留默认的白字。

    section_title 只设字号。浅色主题下若不显式设色，白字画在白底上
    整行标题像消失了一样。
    """
    from app.ui import palette as P
    from app.ui.org_page import OrgPage

    class Host(QWidget):
        def __init__(self):
            super().__init__()
            self.settings_page = type("S", (), {
                "accounts": [], "roster": None})()

    page = OrgPage(Host())
    app.processEvents()
    label = page.error_title
    color = label.palette().color(label.foregroundRole())
    # 传入的是「浅色/深色」颜色对，两个值都要显式设过；
    # 不写 setTextColor 时 palette 里是默认白，_qfluent 的 theme 色对不会生效
    from qfluentwidgets import isDarkTheme

    expected = P.fg("fg")
    assert color.name() == expected.name(), \
        f"标题色 {color.name()} 与主题前景 {expected.name()} 不符（暗色={isDarkTheme()}）"


def test_browser_tab_aligns_to_system_button_and_retries(app, monkeypatch):
    """标签行按系统按钮中线定位；几何未定时挂起，等稳定后重试。

    showEvent 那一拍窗口高度可能还小于 100，`_sync_title_metrics` 提前返回；
    若没有 pending 标记 + resize 重试，标签就永远停在常量位置，与红绿灯差几像素。
    这里用替身跑纯计算，不在测试进程里调 AppKit / 起 WebEngine。
    """
    from app.ui import browser_page as B

    class Bar:
        def __init__(self):
            self.h = 0

        def setFixedHeight(self, h):
            self.h = h

        def height(self):
            return self.h

    class Lay:
        def __init__(self):
            self.m = None

        def setContentsMargins(self, l, t, r, b):
            self.m = (l, t, r, b)

    class Row:
        def __init__(self):
            self.h = 0
            self.lay = Lay()

        def setFixedHeight(self, h):
            self.h = h

        def layout(self):
            return self.lay

    host = type("H", (), {})()
    host.titleBar = Bar()
    host.hBoxLayout = Lay()
    host.tab_row = Row()
    host._tab_top = B.TAB_TOP
    host._metrics_pending = True
    host._win_h = 760

    def fake_height(self):
        return self._win_h

    host.height = fake_height.__get__(host)

    # 几何未定 → 挂起，不动位置
    host._win_h = 60
    monkeypatch.setattr(B, "native_button_center", lambda w: 20)
    B.BrowserWindow._sync_title_metrics(host)
    assert host._metrics_pending is True
    assert host._tab_top == B.TAB_TOP

    # 几何稳定：中线 20 → 上沿 = 20 - TAB_HEIGHT/2
    half = B.TAB_HEIGHT // 2
    host._win_h = 760
    B.BrowserWindow._sync_title_metrics(host)
    assert host._tab_top == 20 - half
    # 标题栏 = 上留白 + 标签 + 下留白，上下相等
    assert host.titleBar.height() == (20 - half) * 2 + B.TAB_HEIGHT
    assert host._metrics_pending is False

    # 中线变了（不同 macOS 版本）要能重算
    monkeypatch.setattr(B, "native_button_center", lambda w: 24)
    B.BrowserWindow._sync_title_metrics(host)
    assert host._tab_top == 24 - half
