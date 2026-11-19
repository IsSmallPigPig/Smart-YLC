"""界面文案检查：拦住 AI 腔与自相重复的说明。

规则来源是这次对话里用户明确提过的要求，逐条写在 RULES 里。只检查用户能看到的
文案（界面文字、提示条、状态说明），不碰代码注释——注释是写给维护者看的，
不按产品文案的标准要求。

用法：
    python scripts/lint_copy.py            # 打印问题并返回非零
    python scripts/lint_copy.py --quiet    # 只返回码，供测试调用
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = (ROOT / "app" / "ui", ROOT / "app" / "services")

# 这些函数/方法的字面量参数会被显示给用户
TEXT_CALLS = {
    "note_label", "BodyLabel", "CaptionLabel", "SubtitleLabel", "StrongBodyLabel",
    "TitleLabel", "setText", "set_desc", "setToolTip", "set_tip",
    "setPlaceholderText", "setWindowTitle", "success", "warning", "error", "info",
}

CJK = re.compile(r"[\u4e00-\u9fff]")

# (规则名, 正则, 说明) —— 每条都对应一次实际被要求改掉的写法
RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("AI 套话",
     re.compile(r"旨在|致力于|赋能|助力|打造|无缝|一站式|全方位|闭环|"
                r"进一步|更加|极大|显著地|有效地|完美支持"),
     "空泛的营销词，读者拿不到任何可判断的信息"),
    ("AI 关联句式",
     re.compile(r"不仅.{0,14}(而且|还|更能)|在.{1,12}的同时"),
     "套句式把两句话硬凑在一起，直接分成两句"),
    ("公文腔",
     re.compile(r"综上所述|值得注意的是|总而言之|换句话说|众所周知"),
     "论文腔，界面上没人这样说话"),
    ("客服腔",
     re.compile(r"温馨提示|友情提示|请注意|敬请期待|感谢您的"),
     "免责式客套，真正该说清楚的约束反而被冲淡"),
    ("空承诺",
     re.compile(r"暂不支持|即将支持|后续版本"),
     "界面上写未来计划，用户无法据此做决定"),
]

# 中文句子里混半角标点：中英文标点混排会在界面里显得脏
HALF_PUNCT = re.compile(r"[\u4e00-\u9fff]\s*[,;?!](?![0-9A-Za-z])")

# 一句说明超过这个字数就该拆：小字说明是放在控件下面的一行，不是段落
MAX_NOTE_CHARS = 48


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        text = "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant) and isinstance(v.value, str))
        return text or None
    return None


def _ui_strings(tree: ast.AST):
    """产出 (行号, 作用域, 文案)：作用域带类名，两个类里的同名方法不算同一处。"""
    scopes: dict[int, str] = {}

    def walk(node: ast.AST, scope: str):
        for child in ast.iter_child_nodes(node):
            child_scope = scope
            if isinstance(child, ast.ClassDef):
                child_scope = f"{scope}.{child.name}" if scope else child.name
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_scope = f"{scope}.{child.name}" if scope else child.name
            scopes[id(child)] = child_scope
            walk(child, child_scope)

    walk(tree, "")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else "")
        if name not in TEXT_CALLS:
            continue
        for arg in node.args:
            text = _literal(arg)
            if text and CJK.search(text):
                yield node.lineno, scopes.get(id(node), ""), text


def _check_repeats(items: list[tuple[str, int, str, str]]) -> list[str]:
    """同一个函数里出现两条相同说明：它们会同时出现在同一个界面块里。

    跨函数不查——两个分支各写一次同样的提示是正常的，用户只会看到其中一条。
    带占位符（{} / %s）的句子不参与比较，它们的正文其实不同。
    """
    problems: list[str] = []
    seen: dict[tuple[str, str, str], int] = {}
    for path, func, line, text in items:
        if "{}" in text or "%s" in text:
            continue
        key = re.sub(r"[\s，。；：、,.!?！？]", "", text)
        if len(key) < 8:
            continue
        slot = (path, func, key)
        prev = seen.get(slot)
        if prev is not None:
            problems.append(
                f"{path}:{line} 与同函数第 {prev} 行重复同一句说明，"
                f"同一块界面里会读到两遍：{text}")
        else:
            seen[slot] = line
    return problems


def check_file(path: Path) -> tuple[list[str], list[tuple[str, int, str, str]]]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{path.name}:{exc.lineno} 语法错误，跳过文案检查"], []

    try:
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:            # 测试里的临时文件不在仓库下
        rel = path.name
    items: list[tuple[str, int, str, str]] = []
    problems: list[str] = []
    for line, func, text in _ui_strings(tree):
        items.append((rel, func, line, text))
        for rule, pattern, why in RULES:
            hit = pattern.search(text)
            if hit:
                problems.append(f"{rel}:{line} [{rule}] 「{hit.group(0)}」—— {why}")
        if HALF_PUNCT.search(text):
            problems.append(
                f"{rel}:{line} [标点混排] 中文里用了半角标点：{text[:30]}")
        cjk_len = len(CJK.findall(text))
        if cjk_len > MAX_NOTE_CHARS:
            problems.append(
                f"{rel}:{line} [过长] {cjk_len} 个汉字，小字说明一行放不下：{text[:30]}…")
    problems.extend(_check_repeats(items))
    return problems, items


def run(quiet: bool = False) -> list[str]:
    problems: list[str] = []
    for folder in SCAN_DIRS:
        for path in sorted(folder.glob("*.py")):
            file_problems, _items = check_file(path)
            problems.extend(file_problems)
    if not quiet:
        for line in problems:
            print(line)
        print(f"\n共 {len(problems)} 处" if problems else "\n没有发现问题")
    return problems


if __name__ == "__main__":
    sys.exit(1 if run(quiet="--quiet" in sys.argv) else 0)
