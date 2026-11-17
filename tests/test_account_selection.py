"""账号表选择：表头全选、勾选优先、单个选中账号成为当前账号。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from PyQt5.QtCore import Qt

from app.ui import settings_page as mod


class FakeItem:
    def __init__(self, state=Qt.CheckState.Unchecked):
        self.state = state

    def checkState(self):
        return self.state

    def setCheckState(self, value):
        self.state = value


class FakeTable:
    def __init__(self, states, selected=()):
        self.items = [FakeItem(s) for s in states]
        self.rows = set(selected)

    def rowCount(self):
        return len(self.items)

    def item(self, row, col):
        return self.items[row] if col == 0 and 0 <= row < len(self.items) else None

    def selectedIndexes(self):
        return [SimpleNamespace(row=lambda r=r: r) for r in sorted(self.rows)]

    def blockSignals(self, value):
        return False


def _page(states, selected=(), active="a"):
    accounts = [{"userName": u} for u in ("a", "b", "c")][:len(states)]
    page = SimpleNamespace(
        accounts=accounts, active_user=active,
        table=FakeTable(states, selected),
        active_combo=MagicMock(),
        _select_all_header=SimpleNamespace(box=MagicMock()),
    )
    # 被测方法之间会互相调用，把类方法绑到桩对象上
    page._checked_rows = lambda: mod.SettingsPage._checked_rows(page)
    page._selected_rows = lambda: mod.SettingsPage._selected_rows(page)
    page._update_select_all_box = lambda: mod.SettingsPage._update_select_all_box(page)
    page._sync_active_from_selection = lambda: mod.SettingsPage._sync_active_from_selection(page)
    page._on_select_all_clicked = lambda: mod.SettingsPage._on_select_all_clicked(page)
    return page


def test_checked_rows_take_priority_over_row_selection():
    page = _page([Qt.CheckState.Checked, Qt.CheckState.Unchecked, Qt.CheckState.Unchecked],
                 selected=(1, 2))
    assert mod.SettingsPage._selected_rows(page) == [0]


def test_row_selection_is_used_when_nothing_checked():
    page = _page([Qt.CheckState.Unchecked] * 3, selected=(2,))
    assert mod.SettingsPage._selected_rows(page) == [2]


def test_no_selection_falls_back_to_active_account():
    page = _page([Qt.CheckState.Unchecked] * 3, active="b")
    assert mod.SettingsPage._selected_rows(page) == [1]


def test_single_checked_account_becomes_active():
    page = _page([Qt.CheckState.Unchecked, Qt.CheckState.Checked, Qt.CheckState.Unchecked])
    mod.SettingsPage._sync_active_from_selection(page)
    page.active_combo.setCurrentText.assert_called_once_with("b")


def test_single_selected_row_becomes_active():
    page = _page([Qt.CheckState.Unchecked] * 3, selected=(2,))
    mod.SettingsPage._sync_active_from_selection(page)
    page.active_combo.setCurrentText.assert_called_once_with("c")


def test_multiple_selection_keeps_active_account():
    page = _page([Qt.CheckState.Checked, Qt.CheckState.Checked, Qt.CheckState.Unchecked])
    mod.SettingsPage._sync_active_from_selection(page)
    page.active_combo.setCurrentText.assert_not_called()


def test_select_all_checks_every_row_then_clears():
    page = _page([Qt.CheckState.Unchecked] * 3)

    mod.SettingsPage._on_select_all_clicked(page)
    assert [i.state for i in page.table.items] == [Qt.CheckState.Checked] * 3
    assert page._select_all_header.box.setCheckState.call_args.args == (Qt.CheckState.Checked,)

    page._select_all_header.box.setCheckState.reset_mock()
    mod.SettingsPage._on_select_all_clicked(page)
    assert [i.state for i in page.table.items] == [Qt.CheckState.Unchecked] * 3
    assert page._select_all_header.box.setCheckState.call_args.args == (Qt.CheckState.Unchecked,)


def test_partial_selection_shows_partially_checked_header():
    page = _page([Qt.CheckState.Checked, Qt.CheckState.Unchecked,
                  Qt.CheckState.Unchecked])
    mod.SettingsPage._update_select_all_box(page)
    assert page._select_all_header.box.setCheckState.call_args.args == (
        Qt.CheckState.PartiallyChecked,)
