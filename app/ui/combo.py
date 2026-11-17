"""下拉选择框：替掉 qfluentwidgets ComboBox 关闭下拉时的自毁。

原生 ComboBox 给下拉菜单设了 `WA_DeleteOnClose`：菜单一关就排队析构。菜单的析构
函数会先 `clearFocus()`，再给「当前焦点控件」派发 FocusOut——而这次析构正是从
下拉列表自身的销毁里走出来的，Qt 手里的焦点指针已经失效，进程直接 SIGSEGV。
macOS 上稳定复现：在整窗遮罩的弹窗里选完支部（班级），窗口直接闪退，崩溃现场是
`QWidget::~QWidget` → `clearFocus` → `setFocusWidget`。

这里只改一件事：菜单关闭时不销毁，改由组合框托管，等组合框自己销毁时作为子节点
一起回收。旧菜单在下一次打开下拉时释放——那一刻焦点在组合框上，没有列表正在析构，
不存在上面那条失效指针。
"""

from __future__ import annotations

from PyQt5.QtCore import QCoreApplication, QEvent, Qt

from qfluentwidgets import ComboBox as _ComboBox


class ComboBox(_ComboBox):
    """与 qfluentwidgets ComboBox 用法一致，只是下拉菜单不再关闭即析构。"""

    def _createComboMenu(self):
        # 新下拉要开了：此刻没有列表持有焦点，正是回收上一个菜单的安全时机
        for old in getattr(self, "_held_menus", []):
            old.deleteLater()
        self._held_menus = []

        menu = super()._createComboMenu()
        menu.closedSignal.connect(lambda m=menu: self._hold_menu(m))
        return menu

    def _hold_menu(self, menu):
        """下拉关闭：撤掉「关闭即销毁」，并清掉已经排队的析构事件。"""
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        QCoreApplication.removePostedEvents(menu, QEvent.Type.DeferredDelete)
        held = getattr(self, "_held_menus", None)
        if held is None:
            held = self._held_menus = []
        held.append(menu)
