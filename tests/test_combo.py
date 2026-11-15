"""下拉选择框：菜单关闭后不能被立即析构。

qfluentwidgets 给下拉菜单设 WA_DeleteOnClose，菜单一关就排队析构；析构里会给
「当前焦点控件」派发 FocusOut，而在整窗遮罩的弹窗里选完支部时那个指针已经失效，
macOS 上直接 SIGSEGV（选完班级闪退）。这里固化约定：关闭只把菜单交给组合框托管，
同时撤掉「关闭即销毁」，下一次打开下拉时才回收上一个。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def test_closed_menu_is_kept_alive(app):
    from app.ui.combo import ComboBox

    combo = ComboBox()
    combo.addItem("高一1班", userData="ssfz12401")

    menu = combo._createComboMenu()
    # 库在 _showComboMenu 里加的就是这条属性
    menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    menu.show()
    menu.close()

    assert menu in combo._held_menus
    assert not menu.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    # 没有排队中的析构事件：下次事件循环不会把它删掉
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert combo._held_menus == [menu]


def test_previous_menu_recycled_on_next_open(app):
    from app.ui.combo import ComboBox

    combo = ComboBox()
    combo.addItem("高一1班", userData="ssfz12401")

    first = combo._createComboMenu()
    first.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    first.show()
    first.close()
    assert combo._held_menus == [first]

    # 再次打开：上一个已关掉且没有焦点，正是回收时机
    second = combo._createComboMenu()
    assert first not in combo._held_menus

    second.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    second.show()
    second.close()
    assert combo._held_menus == [second]
