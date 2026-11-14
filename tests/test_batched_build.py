"""分批构建：首批同步建好（首帧有内容），其余按帧补齐，旧批次自动作废。"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QWidget


@pytest.fixture()
def app():
    yield QApplication.instance() or QApplication([])


def test_first_batch_is_synchronous_rest_follows(app):
    from app.ui.page import BatchedBuild

    owner = QWidget()
    built = []
    builder = BatchedBuild(owner, batch=2)

    builder.start(range(5), built.append)
    assert built == [0, 1]  # 首批同步完成，界面立刻有内容

    for _ in range(8):
        app.processEvents()
    assert built == [0, 1, 2, 3, 4]


def test_restart_drops_the_previous_batch(app):
    """连续搜索时，旧批次必须停下，否则两批卡片会交叉插进同一个布局。"""
    from app.ui.page import BatchedBuild

    owner = QWidget()
    builder = BatchedBuild(owner, batch=1)
    old, new = [], []

    builder.start(["a1", "a2", "a3"], old.append)
    builder.start(["b1", "b2"], new.append)
    for _ in range(6):
        app.processEvents()

    assert old == ["a1"]      # 旧批次只跑了同步的那一个
    assert new == ["b1", "b2"]


def test_cancel_stops_building(app):
    from app.ui.page import BatchedBuild

    owner = QWidget()
    builder = BatchedBuild(owner, batch=1)
    built = []

    builder.start(["x", "y", "z"], built.append)
    builder.cancel()
    for _ in range(6):
        app.processEvents()
    assert built == ["x"]
