"""界面文案检查的回归测试。

既验证规则能拦住被要求改掉的写法，也验证当前代码库是干净的——
否则这条 lint 会变成「跑一次红一次、然后被忽略」的摆设。
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import lint_copy  # noqa: E402


def _check_source(tmp_path, source: str) -> list[str]:
    path = tmp_path / "sample.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    problems, _ = lint_copy.check_file(path)
    return problems


@pytest.mark.parametrize("bad,rule", [
    ("旨在让档案管理更高效", "AI 套话"),
    ("助力团支部完成日常事务", "AI 套话"),
    ("不仅能看到份数，还能直接上传", "AI 关联句式"),
    ("值得注意的是，这里需要密码", "公文腔"),
    ("温馨提示：上传前请先核对", "客服腔"),
    ("当前版本暂不支持批量导出", "空承诺"),
])
def test_ai_tone_rules(tmp_path, bad, rule):
    problems = _check_source(tmp_path, f'''
        from app.ui.page import note_label
        def build(self):
            note_label("{bad}")
    ''')
    assert any(rule in p for p in problems), problems


def test_half_width_punctuation_in_chinese(tmp_path):
    problems = _check_source(tmp_path, '''
        from app.ui.page import note_label
        def build(self):
            note_label("上传前先核对, 再点确定")
    ''')
    assert any("标点混排" in p for p in problems)


def test_repeated_note_in_one_scope(tmp_path):
    """用户在弹窗里同时读到两遍同一句话——这是被点名改过的问题。"""
    problems = _check_source(tmp_path, '''
        from app.ui.page import note_label
        class Dialog:
            def build(self):
                note_label("还没有配置，点击新建或导入")
                note_label("还没有配置，点击新建或导入")
    ''')
    assert any("重复同一句说明" in p for p in problems)


def test_same_text_in_two_classes_is_allowed(tmp_path):
    """两个不同弹窗各写一次同样的提示不算重复：用户只会看到其中一个。"""
    problems = _check_source(tmp_path, '''
        from app.ui.page import note_label
        class NewDialog:
            def build(self):
                note_label("默认按提取码起名，可改")
        class JoinDialog:
            def build(self):
                note_label("默认按提取码起名，可改")
    ''')
    assert not any("重复同一句说明" in p for p in problems)


def test_overlong_note(tmp_path):
    long_text = "扫描完成之后自动核对每一份档案的页数命名与支部归属" * 3
    problems = _check_source(tmp_path, f'''
        from app.ui.page import note_label
        def build(self):
            note_label("{long_text}")
    ''')
    assert any("过长" in p for p in problems)


def test_current_copy_is_clean():
    """当前代码库必须零问题，这条是防止规则变成摆设。"""
    assert lint_copy.run(quiet=True) == []


# —— anti-slop：手机端 Kotlin ——

def _check_kotlin(tmp_path, source: str) -> list[str]:
    path = tmp_path / "Sample.kt"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return lint_copy.check_kotlin(path)


def test_kotlin_middot_is_not_a_separator(tmp_path):
    problems = _check_kotlin(tmp_path, '''
        @Composable
        fun Card1() {
            Text("33 KB · 11月18日")
        }
    ''')
    assert any("中点分隔符" in p for p in problems), problems


def test_kotlin_note_must_not_teach_operations(tmp_path):
    problems = _check_kotlin(tmp_path, '''
        @Composable
        fun Card1() {
            MutedText("先点击右上角，然后选择导入即可")
        }
    ''')
    assert any("操作教学" in p for p in problems), problems


def test_kotlin_button_over_four_chars(tmp_path):
    problems = _check_kotlin(tmp_path, '''
        @Composable
        fun Bar() {
            TextButton(text = "查看全部扫描档案", onClick = {})
        }
    ''')
    assert any("按钮过长" in p for p in problems), problems


def test_kotlin_two_text_buttons_in_card(tmp_path):
    problems = _check_kotlin(tmp_path, '''
        @Composable
        fun Card1() {
            Card {
                Row {
                    TextButton(text = "查看", onClick = {})
                    TextButton(text = "分享", onClick = {})
                }
            }
        }
    ''')
    assert any("按钮堆叠" in p for p in problems), problems


def test_kotlin_dialog_actions_are_allowed(tmp_path):
    problems = _check_kotlin(tmp_path, '''
        @Composable
        fun Dialog1() {
            OverlayDialog(show = true) {
                Row {
                    TextButton(text = "取消", onClick = {})
                    TextButton(text = "删除", onClick = {})
                }
            }
        }
    ''')
    assert not any("按钮堆叠" in p for p in problems), problems


def test_python_note_must_not_teach_operations(tmp_path):
    problems = _check_source(tmp_path, '''
        from app.ui.page import note_label
        def build(self):
            note_label("点击右上角即可导入")
    ''')
    assert any("操作教学" in p for p in problems), problems


def test_current_copy_is_clean():
    assert lint_copy.run(quiet=True) == []
