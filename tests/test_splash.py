"""启动闪屏：整窗覆盖、淡出收起、首屏就绪信号。"""

from __future__ import annotations

import os
import tempfile
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def isolated_store(monkeypatch):
    from app.services import store

    tmp = tempfile.mkdtemp(prefix="gd-splash-")
    monkeypatch.setattr(store, "CONFIG_FILE", os.path.join(tmp, "config.json"))
    store.save({
        "themeMode": "dark",
        "accounts": [],
        "memberCache": {},
        "messagesCache": {},
        "orgStatsCache": {},
    })
    return store


def _pump(app, ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def test_splash_covers_sidebar_and_titlebar(app):
    """闪屏铺满父窗口，侧边栏/标题栏/内容区都只剩闪屏底色。"""
    from app.ui.splash import SplashScreen

    shell = QWidget()
    shell.resize(400, 300)
    side = QWidget(shell)
    side.setGeometry(0, 0, 50, 300)
    side.setStyleSheet("background: #00ff00")
    top = QWidget(shell)
    top.setGeometry(0, 0, 400, 30)
    top.setStyleSheet("background: #0000ff")
    body = QWidget(shell)
    body.setGeometry(50, 30, 350, 270)
    body.setStyleSheet("background: #ff0000")

    splash = SplashScreen(shell)
    splash.setGeometry(shell.rect())
    splash.raise_()
    splash.show()
    shell.show()
    app.processEvents()

    img = shell.grab().toImage()
    samples = [(20, 150), (10, 10), (200, 150), (380, 280)]
    colors = {img.pixelColor(x, y).name() for x, y in samples}
    assert len(colors) == 1  # 四个区域都被同一层盖住
    assert colors != {"#00ff00", "#0000ff", "#ff0000"}
    splash.ring.stop()
    shell.close()


def test_splash_finish_hides_and_clears_effect(app):
    from app.ui.splash import SplashScreen

    splash = SplashScreen()
    splash.show()
    splash.start()
    app.processEvents()
    splash.finish()
    _pump(app, 600)
    assert splash.isHidden()
    assert splash.graphicsEffect() is None


def test_dismiss_splash_is_idempotent():
    """stats_ready 与 5 秒兜底都会调它，只能真正收起一次。"""
    from app.ui.main_window import MainWindow

    calls = []

    class Stub:
        _splash_done = False

        def __init__(self):
            self.home_page = types.SimpleNamespace(_startup_loading=True)
            self.splash = types.SimpleNamespace(finish=lambda: calls.append(1))

    stub = Stub()
    MainWindow._dismiss_splash(stub)
    MainWindow._dismiss_splash(stub)
    assert calls == [1]
    assert stub._splash_done is True
    assert stub.home_page._startup_loading is False


def test_home_emits_stats_ready(app, isolated_store):
    """首屏快照应用后发信号，主窗口据此收起闪屏。"""
    from app.ui.home_page import HomePage

    class Win:
        def switchTo(self, *a, **k):
            pass

    page = HomePage(Win())
    hits = []
    page.stats_ready.connect(lambda: hits.append(1))
    page.refresh_stats(animate=False, show_loading=False)
    end = time.monotonic() + 4
    while time.monotonic() < end and not hits:
        app.processEvents()
        time.sleep(0.02)
    assert hits
