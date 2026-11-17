"""PyQt5 生命周期约束：全局信号的槽必须是绑定方法。

PySide6 的绑定层在控件析构后仍保留可用的 Python 壳，误调用顶多是异常；PyQt5
里 lambda 挂在 qconfig 这类全局单例上会替控件攥着引用，控件的 C++ 侧先析构、
连接却不断，下一次主题切换打进悬空对象直接 abort（迁移期整轮测试随机崩死）。
绑定方法会在接收者析构时自动断开，是唯一安全的写法。"""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication, QWidget

APP_DIR = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def test_no_lambda_on_global_singleton_signals():
    """qconfig 这类活得比控件久的信号，禁止连 lambda 或局部闭包。"""
    bad = []
    pat = re.compile(r"qconfig\.\w+\.connect\(\s*(lambda|\(lambda|\s*lambda)")
    for path in sorted(APP_DIR.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        for n, line in enumerate(src.splitlines(), 1):
            if pat.search(line):
                bad.append(f"{path.relative_to(APP_DIR.parent)}:{n}: {line.strip()}")
    assert not bad, "全局信号连了 lambda（应改绑定方法）：\n" + "\n".join(bad)


def test_theme_switch_after_page_deleted(app, tmp_path, monkeypatch):
    """页面析构后切主题：绑定方法槽已自动断开，不许打到悬空对象。

    迁移期最崩溃的一条路径——导入配置的测试先建一个 SettingsPage 再换主题，
    页面 Python 壳已被回收，qconfig 的旧连接仍握着引用。"""
    import gc

    from app.services import store
    from app.setup import apply_theme_mode
    from app.ui.settings_page import SettingsPage

    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save({"themeMode": "light", "accounts": [], "memberCache": {},
                "messagesCache": {}, "orgStatsCache": {}})

    page = SettingsPage()
    page.show()
    app.processEvents()
    page.deleteLater()
    app.processEvents()
    del page
    gc.collect()
    apply_theme_mode("dark")
    app.processEvents()
    apply_theme_mode("light")
    app.processEvents()


def test_health_worker_parented_to_page(app):
    """测活线程挂到页面上：页面析构时 Qt 等线程结束，
    而不是「QThread: Destroyed while thread is still running」后 abort。"""
    from PyQt5.QtCore import QThread

    from app.ui.settings_page import HealthWorker

    page = QWidget()
    w = HealthWorker([], parent=page)
    assert w.parent() is page
    w.start()
    w.wait(1000)
    assert isinstance(w, QThread)


def test_smoke_result_file_is_written(tmp_path, monkeypatch):
    """--smoke-result 把结论写进文件。

    Windows 的 --windowed 产物没有控制台，CI 从管道既拿不到 stdout 也拿不到
    可靠退出码，只能靠这个文件判断冒烟是否通过；这一步没写成功，CI 就会在
    冒烟环节红掉（曾因此连挂三个 Windows 变体）。"""
    import sys as _sys

    from app import main as main_mod

    out = tmp_path / "smoke.txt"
    monkeypatch.setattr(_sys, "argv", ["main.py", "--smoke-test",
                                       "--smoke-result", str(out)])
    main_mod._write_smoke_result("SMOKE OK")
    assert out.read_text(encoding="utf-8").strip() == "SMOKE OK"


def test_smoke_result_absent_without_flag(tmp_path, monkeypatch):
    """没传 --smoke-result 时不落任何文件：正常启动不该在磁盘上留垃圾。"""
    import sys as _sys

    from app import main as main_mod

    monkeypatch.setattr(_sys, "argv", ["main.py"])
    main_mod._write_smoke_result("SMOKE OK")
    assert not any(tmp_path.iterdir())
