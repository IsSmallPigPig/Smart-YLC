"""界面文案检查：拦住 AI 腔、说明书腔与堆叠的文字按钮。

规则来源是用户明确提过的要求（每一条都对应一次被点名改掉的界面），逐条写在
RULES 里。只检查用户能看到的文案（界面文字、提示条、状态说明），不碰代码注释。

同时扫两处：
- Python 桌面端：app/ui、app/services 里传给标签/提示函数的字面量；
- Kotlin 手机端：mobile/app/src/main/java 里含中文的字符串字面量。

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

# —— 以下是用户点名要求过的写法，桌面端与手机端共用 ——

# 「说明的小字用于概要描述功能，而不是教用户如何操作」：说明里出现操作动词就是在写教程。
# 按钮文案本身不参与这条，只有说明性文本才查。
INSTRUCTION_WORDS = re.compile(
    r"点击|轻触|点一下|请先|首先|然后再|即可查看|如下图|如下所示|操作步骤|按以下步骤")
INSTRUCTION_CALLS = {"note_label", "BodyLabel", "CaptionLabel", "MutedText", "EmptyHint", "SmallTitle"}

# Miuix/Classy 共同的设计铁律：中点不是分隔符。信息层次用留白和字重表达。
MIDDOT = re.compile(r"·")

# 按钮超过四个字就会挤：能用一个词说清的动作不要写半句话。
MAX_BUTTON_CHARS = 4

# 手机端 Kotlin 的字面量与按钮调用
KT_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
KT_BUTTON_TEXT = re.compile(r"TextButton\(\s*text\s*=\s*\"([^\"]+)\"")
KT_BUTTON_LAMBDA_TEXT = re.compile(r"Button\([^)]{0,200}?\)\s*\{\s*Text\(\s*\"([^\"]+)\"", re.S)
KT_VISIBLE_CALL = re.compile(
    r'(?:Text|MutedText|EmptyHint|SmallTitle|StatusPill|contentDescription|label|summary|title)\s*[=(]\s*"([^"]*)"')

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
        if INSTRUCTION_WORDS.search(text):
            hit = INSTRUCTION_WORDS.search(text).group(0)
            problems.append(
                f"{rel}:{line} [操作教学] 「{hit}」—— 小字说明只交代功能概要，"
                "操作路径靠界面本身引导")
        if MIDDOT.search(text):
            problems.append(
                f"{rel}:{line} [中点分隔符] 用留白或换行分层，不要拿「·」拼信息：{text[:32]}")
        if HALF_PUNCT.search(text):
            problems.append(
                f"{rel}:{line} [标点混排] 中文里用了半角标点：{text[:30]}")
        cjk_len = len(CJK.findall(text))
        if cjk_len > MAX_NOTE_CHARS:
            problems.append(
                f"{rel}:{line} [过长] {cjk_len} 个汉字，小字说明一行放不下：{text[:30]}…")
    problems.extend(_check_repeats(items))
    return problems, items


KT_ROOTS = (ROOT / "mobile" / "app" / "src" / "main" / "java",)


def _strip_kt_comments(source: str) -> str:
    """去掉行注释与块注释，注释里的中文不按产品文案要求。"""
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def _cjk_count(text: str) -> int:
    return len(CJK.findall(text))


def check_kotlin(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    source = _strip_kt_comments(raw)
    try:
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:
        rel = path.name
    problems: list[str] = []
    seen_middot: set[int] = set()

    # 中点不依赖调用形态：任何字符串里拿「·」拼信息都违规
    for match in KT_STRING.finditer(source):
        text = match.group(1)
        if MIDDOT.search(text):
            line = source.count("\n", 0, match.start()) + 1
            seen_middot.add(line)
            problems.append(
                f"{rel}:{line} [中点分隔符] 用留白或换行分层，不要拿「·」拼信息：{text[:32]}")

    for match in KT_VISIBLE_CALL.finditer(source):
        text = match.group(1)
        # 中点要单独判：纯英文/数字的拼接（"33 KB · 11月18日"）同样违规
        if not text or (not CJK.search(text) and not MIDDOT.search(text)):
            continue
        line = source.count("\n", 0, match.start()) + 1
        for rule, pattern, why in RULES:
            hit = pattern.search(text)
            if hit:
                problems.append(f"{rel}:{line} [{rule}] 「{hit.group(0)}」—— {why}")
        instruction = INSTRUCTION_WORDS.search(text)
        if instruction:
            problems.append(
                f"{rel}:{line} [操作教学] 「{instruction.group(0)}」—— 小字说明只交代功能概要，"
                "操作路径靠界面本身引导")
        if _cjk_count(text) > MAX_NOTE_CHARS:
            problems.append(
                f"{rel}:{line} [过长] {_cjk_count(text)} 个汉字，小字说明一行放不下：{text[:30]}…")

    # 按钮文案：TextButton(text = "...") 与 Button { Text("...") }
    for pattern in (KT_BUTTON_TEXT, KT_BUTTON_LAMBDA_TEXT):
        for match in pattern.finditer(source):
            text = match.group(1)
            if _cjk_count(text) > MAX_BUTTON_CHARS:
                line = source.count("\n", 0, match.start()) + 1
                problems.append(
                    f"{rel}:{line} [按钮过长] 「{text}」{_cjk_count(text)} 个汉字，"
                    f"按钮最多 {MAX_BUTTON_CHARS} 个，能图标就图标")
            if MIDDOT.search(text):
                line = source.count("\n", 0, match.start()) + 1
                problems.append(f"{rel}:{line} [中点分隔符] 按钮文案里不要用「·」：{text}")

    # 同一行里并排两个以上文字按钮：按钮会挤成一团（用户原话「按钮粘腻」）。
    # 对话框底的「取消 + 主操作」是标准布局，不算堆叠——只看卡片内部的并排按钮。
    row_pattern = r"Row\s*(?:\([\s\S]{0,900}?\))?\s*\{([\s\S]{0,1600}?)\n\s*\}"
    for row in re.finditer(row_pattern, source):
        body = row.group(1)
        count = len(re.findall(r"TextButton\(", body))
        last_dialog = source.rfind("OverlayDialog(", 0, row.start())
        last_card = max(source.rfind("Card(", 0, row.start()), source.rfind("Card {", 0, row.start()))
        if last_dialog > last_card:
            continue
        if count >= 2:
            line = source.count("\n", 0, row.start()) + 1
            problems.append(
                f"{rel}:{line} [按钮堆叠] 一行里放了 {count} 个文字按钮，"
                "改成整块可点 + 图标操作")
    return problems


def check_instruction(text: str) -> str | None:
    hit = INSTRUCTION_WORDS.search(text)
    return hit.group(0) if hit else None


def run(quiet: bool = False) -> list[str]:
    problems: list[str] = []
    for folder in SCAN_DIRS:
        for path in sorted(folder.glob("*.py")):
            file_problems, _items = check_file(path)
            problems.extend(file_problems)
    for root in KT_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.kt")):
            problems.extend(check_kotlin(path))
    if not quiet:
        for line in problems:
            print(line)
        print(f"\n共 {len(problems)} 处" if problems else "\n没有发现问题")
    return problems


if __name__ == "__main__":
    sys.exit(1 if run(quiet="--quiet" in sys.argv) else 0)
