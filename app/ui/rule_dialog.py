"""编号规则编辑器：规则文本、学年选项、示例实时解析、学校间导入导出。

非模态弹窗，保存前校验全部规则；任何一行写错都会列出行号和原因，
不静默跳过。校验收在 MessageBoxBase.validate() 里，失败时弹窗不关闭。
"""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFileDialog, QGridLayout, QHBoxLayout, QWidget

from qfluentwidgets import CaptionLabel, LineEdit, PushButton, SubtitleLabel, TextEdit

from app.domain.org_rule import (
    DEFAULT_LABEL,
    OrgRule,
    RuleError,
    default_patterns,
    default_rule_text,
    explain_code,
    parse_rule_text,
    sample_codes,
)
from app.ui.dialogs import FluentPanelDialog
from app.ui.page import note_label

LEGEND = ("{yy} 入学年两位    {yyyy} 入学年四位    {g} {gg} 年级序号    "
          "{c} {cc} {ccc} 班号    {n} {nn} {nnn} 忽略位")


class RuleDialog(FluentPanelDialog):
    """编辑一套完整规则；保存成功时发出 rule_saved(OrgRule)。"""

    rule_saved = Signal(object)

    def __init__(self, rule: OrgRule, parent: QObject | None = None):
        super().__init__(parent)
        self._built: OrgRule | None = None
        self._error_override = ""
        self.widget.setMinimumWidth(740)
        self.viewLayout.setSpacing(10)

        self.viewLayout.addWidget(SubtitleLabel("编号规则", self.widget))
        self.viewLayout.addWidget(note_label(LEGEND))

        self.rule_edit = TextEdit(self.widget)
        self.rule_edit.setMinimumHeight(190)
        f = self.rule_edit.font()
        f.setFamily("Menlo")
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.rule_edit.setFont(f)
        self.rule_edit.setPlaceholderText(default_rule_text())
        self.viewLayout.addWidget(self.rule_edit)

        opts = QWidget(self.widget)
        grid = QGridLayout(opts)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(9)
        self.century_edit = self._field(grid, 0, 0, "世纪基准", "2000")
        self.month_edit = self._field(grid, 0, 1, "学年起始月", "8")
        self.label_edit = self._field(grid, 1, 0, "备注格式", DEFAULT_LABEL)
        self.account_edit = self._field(grid, 1, 1, "账号提取正则", r"(?P<code>\d{5})")
        self.row_edit = self._field(grid, 2, 0, "账号列正则", r"^[A-Za-z]{0,6}\d{3,}$")
        self.sample_edit = self._field(grid, 2, 1, "示例编号", "12407, 2407")
        self.viewLayout.addWidget(opts)

        self.result_label = note_label("")
        self.result_label.setMinimumHeight(54)
        self.viewLayout.addWidget(self.result_label)

        actions = QWidget(self.widget)
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        for text, slot in (("恢复默认", self._restore_default),
                           ("导入规则", self._import_rule),
                           ("导出规则", self._export_rule)):
            btn = PushButton(text, actions)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch(1)
        self.viewLayout.addWidget(actions)

        self.yesButton.setText("保存")
        self.cancelButton.setText("取消")
        self.finished.connect(self.deleteLater)

        for ed in (self.rule_edit, self.century_edit, self.month_edit, self.label_edit,
                   self.account_edit, self.row_edit, self.sample_edit):
            ed.textChanged.connect(self._on_edit)

        self._fill(rule)

    def _field(self, grid: QGridLayout, row: int, col: int,
               title: str, placeholder: str) -> LineEdit:
        box = QWidget(self.widget)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lbl = CaptionLabel(title, box)
        lbl.setFixedWidth(84)
        edit = LineEdit(box)
        edit.setPlaceholderText(placeholder)
        lay.addWidget(lbl)
        lay.addWidget(edit, 1)
        grid.addWidget(box, row, col)
        return edit

    def _on_edit(self, *_args):
        self._error_override = ""
        self._refresh()

    def _fill(self, rule: OrgRule):
        self.rule_edit.blockSignals(True)
        self.rule_edit.setPlainText(rule.rule_text())
        self.rule_edit.blockSignals(False)
        values = {
            self.century_edit: str(rule.century),
            self.month_edit: str(rule.year_start_month),
            self.label_edit: rule.label_template,
            self.account_edit: rule.account_pattern,
            self.row_edit: rule.row_pattern,
            self.sample_edit: rule.samples,
        }
        for edit, value in values.items():
            edit.blockSignals(True)
            edit.setText(value)
            edit.blockSignals(False)
        self._refresh()

    def _build_rule(self) -> tuple[OrgRule | None, list[str]]:
        patterns, errors = parse_rule_text(self.rule_edit.toPlainText())
        if not patterns and not errors:
            errors.append("至少写一条规则")
        try:
            century = int(self.century_edit.text().strip() or 2000)
            month = int(self.month_edit.text().strip() or 8)
        except ValueError:
            errors.append("世纪基准和学年起始月要填整数")
            return None, errors
        if not 1900 <= century <= 2200:
            errors.append("世纪基准需在 1900 到 2200 之间")
        if not 1 <= month <= 12:
            errors.append("学年起始月需在 1 到 12 之间")
        if errors:
            return None, errors
        rule = OrgRule(
            patterns=patterns,
            century=century,
            year_start_month=month,
            label_template=self.label_edit.text().strip() or DEFAULT_LABEL,
            account_pattern=self.account_edit.text().strip(),
            row_pattern=self.row_edit.text().strip(),
            samples=self.sample_edit.text().strip(),
        )
        try:
            OrgRule.from_config({"orgRule": rule.to_config()})
        except RuleError as e:
            return None, [str(e)]
        return rule, []

    def _refresh(self):
        if self._error_override:
            self.result_label.setText(self._error_override)
            return
        rule, errors = self._build_rule()
        if errors:
            self.result_label.setText("\n".join(errors))
            return
        lines = []
        for code in sample_codes(rule):
            info, why = explain_code(code, rule)
            lines.append(f"{code}  {info.label if info else why}")
        self.result_label.setText("\n".join(lines))

    def validate(self) -> bool:
        rule, errors = self._build_rule()
        if errors:
            self.result_label.setText("\n".join(errors))
            return False
        self._built = rule
        self.rule_saved.emit(rule)
        return True

    def _restore_default(self):
        self._fill(OrgRule(patterns=default_patterns()))

    def _import_rule(self):
        path, _ = QFileDialog.getOpenFileName(
            self.window(), "导入编号规则", "", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            rule = OrgRule.from_config({"orgRule": data.get("orgRule", data)})
        except (OSError, ValueError, RuleError, AttributeError) as e:
            self._error_override = f"导入失败：{e}"
            self._refresh()
            return
        self._error_override = ""
        self._fill(rule)

    def _export_rule(self):
        rule, errors = self._build_rule()
        if errors:
            self._error_override = "规则有误，修正后再导出：" + "；".join(errors)
            self._refresh()
            return
        path, _ = QFileDialog.getSaveFileName(
            self.window(), "导出编号规则", "编号规则.json", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rule.to_config(), f, ensure_ascii=False, indent=2)
        except OSError as e:
            self._error_override = f"导出失败：{e}"
            self._refresh()
