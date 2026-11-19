"""使用引导：每学年（9 月 1 日起）弹一次，内容覆盖配置项与功能。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
from datetime import datetime

import pytest
from PyQt5.QtWidgets import QApplication

from app.ui.guide_dialog import (GuideDialog, mark_guide_shown, school_cycle,
                                 should_show_guide)


@pytest.fixture()
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def isolated_store(monkeypatch):
    from app.services import store

    tmp = tempfile.mkdtemp(prefix="gd-guide-")
    monkeypatch.setattr(store, "CONFIG_FILE", os.path.join(tmp, "config.json"))
    return tmp


def test_school_cycle_starts_on_september_first():
    """8 月 31 日还算上一学年，9 月 1 日起进入新学年。"""
    assert school_cycle(datetime(2026, 8, 31)) == 2025
    assert school_cycle(datetime(2026, 9, 1)) == 2026
    assert school_cycle(datetime(2026, 12, 1)) == 2026
    assert school_cycle(datetime(2027, 3, 1)) == 2026


def test_guide_shows_on_first_launch():
    assert should_show_guide({}, datetime(2026, 11, 19)) is True
    assert should_show_guide({"guideShownAt": "not-a-date"},
                             datetime(2026, 11, 19)) is True


def test_guide_shows_once_per_school_year():
    seen = {"guideShownAt": datetime(2026, 9, 3, 9, 0).isoformat()}
    assert should_show_guide(seen, datetime(2026, 9, 20)) is False
    assert should_show_guide(seen, datetime(2026, 12, 1)) is False
    assert should_show_guide(seen, datetime(2027, 6, 1)) is False   # 同学年
    assert should_show_guide(seen, datetime(2027, 9, 1)) is True    # 新学年


def test_guide_reshows_after_september_when_last_seen_in_summer():
    """上一学年看过（比如 7 月），9 月 1 日进入新学年要再提示一次。"""
    seen = {"guideShownAt": datetime(2026, 7, 1, 9, 0).isoformat()}
    assert should_show_guide(seen, datetime(2026, 9, 1)) is True


def test_mark_guide_shown_writes_timestamp(isolated_store):
    from app.services import store

    mark_guide_shown(datetime(2026, 9, 5, 10, 30))
    assert store.load()["guideShownAt"].startswith("2026-09-05T10:30")


def test_guide_dialog_lists_settings_and_features(app):
    from PyQt5.QtWidgets import QWidget

    host = QWidget()
    dlg = GuideDialog(host)
    texts = []
    for w in dlg.findChildren(QWidget):
        getter = getattr(w, "text", None)
        if callable(getter):
            texts.append(str(getter()))
    blob = " ".join(texts)
    # 弹窗里两个分区都在：配置项与功能
    assert "团支部账号" in blob
    assert "来源文件夹" in blob
    assert "上传前自检" in blob
    assert "访问支部" in blob
    dlg.deleteLater()
